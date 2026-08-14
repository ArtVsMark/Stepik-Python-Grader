"""server.py — HTTP-сервер веб-интерфейса грейдера (issue #58, эпик #80 Tier 1).

Application/UI слой. Поднимает stdlib ``http.server`` на 127.0.0.1 (только
localhost, не торчит в сеть, **без новых зависимостей**). Статические файлы
(``static/index.html``/``app.css``/``app.js``, шрифты ``static/fonts/*.woff2``,
ESM-бандлы редактора ``static/vendor/*.mjs``) читаются один раз при импорте
модуля с диска (тот же паттерн, что ``core/i18n.py`` для локалей) — без
build-шага и без внешних зависимостей. UI полностью офлайн (issue #260):
шрифты (JetBrains Mono/Inter, OFL 1.1, см. ``static/fonts/LICENSE``) и
CodeMirror 6 (issue #265, MIT, см. ``static/vendor/LICENSE``) вендорены
локально, страница не обращается ни к каким внешним доменам — ни для
рендера, ни для факта своего запуска.

Threat model тот же, что у CLI: решения запускаются в subprocess без
OS-sandbox (см. ``core/runner.py``, CLAUDE.md). Сервер слушает только
127.0.0.1 — запускай для своих решений на своей машине.
"""

from __future__ import annotations

import html
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from stepik_grader import config
from stepik_grader.core import user_settings
from stepik_grader.web import runs
from stepik_grader.web.api_routes import _ApiRoutesMixin
from stepik_grader.web.http_guards import (
    _lang_from_query,
)
from stepik_grader.web.i18n import DEFAULT_LANG, render_message, resolve_lang
from stepik_grader.web.viewmodels import (
    set_web_record_history,
)

__all__ = ["run_server"]

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_FONTS_DIR = _STATIC_DIR / "fonts"
_VENDOR_DIR = _STATIC_DIR / "vendor"
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
_APP_CSS = (_STATIC_DIR / "app.css").read_text(encoding="utf-8")
# issue #545 — JSON-каталог i18n статической оболочки (ru/en). Отдаётся как
# /static/locales/ui.json; ``applyUiLocale()`` в static/core.js применяет его к
# узлам data-i18n. Читается один раз при импорте — тот же паттерн, что _APP_CSS.
_UI_LOCALES = (_STATIC_DIR / "locales" / "ui.json").read_text(encoding="utf-8")

# issue #265 — вендоренный ESM-бандл CodeMirror 6 (без CDN, тот же принцип,
# что у шрифтов issue #260); один самодостаточный файл вместо importmap +
# набора esm.sh-бандлов + Node browser-compat шимов (issue #295 — единая
# сборка esbuild'ом; имена/версии/способ пересборки — static/vendor/VERSIONS.md).
# Content-Type text/javascript — браузер принимает его для ES-модулей.
_VENDOR_FILES = ("codemirror-bundle@6.mjs",)

# issue #426 — фиксированный allowlist текстовой статики, построенный СКАНОМ
# static/ ОДИН РАЗ при импорте. Это по-прежнему НЕ пофайловый сервер: запрос
# матчится по точному ключу словаря, per-request резолюции пути/чтения с диска
# нет — path-traversal поверхности нет, как и раньше. Скан снимает ручную
# регистрацию каждого static/*.js: app.js разбит на ES-модули (issue #426), и
# новый модуль подхватывается автоматически, без правки этого списка. Порядок
# ключей детерминирован (sorted). Шрифты (issue #260) — bytes, отдельная map ниже.
_STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/static/app.css": ("text/css; charset=utf-8", _APP_CSS),
    # issue #545 — JSON-каталог i18n оболочки (ru/en) для applyUiLocale().
    "/static/locales/ui.json": ("application/json; charset=utf-8", _UI_LOCALES),
    # static/*.js: app.js (entry) + извлечённые ES-модули (#426).
    **{
        f"/static/{js.name}": (
            "application/javascript; charset=utf-8",
            js.read_text(encoding="utf-8"),
        )
        for js in sorted(_STATIC_DIR.glob("*.js"))
    },
    # Вендоренный ESM-бандл CodeMirror 6 (issue #265/#295), text/javascript.
    **{
        f"/static/vendor/{name}": (
            "text/javascript; charset=utf-8",
            (_VENDOR_DIR / name).read_text(encoding="utf-8"),
        )
        for name in _VENDOR_FILES
    },
}

# Все ES-модули фронтенда одной строкой — для grep-регрессий инвариантов
# фронтенда в тестах (issue #426: после распила app.js паттерны живут в разных
# модулях; тест ищет по всему набору static/*.js, а не только по app.js).
_STATIC_JS_SOURCES: str = "\n".join(
    body for path, (_ctype, body) in _STATIC_ROUTES.items() if path.endswith(".js")
)
_STATIC_BINARY_ROUTES: dict[str, tuple[str, bytes]] = {
    f"/static/fonts/{name}": ("font/woff2", (_FONTS_DIR / name).read_bytes())
    for name in (
        "jetbrains-mono-latin.woff2",
        "jetbrains-mono-cyrillic.woff2",
        "inter-latin.woff2",
        "inter-cyrillic.woff2",
    )
}


