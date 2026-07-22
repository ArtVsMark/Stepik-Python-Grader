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

import contextlib
import html
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.core import user_settings
from stepik_grader.core.stepik_reference import DEFAULT_MAX_TOP
from stepik_grader.web import auth_adapter, runs
from stepik_grader.web.commands import filter_commands
from stepik_grader.web.downloader_adapter import download_task
from stepik_grader.web.glossary_adapter import (
    code_terms,
    glossary_get,
    glossary_missing,
    glossary_search,
    queue_code_gaps,
)
from stepik_grader.web.http_guards import (
    _GuardMixin,
    _json,
    _lang_from_query,
)
from stepik_grader.web.i18n import message_fields, render_message
from stepik_grader.web.insights_adapter import insights_cards, progress_report
from stepik_grader.web.reference_adapter import import_reference
from stepik_grader.web.rules_adapter import rules_get, rules_search
from stepik_grader.web.viewmodels import (
    grade_benchmark,
    grade_microbench,
    grade_path,
    list_solutions,
    read_source,
    save_solution,
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


# issue #259 (A-2): API — не server-ready без лимитов на входные данные.
# Тело POST ограничено 1 MiB (413 при превышении); repeats/number из query
# кламп'аются в разумный диапазон вместо прохода как есть в исполнение.
_REPEATS_RANGE = (1, 1000)
_NUMBER_RANGE = (1, 1_000_000)
# issue #641: границы per-run лимитов из тела POST /api/v1/runs
# (``limits: {timeout_s, memory_mb}``) — override серверных дефолтов в разумных
# пределах. Верх memory совпадает с дефолтом ``CONFIG.max_memory_mb`` (1024): запрос
# не поднимает потолок выше серверного максимума; timeout — до 60 с (6× дефолтных 10).
_TIMEOUT_S_RANGE = (1.0, 60.0)
_MEMORY_MB_RANGE = (16, 1024)


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
    ) -> None:
        self.workspace = workspace
        self.confine = confine
        self.sandbox = sandbox
        self.record_history = record_history
        super().__init__(server_address, handler_cls)


