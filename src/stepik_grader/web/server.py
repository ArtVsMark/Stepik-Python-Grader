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
OS-sandbox (см. ``core/executor.py``, CLAUDE.md). Сервер слушает только
127.0.0.1 — запускай для своих решений на своей машине.
"""

from __future__ import annotations

import html
import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.web import runs
from stepik_grader.web.commands import filter_commands
from stepik_grader.web.downloader_adapter import download_task
from stepik_grader.web.glossary_adapter import (
    code_terms,
    glossary_get,
    glossary_missing,
    glossary_search,
    queue_code_gaps,
)
from stepik_grader.web.i18n import DEFAULT_LANG, message_fields, render_message, resolve_lang
from stepik_grader.web.insights_adapter import insights_cards
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
_APP_JS = (_STATIC_DIR / "app.js").read_text(encoding="utf-8")

# issue #265 — вендоренный ESM-бандл CodeMirror 6 (без CDN, тот же принцип,
# что у шрифтов issue #260); один самодостаточный файл вместо importmap +
# набора esm.sh-бандлов + Node browser-compat шимов (issue #295 — единая
# сборка esbuild'ом; имена/версии/способ пересборки — static/vendor/VERSIONS.md).
# Content-Type text/javascript — браузер принимает его для ES-модулей.
_VENDOR_FILES = ("codemirror-bundle@6.mjs",)

# Небольшой фиксированный allowlist — не файловый static-сервер (нет
# path-traversal поверхности): единственные статические файлы, которые вообще
# существуют в static/, уже загружены выше. Шрифты (issue #260, вендоринг
# вместо Google Fonts CDN) — bytes, не str, поэтому отдельная map.
_STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/static/app.css": ("text/css; charset=utf-8", _APP_CSS),
    "/static/app.js": ("application/javascript; charset=utf-8", _APP_JS),
    **{
        f"/static/vendor/{name}": (
            "text/javascript; charset=utf-8",
            (_VENDOR_DIR / name).read_text(encoding="utf-8"),
        )
        for name in _VENDOR_FILES
    },
}
_STATIC_BINARY_ROUTES: dict[str, tuple[str, bytes]] = {
    f"/static/fonts/{name}": ("font/woff2", (_FONTS_DIR / name).read_bytes())
    for name in (
        "jetbrains-mono-latin.woff2",
        "jetbrains-mono-cyrillic.woff2",
        "inter-latin.woff2",
        "inter-cyrillic.woff2",
    )
}

# issue #242 (F-03): the server only binds to loopback, but a page open in the
# user's browser can still reach it — a plain cross-site request (no CORS
# preflight for a simple GET) or DNS-rebinding (attacker domain briefly
# resolving to 127.0.0.1) would otherwise be enough to trigger grading/
# download/save actions. Host/Origin/Referer are all set by the browser
# itself and can't be forged by page JS, unlike the request body/query.
_ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")

# issue #259 (A-2): API — не server-ready без лимитов на входные данные.
# Тело POST ограничено 1 MiB (413 при превышении); repeats/number из query
# кламп'аются в разумный диапазон вместо прохода как есть в исполнение.
_MAX_BODY_BYTES = 1024 * 1024
_REPEATS_RANGE = (1, 1000)
_NUMBER_RANGE = (1, 1_000_000)


class _GraderServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer``, несущий рабочую директорию сервера (issue #261).

    ``workspace`` и ``confine`` живут на сервере (не на классе ``_Handler``,
    инстанцируемом заново под каждый запрос) — единственное место, где их
    можно надёжно связать с конкретным запущенным сервером без глобального
    мутабельного состояния.
    """

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        workspace: pathlib.Path,
        confine: bool,
    ) -> None:
        self.workspace = workspace
        self.confine = confine
        super().__init__(server_address, handler_cls)


