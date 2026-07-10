"""server.py — HTTP-сервер веб-интерфейса грейдера (issue #58, эпик #80 Tier 1).

Application/UI слой. Поднимает stdlib ``http.server`` на 127.0.0.1 (только
localhost, не торчит в сеть, **без новых зависимостей**). Статические файлы
(``static/index.html``/``app.css``/``app.js``) читаются один раз при импорте
модуля с диска (тот же паттерн, что ``core/i18n.py`` для локалей) — без
build-шага и без внешних зависимостей.

Threat model тот же, что у CLI: решения запускаются в subprocess без
OS-sandbox (см. ``core/executor.py``, CLAUDE.md). Сервер слушает только
127.0.0.1 — запускай для своих решений на своей машине.
"""

from __future__ import annotations

import html
import json
import os
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.web.commands import filter_commands
from stepik_grader.web.downloader_adapter import download_task
from stepik_grader.web.glossary_adapter import glossary_get, glossary_missing, glossary_search
from stepik_grader.web.viewmodels import (
    grade_benchmark,
    grade_microbench,
    grade_path,
    list_solutions,
    read_source,
    save_solution,
)

__all__ = ["run_server"]

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
_APP_CSS = (_STATIC_DIR / "app.css").read_text(encoding="utf-8")
_APP_JS = (_STATIC_DIR / "app.js").read_text(encoding="utf-8")

# Небольшой фиксированный allowlist — не файловый static-сервер (нет
# path-traversal поверхности): единственные статические файлы, которые вообще
# существуют в static/, уже загружены выше.
_STATIC_ROUTES: dict[str, tuple[str, str]] = {
    "/static/app.css": ("text/css; charset=utf-8", _APP_CSS),
    "/static/app.js": ("application/javascript; charset=utf-8", _APP_JS),
}

# issue #242 (F-03): the server only binds to loopback, but a page open in the
# user's browser can still reach it — a plain cross-site request (no CORS
# preflight for a simple GET) or DNS-rebinding (attacker domain briefly
# resolving to 127.0.0.1) would otherwise be enough to trigger grading/
# download/save actions. Host/Origin/Referer are all set by the browser
# itself and can't be forged by page JS, unlike the request body/query.
_ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")


