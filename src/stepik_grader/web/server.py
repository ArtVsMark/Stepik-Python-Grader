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

from stepik_grader.web.glossary_adapter import glossary_get, glossary_missing, glossary_search
from stepik_grader.web.viewmodels import grade_benchmark, grade_path

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


class _Handler(BaseHTTPRequestHandler):
    """GET / → страница; GET /api/grade?path=…&mode=tests|bench → JSON.

    Плюс (issue #125): GET /api/glossary?q= (поиск карточек), GET
    /api/glossary/<id> (карточка или 404), GET /api/glossary/missing (очередь
    пополнения, J7) — тонкие адаптеры над ``glossary_adapter.py``.
    """

    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = _INDEX_HTML.replace("__DEFAULT_PATH__", html.escape(os.getcwd(), quote=True))
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif parsed.path in _STATIC_ROUTES:
            ctype, body = _STATIC_ROUTES[parsed.path]
            self._send(200, ctype, body.encode("utf-8"))
        elif parsed.path == "/api/grade":
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
                data = grade_benchmark(path, repeats=_int(qs.get("repeats"), 15))
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
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

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