def _resolve_within_root(
    workspace: pathlib.Path, raw: str, *, confine: bool
) -> pathlib.Path | None:
    """Резолвит путь запроса относительно ``workspace`` (issue #261).

    Относительные пути резолвятся от ``workspace`` (не от cwd процесса —
    после этого issue ``workspace`` и есть концептуальный «корень» сервера).
    ``Path.resolve()`` разворачивает симлинки ДО проверки контейнмента —
    симлинк внутри ``workspace``, ведущий наружу, тоже ловится. Возвращает
    ``None`` (нарушение), если ``confine`` включён и результат не внутри
    ``workspace``; при ``confine=False`` — всегда резолвит без проверки
    (explicit opt-out, `--no-root-confinement`).
    """
    candidate = pathlib.Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    if confine and not resolved.is_relative_to(workspace):
        return None
    return resolved


class _Handler(BaseHTTPRequestHandler):
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

    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = _INDEX_HTML.replace(
                "__DEFAULT_PATH__", html.escape(str(self.server.workspace), quote=True)
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

    def _dispatch_api_get(self, parsed: Any, lang: str) -> None:
        """Диспетчеризация GET /api/* — вызывается только после `_guard_request()`.

        ``lang`` — локаль ``?lang=`` запроса (issue #264), уже разрешённая
        ``_lang_from_query()`` в ``do_GET`` — прокидывается дальше в
        ``viewmodels.py``/каталог сообщений для рендера ``message``.
        """
        if parsed.path.startswith("/api/v1/runs/"):
            run_id = parsed.path[len("/api/v1/runs/") :]
            if not run_id or "/" in run_id:
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            job = runs.get_job(run_id)
            if job is None:
                self._send(
                    404,
                    "application/json; charset=utf-8",
                    _json(
                        {"kind": "error", **message_fields("run_not_found", lang, run_id=run_id)}
                    ),
                )
                return
            self._send(200, "application/json; charset=utf-8", _json(job.to_status_dict()))
        elif parsed.path == "/api/grade":
            # DEPRECATED для bench/microbench (issue #262): синхронный —
            # держит HTTP-запрос открытым на всю длительность бенчмарка, без
            # прогресса и без отмены. POST /api/v1/runs + polling — асинхронная
            # замена (см. web/runs.py). Оставлен как тонкая sync-обёртка для
            # обратной совместимости и для режимов 1/2 (обычные тесты), которые
            # вне scope #262 — поведение не меняется, TODO(#267) docs/api.md.
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
                    data = grade_benchmark(path, repeats=repeats, reference=reference, lang=lang)
                elif mode == "microbench":
                    number = _clamp(_int(qs.get("number"), 1000), *_NUMBER_RANGE)
                    data = grade_microbench(path, number=number, lang=lang)
                else:
                    data = grade_path(path, lang=lang)
            self._send(200, "application/json; charset=utf-8", _json(data))
        elif parsed.path == "/api/glossary":
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
        elif parsed.path == "/api/glossary/missing":
            self._send(200, "application/json; charset=utf-8", _json(glossary_missing()))
        elif parsed.path.startswith("/api/glossary/"):
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
        elif parsed.path == "/api/rules":
            qs = parse_qs(parsed.query)
            cards = rules_search(
                (qs.get("q") or [""])[0],
                tag=(qs.get("tag") or [""])[0] or None,
            )
            self._send(200, "application/json; charset=utf-8", _json(cards))
        elif parsed.path == "/api/insights":
            self._send(200, "application/json; charset=utf-8", _json(insights_cards()))
        elif parsed.path.startswith("/api/rules/"):
            code = parsed.path[len("/api/rules/") :]
            card = rules_get(code)
            if card is None:
                self._send(
                    404,
                    "application/json; charset=utf-8",
                    _json(
                        {"kind": "error", **message_fields("rule_card_not_found", lang, code=code)}
                    ),
                )
            else:
                self._send(200, "application/json; charset=utf-8", _json(card))
        elif parsed.path == "/api/commands":
            qs = parse_qs(parsed.query)
            raw_context = (qs.get("context") or [""])[0]
            context = {tag for tag in raw_context.split(",") if tag} or None
            self._send(200, "application/json; charset=utf-8", _json(filter_commands(context)))
        elif parsed.path == "/api/solutions":
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0].strip()
            if not path:
                data = {"kind": "error", **message_fields("specify_path_folder", lang), "files": []}
            else:
                confined = self._confined_path(path, lang)
                if confined is None:
                    return
                data = list_solutions(confined, lang=lang)
            self._send(200, "application/json; charset=utf-8", _json(data))
        elif parsed.path == "/api/source":
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0].strip()
            if not path:
                data = {"kind": "error", **message_fields("specify_path_file", lang)}
            else:
                confined = self._confined_path(path, lang)
                if confined is None:
                    return
                data = read_source(confined, lang=lang)
            self._send(200, "application/json; charset=utf-8", _json(data))
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/runs":
            self._handle_create_run(parsed)
            return
        if parsed.path.startswith("/api/v1/runs/") and parsed.path.endswith("/cancel"):
            self._handle_cancel_run(parsed)
            return
        if parsed.path not in ("/api/download", "/api/save-solution", "/api/code-terms"):
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        lang = _lang_from_query(parsed)
        if not self._guard_request(lang):
            return
        body = self._read_json_body(lang)
        if body is None:
            return
        if parsed.path == "/api/code-terms":
            # issue #321/#322: мини-карточки глоссария по коду. Тело — либо
            # {code} (режим 1/песочница, debounce), либо {path} (режим 2, разово
            # после прогона): path конфайнится и читается, пробелы решения
            # дозаписываются в очередь «Недостающее» (practice-driven канал).
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
            return
        if parsed.path == "/api/download":
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
        else:  # /api/save-solution
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
            # issue #297: optimistic locking — фронтенд присылает mtime,
            # запомненный при загрузке файла; save_solution откажет с
            # conflict=True, если файл на диске с тех пор изменился. Нечисловое/
            # отсутствующее значение → None (проверка не применяется).
            raw_mtime = body.get("expected_mtime")
            expected_mtime = float(raw_mtime) if isinstance(raw_mtime, int | float) else None
            data = save_solution(
                confined_folder, target_path, code, lang=lang, expected_mtime=expected_mtime
            )
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _read_json_body(self, lang: str = DEFAULT_LANG) -> dict[str, Any] | None:
        """Читает и валидирует JSON-тело POST-запроса.

        Issue #259: лимит ``_MAX_BODY_BYTES`` на Content-Length. Issue #262:
        вынесено из ``do_POST`` в общий хелпер — было продублировано для
        ``/api/download``/``/api/save-solution``, теперь используется также
        ``POST /api/v1/runs``. Отправляет 400/413 и возвращает ``None`` при
        любой ошибке — тот же паттерн, что ``_confined_path`` (шли ответ
        внутри, сигнализируй отказ через ``None``, вызывающая сторона просто
        ``return``-ит).
        """
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("content_length_required", lang)}),
            )
            return None
        if length > _MAX_BODY_BYTES:
            # Осушаем входящий поток в разумных пределах: если оставить
            # непрочитанные байты клиента в буфере ядра и просто закрыть
            # сокет, ОС на некоторых платформах (Windows) шлёт RST вместо
            # штатного FIN — клиент получает ConnectionAbortedError вместо
            # тела 413-ответа, даже не успев его прочитать. Кламп на
            # 2×лимит — не безусловное чтение произвольного (attacker-
            # controlled) length, а лишь снятие типичного чуть-за-лимитом
            # хвоста.
            try:
                self.rfile.read(min(length, _MAX_BODY_BYTES * 2))
            except OSError:
                pass
            self._send(
                413,
                "application/json; charset=utf-8",
                _json(
                    {
                        "ok": False,
                        **message_fields("body_too_large", lang, limit=_MAX_BODY_BYTES),
                    }
                ),
            )
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("body_invalid_json", lang)}),
            )
            return None
        if not isinstance(body, dict):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, **message_fields("body_not_object", lang)}),
            )
            return None
        return body

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

        job = runs.submit_job(mode, confined, params, code=code)
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
        job = runs.submit_job(kind, None, {"lang": lang}, code=code, stdin=stdin)
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

    def _confined_path(self, raw: str, lang: str = DEFAULT_LANG) -> pathlib.Path | None:
        """Резолвит и конфайнит путь запроса в ``server.workspace`` (issue #261).

        Отправляет 403 и возвращает ``None``, если путь выходит за пределы
        рабочей директории (и конфайнмент включён); иначе — резолвленный
        абсолютный ``Path``, готовый передавать дальше в ``viewmodels.py``
        (которые остаются агностичны к политике конфайнмента). ``lang`` —
        локаль сообщения 403 (issue #264).
        """
        resolved = _resolve_within_root(self.server.workspace, raw, confine=self.server.confine)
        if resolved is None:
            self._send(
                403,
                "application/json; charset=utf-8",
                _json(
                    {
                        "kind": "error",
                        **message_fields("path_outside_workspace", lang, path=raw),
                    }
                ),
            )
            return None
        return resolved

    def _guard_request(self, lang: str = DEFAULT_LANG) -> bool:
        """Host/Origin/Referer-проверка для `/api/*` (issue #242, F-03).

        Отклоняет запрос 403-м, если он не прошёл. Host защищает от
        DNS-rebinding (домен, кратковременно резолвящийся в 127.0.0.1); Origin/
        Referer — от обычного cross-site запроса из браузера (для GET без CORS
        preflight никакой другой защиты нет). Оба заголовка при полном
        отсутствии считаются допустимыми: не-браузерные клиенты (curl, тесты)
        их не отправляют, а странице чужого происхождения их не подделать —
        браузер выставляет Origin/Referer сам; дополнительно ``Sec-Fetch-Site:
        cross-site`` отклоняется (Fetch Metadata, issue #399). ``lang`` — локаль
        сообщения 403 (issue #264).
        """
        if not self._host_header_is_allowed():
            self._send(
                403,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("invalid_host", lang)}),
            )
            return False
        if not self._origin_is_allowed():
            self._send(
                403,
                "application/json; charset=utf-8",
                _json({"kind": "error", **message_fields("invalid_origin", lang)}),
            )
            return False
        return True

    def _host_header_is_allowed(self) -> bool:
        host_header = (self.headers.get("Host") or "").strip().lower()
        hostname = host_header.split(":", 1)[0]
        return hostname in _ALLOWED_HOSTNAMES

    def _origin_is_allowed(self) -> bool:
        # issue #399: Fetch Metadata. Браузеры шлют Sec-Fetch-Site на каждый
        # запрос; ``cross-site`` — межсайтовый контекст (в т.ч. если атакующему
        # удалось убрать Origin/Referer) → отклоняем. Не-браузерные клиенты
        # (curl, тесты) заголовок не шлют — их поведение не меняется.
        if (self.headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
            return False
        value = self.headers.get("Origin") or self.headers.get("Referer")
        if not value:
            return True
        hostname = (urlparse(value).hostname or "").lower()
        return hostname in _ALLOWED_HOSTNAMES

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:  # noqa: N802
        """Приглушить пер-запросный лог в stdout (иначе шумно)."""


def _lang_from_query(parsed: Any) -> str:
    """Локаль запроса из ``?lang=`` (issue #264) — ``resolve_lang()`` даёт graceful
    fallback на ``DEFAULT_LANG`` (ru) для пустого/неизвестного/отсутствующего
    значения, так что дефолтное поведение (без ``?lang=``) не меняется."""
    qs = parse_qs(parsed.query)
    return resolve_lang((qs.get("lang") or [None])[0])


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


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


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
        from stepik_grader.core.grader_core import set_runner
        from stepik_grader.core.sandbox import SandboxRunner

        set_runner(SandboxRunner())
    workspace = root.expanduser().resolve() if root else pathlib.Path.cwd().resolve()
    server = _GraderServer((host, port), _Handler, workspace=workspace, confine=confine)
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