class _Handler(BaseHTTPRequestHandler):
    """GET / → страница; GET /api/grade?path=…&mode=tests|bench|microbench → JSON.

    Плюс (issue #125): GET /api/glossary?q= (поиск карточек), GET
    /api/glossary/<id> (карточка или 404), GET /api/glossary/missing (очередь
    пополнения, J7) — тонкие адаптеры над ``glossary_adapter.py``; GET
    /api/commands?context=tag1,tag2 — реестр команд (``commands.py``),
    отфильтрованный по тегам контекста (пусто/нет параметра → весь реестр).
    Плюс (фикс режима 1, #125): GET /api/solutions?path= (список решений в
    папке — пикер режима «Один файл») и GET /api/source?path= (исходник
    файла для показа кода перед запуском).
    Плюс (issue #186): POST /api/download — тело JSON {"url","root"?} — раздел
    «Загрузчик задач», тонкий адаптер над ``downloader_adapter.download_task``.
    Плюс (доделка #125): POST /api/save-solution — тело JSON
    {"folder","path"?,"code"} — сохранить код из редактируемого окна на
    диск (в ``path``, если выбран, иначе — новый файл по маске в ``folder``)
    перед грейдингом в режиме 1.
    """

    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = _INDEX_HTML.replace("__DEFAULT_PATH__", html.escape(os.getcwd(), quote=True))
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif parsed.path in _STATIC_ROUTES:
            ctype, body = _STATIC_ROUTES[parsed.path]
            self._send(200, ctype, body.encode("utf-8"))
        elif parsed.path.startswith("/api/"):
            if self._guard_request():
                self._dispatch_api_get(parsed)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _dispatch_api_get(self, parsed: Any) -> None:
        """Диспетчеризация GET /api/* — вызывается только после `_guard_request()`."""
        if parsed.path == "/api/grade":
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0].strip()
            mode = (qs.get("mode") or ["tests"])[0]
            if not path:
                data: dict[str, Any] = {
                    "kind": "error",
                    "message": "Укажите путь к файлу или папке.",
                    "rows": [],
                }
            elif mode == "bench":
                reference = (qs.get("reference") or [""])[0].strip() or None
                data = grade_benchmark(
                    path, repeats=_int(qs.get("repeats"), 15), reference=reference
                )
            elif mode == "microbench":
                data = grade_microbench(path, number=_int(qs.get("number"), 1000))
            else:
                data = grade_path(path)
            self._send(200, "application/json; charset=utf-8", _json(data))
        elif parsed.path == "/api/glossary":
            qs = parse_qs(parsed.query)
            query = (qs.get("q") or [""])[0]
            self._send(200, "application/json; charset=utf-8", _json(glossary_search(query)))
        elif parsed.path == "/api/glossary/missing":
            self._send(200, "application/json; charset=utf-8", _json(glossary_missing()))
        elif parsed.path.startswith("/api/glossary/"):
            card_id = parsed.path[len("/api/glossary/") :]
            card = glossary_get(card_id)
            if card is None:
                self._send(
                    404,
                    "application/json; charset=utf-8",
                    _json({"kind": "error", "message": f"Карточка не найдена: {card_id}"}),
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
            data = (
                list_solutions(path)
                if path
                else {"kind": "error", "message": "Укажите путь к папке.", "files": []}
            )
            self._send(200, "application/json; charset=utf-8", _json(data))
        elif parsed.path == "/api/source":
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0].strip()
            data = (
                read_source(path) if path else {"kind": "error", "message": "Укажите путь к файлу."}
            )
            self._send(200, "application/json; charset=utf-8", _json(data))
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/download", "/api/save-solution"):
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        if not self._guard_request():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, "message": "Тело запроса должно быть валидным JSON."}),
            )
            return
        if not isinstance(body, dict):
            self._send(
                400,
                "application/json; charset=utf-8",
                _json({"ok": False, "message": "Тело запроса должно быть JSON-объектом."}),
            )
            return
        if parsed.path == "/api/download":
            url = str(body.get("url") or "").strip()
            if not url:
                self._send(
                    400,
                    "application/json; charset=utf-8",
                    _json({"ok": False, "message": "Укажите url."}),
                )
                return
            root = str(body.get("root") or "").strip() or None
            data = download_task(url, root=root)
        else:  # /api/save-solution
            folder = str(body.get("folder") or "").strip()
            if not folder:
                self._send(
                    400,
                    "application/json; charset=utf-8",
                    _json({"ok": False, "message": "Укажите папку."}),
                )
                return
            code = body.get("code")
            if not isinstance(code, str):
                self._send(
                    400,
                    "application/json; charset=utf-8",
                    _json({"ok": False, "message": "Укажите code (строка)."}),
                )
                return
            path = str(body.get("path") or "").strip() or None
            data = save_solution(folder, path, code)
        self._send(200, "application/json; charset=utf-8", _json(data))

    def _guard_request(self) -> bool:
        """Host/Origin/Referer-проверка для `/api/*` (issue #242, F-03).

        Отклоняет запрос 403-м, если он не прошёл. Host защищает от
        DNS-rebinding (домен, кратковременно резолвящийся в 127.0.0.1); Origin/
        Referer — от обычного cross-site запроса из браузера (для GET без CORS
        preflight никакой другой защиты нет). Оба заголовка при полном
        отсутствии считаются допустимыми: не-браузерные клиенты (curl, тесты)
        их не отправляют, а странице чужого происхождения их не подделать —
        браузер выставляет Origin/Referer сам.
        """
        if not self._host_header_is_allowed():
            self._send(
                403,
                "application/json; charset=utf-8",
                _json({"kind": "error", "message": "Недопустимый Host — запрос отклонён."}),
            )
            return False
        if not self._origin_is_allowed():
            message = "Недопустимый Origin/Referer — запрос отклонён."
            self._send(
                403,
                "application/json; charset=utf-8",
                _json({"kind": "error", "message": message}),
            )
            return False
        return True

    def _host_header_is_allowed(self) -> bool:
        host_header = (self.headers.get("Host") or "").strip().lower()
        hostname = host_header.split(":", 1)[0]
        return hostname in _ALLOWED_HOSTNAMES

    def _origin_is_allowed(self) -> bool:
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


def _int(values: list[str] | None, default: int) -> int:
    """Первое значение из query как int, иначе default (без падения)."""
    try:
        return int((values or [str(default)])[0])
    except (TypeError, ValueError):
        return default


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Запустить веб-интерфейс на http://host:port (Ctrl+C — остановить).

    Слушает только localhost. ``ThreadingHTTPServer`` — чтобы медленный
    грейдинг одного запроса не блокировал отдачу страницы другому.
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"🌐 Веб-интерфейс грейдера: {url}  (Ctrl+C — остановить)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