class _GraderServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer``, несущий рабочую директорию сервера (issue #261).

    ``workspace`` и ``confine`` живут на сервере (не на классе ``_Handler``,
    инстанцируемом заново под каждый запрос) — единственное место, где их
    можно надёжно связать с конкретным запущенным сервером без глобального
    мутабельного состояния. Здесь же — флаги режима ``sandbox`` и
    ``record_history`` (issue #565): ``do_GET`` инжектит их в HTML, чтобы фронт
    показал баннер «есть/нет OS-изоляции» и статус локального сбора истории.
    """

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        workspace: pathlib.Path,
        confine: bool,
        sandbox: bool = False,
        record_history: bool = True,
        lang: str = "ru",
    ) -> None:
        self.workspace = workspace
        self.confine = confine
        self.sandbox = sandbox
        self.record_history = record_history
        # issue #1131: нормализуем здесь, а не у вызывающего, — тогда защищён
        # любой путь создания сервера, включая тесты и будущих потребителей.
        self.lang = resolve_lang(lang)
        super().__init__(server_address, handler_cls)


class _Handler(_ApiRoutesMixin):
    """HTTP-хендлер `--serve`: GET/POST на `/api/*` + статика.

    Полный справочник эндпоинтов, параметров, лимитов и кодов ответов — см.
    [docs/dev/api.md](../../../docs/dev/api.md) (issue #267); эта докстрока не
    дублирует его.

    Пути (issue #261): все пути из запросов (``/api/grade``, ``/api/source``,
    ``/api/solutions``, ``/api/save-solution``, ``/api/v1/runs``) конфайнятся
    в ``server.workspace`` (``--root``, по умолчанию — cwd на момент запуска
    ``--serve``) — выход за пределы отклоняется 403-м. Отключается явно
    (``--no-root-confinement``) — сознательный откат пользователя.
    ``/api/download`` (``root`` — куда СКАЧИВАТЬ задачу) с issue #401 тоже
    конфайнится в ``workspace``: произвольный ``root`` из тела запроса иначе
    создавал бы каталоги вне рабочей директории через ``mkdir``.
    """

    # Уточнение типа сервера (issue #261) — self.server на самом деле
    # _GraderServer, а не базовый socketserver.BaseServer из typeshed.
    server: _GraderServer  # type: ignore[assignment]

    # issue #563: read-timeout на соединение — медленный/зависший клиент
    # (slow-loris, не дочитавший запрос) не держит воркер-поток бесконечно.
    # ``socketserver`` ставит его на сокет в ``setup()``; блокирующие
    # read/write рвутся ``socket.timeout`` через 30с (ответы не стримятся, а
    # грейд считается в Python вне сокет-операций — долгий прогон не рвётся).
    timeout = 30

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            # issue #811 (SECW-06): страница несёт АБСОЛЮТНЫЙ путь рабочей
            # директории (а с ним имя пользователя ОС) и флаг наличия
            # OS-изоляции, поэтому Host-гард нужен и здесь. Прежде он стоял
            # только на /api/*: `curl -H 'Host: evil.example' /` отдавал 200 с
            # этими данными — при DNS-rebinding чужая страница узнавала их, не
            # трогая API. Проверяется только Host (не Origin/Referer): корневую
            # страницу открывают прямым переходом, где Origin отсутствует.
            if not self._host_header_is_allowed():
                self._send(403, "text/plain; charset=utf-8", b"invalid host")
                return
            # issue #565: инжектим флаги режима в data-атрибуты <body>, фронт
            # читает их для баннера OS-изоляции и статуса истории.
            # issue #660: онбординг показывается при первом заходе — флаг живёт в
            # .grader_settings.json (машинный факт «первый запуск на этой рабочей
            # директории»), читаем его тут и инжектим в data-onboarding-seen.
            onboarding_seen = (
                user_settings.load_settings(
                    # issue #948: путь резолвится общей функцией — иначе страница
                    # читала бы папочный файл, когда всё остальное уже перешло на
                    # общий ~/.stepik-grader/settings.json.
                    user_settings.default_settings_path(self.server.workspace)
                ).onboarding_seen
                is True
            )
            page = (
                _INDEX_HTML.replace(
                    "__DEFAULT_PATH__", html.escape(str(self.server.workspace), quote=True)
                )
                .replace("__EXEC_SANDBOX__", "true" if self.server.sandbox else "false")
                .replace("__RECORD_HISTORY__", "true" if self.server.record_history else "false")
                .replace("__ONBOARDING_SEEN__", "true" if onboarding_seen else "false")
                # issue #1131 (LNCH-2-05): стартовый язык страницы. Фронт брал
                # его только из localStorage с откатом на "ru", поэтому
                # `--serve --lang en` открывал английскому пользователю русский
                # интерфейс — единственный выбор языка, который он сделал явно,
                # игнорировался. localStorage остаётся сильнее: переключатель в
                # шапке — более поздний и более конкретный выбор того же
                # человека, и запуск с флагом не должен его отменять.
                .replace("__START_LANG__", html.escape(self.server.lang, quote=True))
            )
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif parsed.path in _STATIC_ROUTES:
            ctype, body = _STATIC_ROUTES[parsed.path]
            self._send(200, ctype, body.encode("utf-8"))
        elif parsed.path in _STATIC_BINARY_ROUTES:
            ctype, raw = _STATIC_BINARY_ROUTES[parsed.path]
            self._send(200, ctype, raw)
        elif parsed.path.startswith("/api/"):
            lang = _lang_from_query(parsed)
            if self._guard_request(lang):
                self._dispatch_api_get(parsed, lang)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:
        self._dispatch(self._API_POST_EXACT, self._API_POST_PREFIX, urlparse(self.path))


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    root: pathlib.Path | None = None,
    confine: bool = True,
    sandbox: bool = False,
    record_history: bool = True,
    lang: str = DEFAULT_LANG,
) -> None:
    """Запустить веб-интерфейс на http://host:port (Ctrl+C — остановить).

    Слушает только localhost. ``ThreadingHTTPServer`` — чтобы медленный
    грейдинг одного запроса не блокировал отдачу страницы другому.

    ``root`` — рабочая директория сервера (``--root``); ``None`` (по
    умолчанию) — cwd на момент запуска. ``confine`` (по умолчанию ``True``)
    — конфайнить ли пути запросов в неё; ``False`` (``--no-root-
    confinement``) — явный откат к прежнему поведению (доступ к любому
    пути на диске), issue #261.

    ``sandbox`` (issue #396) — включить OS-изоляцию исполнения кода. Ставит
    ``SandboxRunner`` активным ``grader_core._RUNNER`` ДО старта: все пути
    исполнения (grade/playground/microbench/trace) консультируют его, поэтому
    изолируются разом. ``SandboxRunner()`` бросает ``SandboxUnavailableError``,
    если backend недоступен (нет bwrap и т.п.) — пробрасываем вызывающему
    (CLI → ``parser.error``), никогда не откатываясь молча на ``LocalRunner``.

    ``record_history`` (issue #395, по умолчанию ``True`` для ``--serve``) —
    писать ли web-прогоны в локальную приватную ``.grader_history.db``, чтобы
    наполнялся раздел «Подучить». В отличие от CLI (opt-in ``--history``), для
    браузерной аудитории история включена по умолчанию; ``--no-history``
    выключает. Ставит оверрайд в ``viewmodels`` (``CONFIG`` — frozen).

    ``lang`` (issue #1131, находка LNCH-2-05) — язык, на котором открывается
    страница при первом заходе. Прежде фронт брал его только из
    ``localStorage`` с откатом на ``ru``, поэтому ``--serve --lang en``
    показывал английскому пользователю русский интерфейс: единственный явный
    выбор языка игнорировался. Неизвестное значение нормализуется к ``ru``
    (``resolve_lang``) — тот же graceful degradation, что и у ``?lang=``.
    """
    set_web_record_history(record_history)
    if sandbox:
        from stepik_grader.core.sandbox import SandboxRunner
        from stepik_grader.web.grading import set_runner

        set_runner(SandboxRunner())
    workspace = root.expanduser().resolve() if root else pathlib.Path.cwd().resolve()
    # issue #984: workspace сервера — он же корень настроек прогона. Прежде веб
    # якорил настройки на ``--root``, а конфиг читался от cwd: один запуск имел
    # два разных корня, и вердикт зависел от того, откуда стартовал сервер.
    config.set_workspace_root(workspace)
    server = _GraderServer(
        (host, port),
        _Handler,
        workspace=workspace,
        confine=confine,
        sandbox=sandbox,
        record_history=record_history,
        lang=lang,
    )
    url = f"http://{host}:{port}"
    # Консольный вывод оператора сервера (не JSON-ответ API) — тоже через
    # каталог сообщений (issue #264): вся кириллица в этом файле проходит
    # через message-catalog, локаль здесь всегда DEFAULT_LANG (ru), т.к. это
    # локальный вывод в терминал, не HTTP-ответ с ``?lang=``.
    print(render_message("server_running", url=url))
    if sandbox:
        print(render_message("server_sandbox_active"))
    if confine:
        print(render_message("server_workspace_confined", workspace=workspace))
    else:
        print(render_message("server_workspace_unconfined", workspace=workspace))
    if record_history:
        print(render_message("server_history_active"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(render_message("server_stopped"))
    finally:
        server.server_close()
        # issue #806: пул job'ов надо гасить явно. Его воркеры не daemon, и без
        # этого atexit-хук concurrent.futures join'ил их уже ПОСЛЕ «сервер
        # остановлен» — процесс молча висел до конца текущего прогона.
        runs.shutdown_jobs()
