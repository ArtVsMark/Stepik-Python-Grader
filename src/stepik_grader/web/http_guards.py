"""http_guards.py — периметр безопасности и сериализации HTTP-хендлера.

Извлечён из монолитного ``server._Handler`` (issue #647, DEV-01): host/Origin/
Referer-guard (#242/#399/#631), конфайнмент путей в ``workspace`` (#261),
чтение/лимит тела запроса (#259) и единый ``_send`` с security-заголовками
(CSP/nosniff/X-Frame-Options). Отдельный модуль делает границу безопасности
маленькой и проверяемой независимо от бизнес-хендлеров — именно она станет
публичным периметром при серверном пивоте. ``_Handler`` наследует ``_GuardMixin``;
сам миксин как HTTP-хендлер не регистрируется.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.web.i18n import DEFAULT_LANG, message_fields, resolve_lang

if TYPE_CHECKING:
    from stepik_grader.web.server import _GraderServer

__all__ = [
    "_ALLOWED_HOSTNAMES",
    "_MAX_BODY_BYTES",
    "_GuardMixin",
    "_json",
    "_lang_from_query",
    "_resolve_within_root",
]

# issue #242 (F-03): the server only binds to loopback, but a page open in the
# user's browser can still reach it — a plain cross-site request (no CORS
# preflight for a simple GET) or DNS-rebinding (attacker domain briefly
# resolving to 127.0.0.1) would otherwise be enough to trigger grading/
# download/save actions. Host/Origin/Referer are all set by the browser
# itself and can't be forged by page JS, unlike the request body/query.
_ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")
# issue #259 (A-2): тело POST ограничено 1 MiB (413 при превышении) — API
# не server-ready без лимита на входные данные (repeats/number кламп'ы — в server.py).
_MAX_BODY_BYTES = 1024 * 1024


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


def _lang_from_query(parsed: Any) -> str:
    """Локаль запроса из ``?lang=`` (issue #264) — ``resolve_lang()`` даёт graceful
    fallback на ``DEFAULT_LANG`` (ru) для пустого/неизвестного/отсутствующего
    значения, так что дефолтное поведение (без ``?lang=``) не меняется."""
    qs = parse_qs(parsed.query)
    return resolve_lang((qs.get("lang") or [None])[0])


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class _GuardMixin(BaseHTTPRequestHandler):
    """Периметр безопасности и сериализации HTTP-хендлера (issue #647, DEV-01).

    Host/Origin/Referer-guard, конфайнмент путей в ``server.workspace``, чтение/
    лимит тела и единый ``_send`` с security-заголовками. Наследуется ``_Handler``'ом;
    держит только границу безопасности, без бизнес-логики эндпоинтов.
    """

    # Уточнение типа сервера (issue #261) — self.server на самом деле _GraderServer.
    server: _GraderServer  # type: ignore[assignment]

    # issue #563: CSP на HTML. Скрипты строго 'self' (весь JS self-hosted —
    # см. static/) — это главный барьер против XSS; base-uri/object-src
    # заперты; шрифты 'self'. style-src вынужденно несёт 'unsafe-inline':
    # вендоренный CodeMirror 6 (static/vendor/) инжектит стили рантайм-тегом
    # <style>.textContent в light-DOM (style-mod: CSP-чистый путь через
    # constructable CSSStyleSheet включается только для shadow-DOM), а
    # nonce/hash для его динамических тем невозможны без пофайловой пересборки.
    # Наш СОБСТВЕННЫЙ разметочный код при этом свободен от инлайновых style=
    # (гейт `test_no_inline_styles_in_served_static`) — на 'unsafe-inline'
    # опирается только сторонний редактор, не наша поверхность; уберём его,
    # когда CodeMirror начнёт поддерживать nonce/constructable в light-DOM.
    # nosniff — на всех ответах, чтобы браузер не угадывал MIME.
    _CSP = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors 'none'"
    )

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", self._CSP)
            # issue #631: anti-clickjacking. Без этого страницу можно встроить
            # в <iframe> на чужом сайте: внутрифреймовые вызовы идут в СВОЙ
            # origin, поэтому CSRF-guard их не режет — жертва кликает по
            # невидимым «Проверить»/«Скачать». frame-ancestors в CSP выше
            # закрывает то же для современных браузеров, X-Frame-Options — для
            # старых, не понимающих эту директиву.
            self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        """Приглушить пер-запросный лог в stdout (иначе шумно)."""

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
            # issue #631: отсутствие ОБОИХ заголовков намеренно НЕ считается
            # нарушением (fail-open). Fail-closed на state-changing запросах
            # сломал бы документированное не-браузерное использование API
            # (docs/api.md, curl/скрипты, собственные тесты — они Origin не
            # шлют), а браузерный вектор уже закрыт двумя барьерами выше:
            # Host-check (только 127.0.0.1/localhost) и Sec-Fetch-Site, который
            # современные браузеры шлют всегда. Остаточный риск — легаси-браузер
            # без Fetch Metadata, подавивший Referer; на сервере эта эвристика
            # всё равно заменяется полноценной аутентификацией (эпик #621).
            return True
        hostname = (urlparse(value).hostname or "").lower()
        return hostname in _ALLOWED_HOSTNAMES

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
            with contextlib.suppress(OSError):
                self.rfile.read(min(length, _MAX_BODY_BYTES * 2))
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

    def _guard_and_read_body(self, parsed: Any) -> tuple[str, dict[str, Any]] | None:
        """Общий preamble body-эндпоинтов POST (issue #427): localhost/Origin
        guard (#242/#399) + чтение/валидация JSON-тела (#259). Возвращает
        ``(lang, body)`` либо ``None``, уже отправив 400/403/413 (паттерн
        «ответ внутри, отказ через None», как ``_confined_path``). Group A
        (create-run/cancel/auth-start) держат свой preamble внутри себя."""
        lang = _lang_from_query(parsed)
        if not self._guard_request(lang):
            return None
        body = self._read_json_body(lang)
        if body is None:
            return None
        return lang, body