class _Handler(_GuardMixin):
    """HTTP-хендлер `--serve`: GET/POST на `/api/*` + статика.

    Полный справочник эндпоинтов, параметров, лимитов и кодов ответов — см.
    [docs/api.md](../../../docs/api.md) (issue #267); эта докстрока не
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
            # issue #565: инжектим флаги режима в data-атрибуты <body>, фронт
            # читает их для баннера OS-изоляции и статуса истории.
            page = (
                _INDEX_HTML.replace(
                    "__DEFAULT_PATH__", html.escape(str(self.server.workspace), quote=True)
                )
                .replace("__EXEC_SANDBOX__", "true" if self.server.sandbox else "false")
                .replace("__RECORD_HISTORY__", "true" if self.server.record_history else "false")
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

    # -- Реестр маршрутов (issue #427) — декларативная таблица path→handler-метод
    # вместо растущих if/elif в do_GET/do_POST. Точные пути ищутся в dict (O(1)),
    # затем префиксы по порядку (первое совпадение), суффикс опционален (POST
    # /cancel). Один диспетчер `_dispatch` на оба метода; HTTP-контракт не
    # меняется (docs/api.md). Новый эндпоинт = запись в таблице + метод-хендлер.
    _API_GET_EXACT = {
        "/api/grade": "_get_grade",
        "/api/glossary": "_get_glossary",
        "/api/glossary/missing": "_get_glossary_missing",
        "/api/rules": "_get_rules",
        "/api/insights": "_get_insights",
        "/api/progress": "_get_progress",
        "/api/commands": "_get_commands",
        "/api/solutions": "_get_solutions",
        "/api/source": "_get_source",
        "/api/auth/status": "_get_auth_status",
    }
    _API_GET_PREFIX = (
        ("/api/v1/runs/", "_get_run_status"),
        ("/api/glossary/", "_get_glossary_card"),
        ("/api/rules/", "_get_rule_card"),
    )
    _API_POST_EXACT = {
        "/api/v1/runs": "_handle_create_run",
        "/api/v1/hint": "_handle_create_hint",
        "/api/auth/start": "_handle_auth_start",
        "/api/code-terms": "_post_code_terms",
        "/api/download": "_post_download",
        "/api/import-reference": "_post_import_reference",
        "/api/save-solution": "_post_save_solution",
    }
    _API_POST_PREFIX = (("/api/v1/runs/", "_handle_cancel_run", "/cancel"),)

    def _dispatch(
        self,
        exact: dict[str, str],
        prefixes: tuple[tuple[str, ...], ...],
        parsed: Any,
        *args: Any,
    ) -> None:
        """Единый диспетчер (issue #427): точное совпадение → префикс(+суффикс) →
        404. Хендлер — имя метода, вызывается с ``(parsed, *args)`` (GET
        прокидывает ``lang``, POST — нет)."""
        handler = exact.get(parsed.path)
        if handler is None:
            for entry in prefixes:
                prefix, name = entry[0], entry[1]
                suffix = entry[2] if len(entry) > 2 else ""
                if parsed.path.startswith(prefix) and (not suffix or parsed.path.endswith(suffix)):
                    handler = name
                    break
        if handler is None:
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        getattr(self, handler)(parsed, *args)

    def _dispatch_api_get(self, parsed: Any, lang: str) -> None:
        """Диспетчеризация GET /api/* — вызывается только после `_guard_request()`.

        ``lang`` — локаль ``?lang=`` запроса (issue #264), уже разрешённая
        ``_lang_from_query()`` в ``do_GET`` — прокидывается в хендлеры и дальше
        в ``viewmodels.py``/каталог сообщений для рендера ``message``.
        """
        self._dispatch(self._API_GET_EXACT, self._API_GET_PREFIX, parsed, lang)

    def _get_run_status(self, parsed: Any, lang: str) -> None:
        run_id = parsed.path[len("/api/v1/runs/") :]
        if not run_id or "/" in run_id:
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        job = runs.get_job(run_id)
        if job is None:
            self._send(
                404,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("run_not_found", lang, run_id=run_id)}),
            )
            return
        self._send(200, "application/json; charset=utf-8", _json(job.to_status_dict()))

    def _get_grade(self, parsed: Any, lang: str) -> None:
        # DEPRECATED для bench/microbench (issue #262): синхронный — держит
        # HTTP-запрос открытым на всю длительность бенчмарка, без прогресса и
        # без отмены. POST /api/v1/runs + polling — асинхронная замена (см.
        # web/runs.py). Оставлен как тонкая sync-обёртка для обратной
        # совместимости и для режимов 1/2 (обычные тесты), вне scope #262 —
        # поведение не меняется, TODO(#267) docs/api.md.
        qs = parse_qs(parsed.query)
        path = (qs.get("path") or [""])[0].strip()
        mode = (qs.get("mode") or ["tests"])[0]
        if not path:
            data: dict[str, Any] = {
                "kind": "error",
                **message_fields("specify_path_file_or_folder", lang),
                "rows": [],
            }
        else:
            confined = self._confined_path(path, lang)
            if confined is None:
                return
            path = confined
            if mode == "bench":
                reference = (qs.get("reference") or [""])[0].strip() or None
                repeats = _clamp(_int(qs.get("repeats"), 15), *_REPEATS_RANGE)
                data = grade_benchmark(
                    path,
                    repeats=repeats,
                    reference=reference,
                    lang=lang,
                    workspace=self.server.workspace,
                )
            elif mode == "microbench":
                number = _clamp(_int(qs.get("number"), 1000), *_NUMBER_RANGE)
                data = grade_microbench(
                    path, number=number, lang=lang, workspace=self.server.workspace
                )
            else:
                data = grade_path(path, lang=lang, workspace=self.server.workspace)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _get_glossary(self, parsed: Any, lang: str) -> None:
        qs = parse_qs(parsed.query)
        query = (qs.get("q") or [""])[0]
        # Опциональные грани фильтра/сортировки (issue #329); пустые → None.
        cards = glossary_search(
            query,
            section=(qs.get("section") or [""])[0] or None,
            kind=(qs.get("kind") or [""])[0] or None,
            status=(qs.get("status") or [""])[0] or None,
            sort=(qs.get("sort") or [""])[0] or None,
            lang=lang,
        )
        self._send(200, "application/json; charset=utf-8", _json(cards))

    def _get_glossary_missing(self, parsed: Any, lang: str) -> None:
        self._send(200, "application/json; charset=utf-8", _json(glossary_missing()))

    def _get_glossary_card(self, parsed: Any, lang: str) -> None:
        card_id = parsed.path[len("/api/glossary/") :]
        card = glossary_get(card_id, lang=lang)
        if card is None:
            self._send(
                404,
                "application/json; charset=utf-8",
                _json(
                    {
                        "kind": "error",
                        **message_fields("glossary_card_not_found", lang, card_id=card_id),
                    }
                ),
            )
        else:
            self._send(200, "application/json; charset=utf-8", _json(card))

    def _get_rules(self, parsed: Any, lang: str) -> None:
        qs = parse_qs(parsed.query)
        cards = rules_search(
            (qs.get("q") or [""])[0],
            tag=(qs.get("tag") or [""])[0] or None,
        )
        self._send(200, "application/json; charset=utf-8", _json(cards))

    def _get_insights(self, parsed: Any, lang: str) -> None:
        self._send(200, "application/json; charset=utf-8", _json(insights_cards()))

    def _get_progress(self, parsed: Any, lang: str) -> None:
        # issue #538: агрегатный отчёт прогресса (KPI solved/total, вердикты,
        # TTFG по задачам в ``tasks``) — тот же движок, что CLI --export-progress.
        self._send(200, "application/json; charset=utf-8", _json(progress_report()))

    def _get_rule_card(self, parsed: Any, lang: str) -> None:
        code = parsed.path[len("/api/rules/") :]
        card = rules_get(code)
        if card is None:
            self._send(
                404,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("rule_card_not_found", lang, code=code)}),
            )
        else:
            self._send(200, "application/json; charset=utf-8", _json(card))

    def _get_commands(self, parsed: Any, lang: str) -> None:
        qs = parse_qs(parsed.query)
        raw_context = (qs.get("context") or [""])[0]
        context = {tag for tag in raw_context.split(",") if tag} or None
        self._send(200, "application/json; charset=utf-8", _json(filter_commands(context)))

    def _get_solutions(self, parsed: Any, lang: str) -> None:
        qs = parse_qs(parsed.query)
        path = (qs.get("path") or [""])[0].strip()
        if not path:
            data: dict[str, Any] = {
                "kind": "error",
                **message_fields("specify_path_folder", lang),
                "files": [],
            }
        else:
            confined = self._confined_path(path, lang)
            if confined is None:
                return
            data = list_solutions(confined, lang=lang)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _get_source(self, parsed: Any, lang: str) -> None:
        qs = parse_qs(parsed.query)
        path = (qs.get("path") or [""])[0].strip()
        if not path:
            data: dict[str, Any] = {"kind": "error", **message_fields("specify_path_file", lang)}
        else:
            confined = self._confined_path(path, lang)
            if confined is None:
                return
            data = read_source(confined, lang=lang)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _get_auth_status(self, parsed: Any, lang: str) -> None:
        # issue #402: валиден ли токен Stepik по secrets.json в workspace.
        secrets_path = self.server.workspace / "secrets.json"
        self._send(
            200,
            "application/json; charset=utf-8",
            _json(auth_adapter.auth_status(secrets_path)),
        )

    def do_POST(self) -> None:
        self._dispatch(self._API_POST_EXACT, self._API_POST_PREFIX, urlparse(self.path))

    def _post_code_terms(self, parsed: Any) -> None:
        # issue #321/#322: мини-карточки глоссария по коду. Тело — либо {code}
        # (режим 1/песочница, debounce), либо {path} (режим 2, разово после
        # прогона): path конфайнится и читается, пробелы решения дозаписываются
        # в очередь «Недостающее» (practice-driven канал).
        res = self._guard_and_read_body(parsed)
        if res is None:
            return
        lang, body = res
        terms_path = str(body.get("path") or "").strip()
        if terms_path:
            confined = self._confined_path(terms_path, lang)
            if confined is None:
                return  # _confined_path уже отправил ошибку
            try:
                terms_code = confined.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # issue #423: не-UTF8 файл не должен ронять /api/code-terms —
                # best-effort детект пробелов на пустом коде.
                terms_code = ""
            queue_code_gaps(terms_code, source=confined.name)
        else:
            raw_code = body.get("code")
            terms_code = raw_code if isinstance(raw_code, str) else ""
        self._send(
            200,
            "application/json; charset=utf-8",
            _json({"terms": code_terms(terms_code, lang=lang)}),
        )

    def _post_download(self, parsed: Any) -> None:
        res = self._guard_and_read_body(parsed)
        if res is None:
            return
        lang, body = res
        url = str(body.get("url") or "").strip()
        if not url:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("specify_url", lang)}),
            )
            return
        # issue #401: root (куда скачивать) из тела запроса — конфайнить в
        # workspace, иначе download_task через mkdir создаёт произвольные
        # каталоги вне рабочей директории.
        raw_root = str(body.get("root") or "").strip()
        if raw_root:
            confined = self._confined_path(raw_root, lang)
            if confined is None:
                return  # _confined_path уже отправил 403
            root: str | None = str(confined)
        else:
            root = None
        data = download_task(url, root=root)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _post_import_reference(self, parsed: Any) -> None:
        # issue #55: закреплённое решение Stepik → task{N}_{100+}.py в папке
        # задачи. path конфайнится в workspace (как download's root).
        res = self._guard_and_read_body(parsed)
        if res is None:
            return
        lang, body = res
        raw_folder = str(body.get("path") or "").strip()
        if not raw_folder:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("specify_folder", lang)}),
            )
            return
        confined_dir = self._confined_path(raw_folder, lang)
        if confined_dir is None:
            return  # _confined_path уже отправил 403
        raw_top = body.get("top")
        top = raw_top if isinstance(raw_top, int) and raw_top > 0 else DEFAULT_MAX_TOP
        data = import_reference(str(confined_dir), top=top)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _post_save_solution(self, parsed: Any) -> None:
        res = self._guard_and_read_body(parsed)
        if res is None:
            return
        lang, body = res
        raw_folder = str(body.get("folder") or "").strip()
        if not raw_folder:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("specify_folder", lang)}),
            )
            return
        confined_folder = self._confined_path(raw_folder, lang)
        if confined_folder is None:
            return
        code = body.get("code")
        if not isinstance(code, str):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("specify_code", lang)}),
            )
            return
        raw_path = str(body.get("path") or "").strip() or None
        target_path: pathlib.Path | None = None
        if raw_path:
            confined_target = self._confined_path(raw_path, lang)
            if confined_target is None:
                return
            target_path = confined_target
        # issue #297: optimistic locking — фронтенд присылает mtime, запомненный
        # при загрузке файла; save_solution откажет с conflict=True, если файл на
        # диске с тех пор изменился. Нечисловое/отсутствующее значение → None
        # (проверка не применяется).
        raw_mtime = body.get("expected_mtime")
        expected_mtime = float(raw_mtime) if isinstance(raw_mtime, int | float) else None
        data = save_solution(
            confined_folder, target_path, code, lang=lang, expected_mtime=expected_mtime
        )
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _submit_or_429(
        self,
        lang: str,
        kind: str,
        path: pathlib.Path | None,
        params: dict[str, Any],
        *,
        code: str | None = None,
        stdin: str | None = None,
    ) -> runs.Job | None:
        """``runs.submit_job`` с back-pressure (issue #429): при превышении
        лимита активных job'ов шлёт ``429`` с ``too_many_runs`` и возвращает
        ``None`` (паттерн «ответ внутри, отказ через None», как
        ``_confined_path``/``_read_json_body``); иначе — созданная ``Job``."""
        try:
            return runs.submit_job(
                kind, path, params, code=code, stdin=stdin, workspace=self.server.workspace
            )
        except runs.TooManyRunsError as exc:
            self._send(
                429,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("too_many_runs", lang, limit=exc.limit)}),
            )
            return None

    def _handle_create_run(self, parsed: Any) -> None:
        """POST /api/v1/runs (issue #262/#297) — тело ``{"path","code"?,"mode",
        "params"?}`` → ``202`` + ``{"run_id","status"}``. Асинхронная
        альтернатива ``/api/grade`` для tests (корректность режима 1, issue
        #297) / bench / microbench — ставит job в очередь (``web/runs.py``) и
        сразу возвращает, не дожидаясь завершения; прогресс/результат — через
        ``GET /api/v1/runs/{id}``. С ``code`` в теле (режим 1) грейд идёт из
        временного файла, целевой файл не перезаписывается.
        """
        lang = _lang_from_query(parsed)
        if not self._guard_request(lang):
            return
        body = self._read_json_body(lang)
        if body is None:
            return

        mode = str(body.get("mode") or "").strip()
        # issue #317/#318: песочница — code+stdin без path и без тестов
        # (playground — запуск, trace — пошаговый трейс исполнения).
        if mode in ("playground", "trace"):
            self._handle_code_run(body, lang, mode)
            return

        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("specify_path_file_or_folder", lang)}),
            )
            return
        if mode not in ("tests", "bench", "microbench"):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("invalid_run_mode", lang, mode=mode)}),
            )
            return
        confined = self._confined_path(raw_path, lang)
        if confined is None:
            return

        raw_code = body.get("code")
        code = raw_code if isinstance(raw_code, str) and raw_code else None

        raw_params = body.get("params")
        params_in = raw_params if isinstance(raw_params, dict) else {}
        params: dict[str, Any] = {"lang": lang}
        if mode == "bench":
            params["repeats"] = _clamp(_to_int(params_in.get("repeats"), 15), *_REPEATS_RANGE)
            reference = str(params_in.get("reference") or "").strip() or None
            params["reference"] = reference
        elif mode == "microbench":
            params["number"] = _clamp(_to_int(params_in.get("number"), 1000), *_NUMBER_RANGE)
        # mode == "tests" (issue #297): корректность режима 1, никаких
        # числовых params (кроме lang) — только code в теле.

        # issue #641: опц. per-run лимиты {timeout_s, memory_mb} — override дефолтов
        # сервера в границах диапазонов. Пусто → grade-слой берёт дефолт из CONFIG.
        # microbench их не потребляет (run_microbench_mode держит серверные дефолты).
        params.update(_parse_run_limits(body))

        job = self._submit_or_429(lang, mode, confined, params, code=code)
        if job is None:
            return
        self._send(
            202,
            "application/json; charset=utf-8",
            _json({"run_id": job.id, "status": job.status}),
        )

    def _handle_code_run(self, body: dict[str, Any], lang: str, kind: str) -> None:
        """POST /api/v1/runs с ``mode="playground"``/``"trace"`` (issue #317/#318)
        — тело ``{"code","stdin"?}`` → ``202`` + ``{"run_id","status"}``. Без
        ``path`` и без тестов: ``playground`` — одиночный запуск кода со stdin,
        ``trace`` — пошаговый трейс исполнения (оба через async-очередь ради
        отмены/неблокирующего UI). Лимит тела (#259) и localhost/Origin guard
        (#242) уже применены вызывающим кодом."""
        raw_code = body.get("code")
        code = raw_code if isinstance(raw_code, str) else ""
        if not code.strip():
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("specify_code", lang)}),
            )
            return
        raw_stdin = body.get("stdin")
        stdin = raw_stdin if isinstance(raw_stdin, str) else ""
        job = self._submit_or_429(lang, kind, None, {"lang": lang}, code=code, stdin=stdin)
        if job is None:
            return
        self._send(
            202,
            "application/json; charset=utf-8",
            _json({"run_id": job.id, "status": job.status}),
        )

    def _handle_create_hint(self, parsed: Any) -> None:
        """POST /api/v1/hint (issue #543) — AI-объяснение упавшего кейса как async-job.

        Тело: ``{"verdict","stdin"?,"expected"?,"actual"?,"diff"?,"error"?,
        "path"?|"code"?,"consent"?}`` → ``202`` + ``{"run_id","status"}``; результат
        (``{"hint": str|None, "configured": bool}``) — через ``GET /api/v1/runs/{id}``.

        Приватность (в т.ч. несовершеннолетних): подсказка отправляет код и ввод-вывод
        AI-провайдеру, поэтому нужно ОБЯЗАТЕЛЬНОЕ однократное явное согласие. Без
        согласия — ``403 consent_required``, в сеть НИЧЕГО не уходит (job не ставится,
        провайдер не вызывается). ``consent: true`` в теле фиксирует согласие в
        ``.grader_settings.json`` (``ai_hint_consent``) — далее не требуется. Провайдер
        не настроен → job вернёт ``hint=null`` (graceful, грейдинг не затрагивается).
        """
        res = self._guard_and_read_body(parsed)
        if res is None:
            return
        lang, body = res

        # Consent-гейт — синхронно, ДО любого обращения к провайдеру (приватность).
        settings_path = self.server.workspace / user_settings.SETTINGS_FILE_NAME
        settings = user_settings.load_settings(settings_path)
        granted = settings.ai_hint_consent is True
        if body.get("consent") is True and not granted:
            settings.ai_hint_consent = True
            with contextlib.suppress(OSError):
                user_settings.save_settings(settings, settings_path)
            granted = True
        if not granted:
            self._send(
                403,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("consent_required", lang)}),
            )
            return

        # Код решения для заземления промпта: из confined-пути (грейд по файлу) или
        # из тела (playground/инлайн). Отсутствие — не ошибка (промпт без кода).
        code = ""
        raw_path = str(body.get("path") or "").strip()
        if raw_path:
            confined = self._confined_path(raw_path, lang)
            if confined is None:
                return
            with contextlib.suppress(OSError):
                code = confined.read_text(encoding="utf-8")
        else:
            raw_code = body.get("code")
            if isinstance(raw_code, str):
                code = raw_code

        # web зовёт stdout кейса "actual"; нормализуем в CaseResult-форму для
        # общего core-хелпера build_failure_context (issue #542, str|list толерантен).
        case: dict[str, Any] = {
            "verdict": str(body.get("verdict") or ""),
            "stdin": str(body.get("stdin") or ""),
            "expected": str(body.get("expected") or ""),
            "output": str(body.get("actual") or ""),
            "diff": str(body.get("diff") or ""),
            "error": str(body.get("error") or ""),
        }
        job = self._submit_or_429(lang, "hint", None, {"lang": lang, "case": case}, code=code)
        if job is None:
            return
        self._send(
            202,
            "application/json; charset=utf-8",
            _json({"run_id": job.id, "status": job.status}),
        )

    def _handle_auth_start(self, parsed: Any) -> None:
        """POST /api/auth/start (issue #402) — тело ``{client_id, client_secret,
        redirect_uri?}`` → ``202`` + ``{run_id, status}``. Записывает креды в
        ``secrets.json`` (workspace) и запускает браузерный OAuth-flow как
        async-job (``kind="auth"``); опрос — через ``GET /api/v1/runs/{id}``.

        ``webbrowser.open`` открывается на МАШИНЕ СЕРВЕРА — корректно только для
        локального ``--serve`` (localhost, single-user). Под ``_guard_request``
        (Host/Origin/Fetch-Metadata, #242/#399) — сторонняя страница не
        инициирует OAuth-flow.
        """
        lang = _lang_from_query(parsed)
        if not self._guard_request(lang):
            return
        body = self._read_json_body(lang)
        if body is None:
            return
        client_id = str(body.get("client_id") or "").strip()
        client_secret = str(body.get("client_secret") or "").strip()
        redirect_uri = (
            str(body.get("redirect_uri") or "").strip() or auth_adapter.DEFAULT_REDIRECT_URI
        )
        if not client_id or not client_secret:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("specify_oauth_creds", lang)}),
            )
            return
        # issue #402 (harden): callback-сервер (wait_for_auth_code) биндится на
        # host из redirect_uri. Принимаем только loopback — иначе тело POST могло
        # бы забиндить callback на все интерфейсы (0.0.0.0) на ~120с.
        redirect_host = (urlparse(redirect_uri).hostname or "").lower()
        if redirect_host not in ("localhost", "127.0.0.1", "::1"):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("invalid_redirect_uri", lang)}),
            )
            return
        secrets_path = self.server.workspace / "secrets.json"
        params: dict[str, Any] = {
            "lang": lang,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "secrets_path": str(secrets_path),
        }
        job = self._submit_or_429(lang, "auth", None, params)
        if job is None:
            return
        self._send(
            202,
            "application/json; charset=utf-8",
            _json({"run_id": job.id, "status": job.status}),
        )

    def _handle_cancel_run(self, parsed: Any) -> None:
        """POST /api/v1/runs/{id}/cancel (issue #262) — best-effort отмена."""
        lang = _lang_from_query(parsed)
        if not self._guard_request(lang):
            return
        run_id = parsed.path[len("/api/v1/runs/") : -len("/cancel")]
        job = runs.get_job(run_id)
        if job is None:
            self._send(
                404,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("run_not_found", lang, run_id=run_id)}),
            )
            return
        runs.cancel_job(run_id)
        self._send(200, "application/json; charset=utf-8", _json(job.to_status_dict()))


def _int(values: list[str] | None, default: int) -> int:
    """Первое значение из query как int, иначе default (без падения)."""
    try:
        return int((values or [str(default)])[0])
    except (TypeError, ValueError):
        return default


def _clamp(value: int, lo: int, hi: int) -> int:
    """Ограничивает значение диапазоном [lo, hi] (issue #259 — защита от
    неограниченных `repeats`/`number` в API)."""
    return max(lo, min(hi, value))


def _to_int(value: Any, default: int) -> int:
    """Значение из JSON-тела (не query-string-списка, в отличие от ``_int``)
    как int, иначе default (issue #262 — `POST /api/v1/runs` params)."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_run_limits(body: dict[str, Any]) -> dict[str, float | int]:
    """Per-run лимиты ``{timeout_s?, memory_mb?}`` из тела POST /api/v1/runs (issue #641).

    Возвращает только числовые заданные ключи, зажатые в серверные границы
    (``_TIMEOUT_S_RANGE``/``_MEMORY_MB_RANGE``). Отсутствие блока ``limits`` или
    нечисловой мусор (строка/``null``/``bool``) → ключ не добавляется, и grade-слой
    берёт дефолт из ``CONFIG``. Override не поднимает потолок выше серверного
    максимума (верх диапазонов).
    """
    raw = body.get("limits")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float | int] = {}
    timeout_s = raw.get("timeout_s")
    if isinstance(timeout_s, int | float) and not isinstance(timeout_s, bool):
        lo, hi = _TIMEOUT_S_RANGE
        out["timeout_s"] = max(lo, min(hi, float(timeout_s)))
    memory_mb = raw.get("memory_mb")
    if isinstance(memory_mb, int | float) and not isinstance(memory_mb, bool):
        out["memory_mb"] = _clamp(int(memory_mb), *_MEMORY_MB_RANGE)
    return out


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    root: pathlib.Path | None = None,
    confine: bool = True,
    sandbox: bool = False,
    record_history: bool = True,
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
    """
    set_web_record_history(record_history)
    if sandbox:
        from stepik_grader.core.sandbox import SandboxRunner
        from stepik_grader.web.grading import set_runner

        set_runner(SandboxRunner())
    workspace = root.expanduser().resolve() if root else pathlib.Path.cwd().resolve()
    server = _GraderServer(
        (host, port),
        _Handler,
        workspace=workspace,
        confine=confine,
        sandbox=sandbox,
        record_history=record_history,
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
