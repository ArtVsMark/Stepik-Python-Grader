"""stepik_client.py — HTTP/OAuth клиент для Stepik API.

Архитектурный слой: Infrastructure.
Отвечает исключительно за:
  - аутентификацию (OAuth2 Authorization Code + Refresh Token),
  - создание авторизованной requests.Session,
  - GET-запросы к Stepik REST API (/api/steps, /api/lessons и др.),
  - скачивание сабмишнов.

Бизнес-логика (slugify, build_task_directory, save_task_files и т.д.)
находится в downloader.py.

Типичный вызов из downloader.py:
    from stepik_client import create_user_session, fetch_step_data, ...
    session = create_user_session(secrets, secrets_path)
    step    = fetch_step_data(session, lesson_id, step_position)
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json as _json_mod
import pathlib
import secrets as secrets_module
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from stepik_grader.core.diag_log import get_logger, register_secret
from stepik_grader.core.storage import save_secrets

__all__ = [
    "API_HOST",
    "CACHE_DIR",
    "CACHE_TTL_SECONDS",
    "EXTERNAL_DOWNLOAD_ALLOWED_HOSTS",
    "HEADERS",
    "RETRY_STATUS_FORCELIST",
    "STEPIK_HOST",
    "ExternalUrlRejected",
    "authorize_via_browser",
    "create_user_session",
    "external_download_get",
    "fetch_comments_with_submissions",
    "fetch_course_data",
    "fetch_discussion_proxy",
    "fetch_discussion_threads",
    "fetch_lesson_data",
    "fetch_section_data",
    "fetch_step_data",
    "fetch_submission_data",
    "fetch_unit_data",
    "is_stepik_url",
    "make_session",
    "refresh_access_token",
    "token_is_valid",
    "validate_external_url",
    "wait_for_auth_code",
]

_log = get_logger("stepik_client")  # issue #148: диагностический лог (opt-in)

API_HOST = "https://stepik.org"

# issue #109: статусы, повторяемые транспортным слоем make_session() —
# 429 (rate limit) и временные 5xx. 4xx помимо 429 не повторяются (не временные).
RETRY_STATUS_FORCELIST: tuple[int, ...] = (429, 500, 502, 503, 504)

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def make_session(
    access_token: str,
    *,
    retries: int = 3,
    backoff_factor: float = 1.0,
) -> requests.Session:
    """Возвращает requests.Session с Bearer-токеном и transport-level retry (issue #109).

    ``HTTPAdapter`` + ``urllib3.util.Retry`` монтируются на http/https:
    429 (rate limit) и временные 5xx (``RETRY_STATUS_FORCELIST``) повторяются
    автоматически с экспоненциальным backoff (``backoff_factor`` удваивается с
    каждой попыткой; ``Retry`` также уважает заголовок ``Retry-After``, если
    сервер его прислал). Действует на уровне транспорта — применяется к любому
    запросу через эту сессию, а не только к вызовам ``_get_with_retry()``
    (который остаётся дополнительным уровнем повтора при сетевых
    исключениях — см. его docstring). Прочие 4xx (напр. 404) не повторяются.

    Args:
        retries: максимальное число попыток на статусы из
            ``RETRY_STATUS_FORCELIST`` (``Retry.total``).
        backoff_factor: базовая задержка в секундах перед повтором; тесты
            передают маленькое значение, чтобы не ждать реально.
    """
    register_secret(access_token)  # issue #148: маскировать токен в диагностике
    _log.debug("создаю авторизованную сессию к %s (retries=%d)", API_HOST, retries)
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"

    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUS_FORCELIST,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# External downloads: unauthenticated session + host allowlist (issue #240)
#
# downloader.py извлекает ZIP/GitHub-ссылки из текста задачи Stepik. Эти
# ссылки могут указывать на произвольный сторонний хост, поэтому им нельзя
# передавать авторизованную ``requests.Session`` из ``make_session()`` —
# иначе OAuth Bearer-токен утечёт на сторонний домен вместе с запросом.
# ---------------------------------------------------------------------------

STEPIK_HOST = "stepik.org"

# Известные легитимные источники внешних тест-кейсов (issue #240, F-01).
# Расширять точечно, а не заменой на wildcard-подстроку хоста.
EXTERNAL_DOWNLOAD_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "api.github.com",
        "codeload.github.com",
    }
)


class ExternalUrlRejected(ValueError):
    """URL внешней загрузки не прошёл проверку allowlist/private-address."""


def _is_stepik_host(hostname: str) -> bool:
    """True если hostname — сам Stepik (stepik.org или его поддомен)."""
    return hostname == STEPIK_HOST or hostname.endswith(f".{STEPIK_HOST}")


def _is_disallowed_ip_literal(hostname: str) -> bool:
    """True если hostname — IP-литерал loopback/private/link-local/reserved сети."""
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast


def is_stepik_url(url: str) -> bool:
    """True если URL указывает на сам Stepik (авторизованная сессия уместна)."""
    return _is_stepik_host((urlparse(url).hostname or "").lower())


def validate_external_url(url: str) -> None:
    """Проверяет URL перед скачиванием без OAuth-токена.

    Разрешены только http(s)-ссылки на хосты из
    ``EXTERNAL_DOWNLOAD_ALLOWED_HOSTS`` (точное совпадение hostname — без
    поддоменных трюков вида ``raw.githubusercontent.com.evil.example``).
    Loopback/private/link-local/reserved IP-литералы отклоняются безусловно.
    Поднимает :class:`ExternalUrlRejected` при отказе.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ExternalUrlRejected(f"Недопустимая схема URL: {url!r}")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ExternalUrlRejected(f"Не удалось определить host: {url!r}")

    if hostname == "localhost" or _is_disallowed_ip_literal(hostname):
        raise ExternalUrlRejected(f"Локальный/приватный адрес запрещён: {url!r}")

    if hostname not in EXTERNAL_DOWNLOAD_ALLOWED_HOSTS:
        raise ExternalUrlRejected(f"Host не входит в allowlist внешних загрузок: {hostname!r}")


def external_download_get(
    url: str,
    *,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET на внешний URL через отдельную сессию без Authorization (issue #240).

    Используется для ZIP/GitHub-ссылок из текста задачи Stepik, которые не
    являются самим Stepik API — им не передаётся Bearer-токен текущей
    OAuth-сессии. URL сначала проверяется через :func:`validate_external_url`.
    """
    validate_external_url(url)
    session = requests.Session()
    session.headers.update(HEADERS)
    if headers:
        session.headers.update(headers)
    return session.get(url, timeout=timeout)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def token_is_valid(secrets: dict[str, Any]) -> bool:
    """True если access_token существует и не истечёт в ближайшие 60 секунд."""
    access_token = str(secrets.get("access_token", "")).strip()
    expires_at = float(secrets.get("expires_at", 0) or 0)
    return bool(access_token) and time.time() < expires_at - 60


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Обменивает refresh_token на новую пару access/refresh токенов."""
    register_secret(client_secret)
    register_secret(refresh_token)
    _log.info("обновляю access_token через %s/oauth2/token/ (grant=refresh_token)", API_HOST)
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    token_data = cast(dict[str, Any], response.json())
    register_secret(str(token_data.get("access_token", "")))
    register_secret(str(token_data.get("refresh_token", "")))
    _log.info("access_token обновлён (expires_in=%s)", token_data.get("expires_in"))
    return token_data


# ---------------------------------------------------------------------------
# OAuth2 Authorization Code flow
# ---------------------------------------------------------------------------


def _make_oauth_handler(
    auth_data: dict[str, Any],
    path: str,
    expected_state: str,
) -> type[BaseHTTPRequestHandler]:
    """Фабрика OAuthHandler: захватывает auth_data, path и ожидаемый OAuth ``state``.

    ``expected_state`` защищает от Login-CSRF (issue #241): колбэк с
    ``state``, не совпадающим с тем, что был отправлен в authorize URL,
    отклоняется без извлечения ``code`` — иначе злоумышленник мог бы
    подсунуть жертве ссылку на локальный callback-сервер со своим кодом
    авторизации и привязать её сессию к своему Stepik-аккаунту.
    """

    class OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            req = urlparse(self.path)
            params = parse_qs(req.query)
            if req.path != path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            received_state = params.get("state", [None])[0]
            if received_state != expected_state:
                auth_data["error"] = "state_mismatch"
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>Invalid state. Possible CSRF - authorization rejected.</h1>")
                return

            auth_data["code"] = params.get("code", [None])[0]
            auth_data["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>OK. You can close this tab.</h1>")

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    return OAuthHandler


def wait_for_auth_code(
    host: str,
    port: int,
    path: str,
    expected_state: str,
    timeout: int = 120,
) -> str:
    """Запускает временный HTTP-сервер и ожидает OAuth-колбэк с кодом авторизации.

    Parameters
    ----------
    host, port, path:
        Параметры redirect_uri из secrets.json.
    expected_state:
        Значение ``state``, отправленное в authorize URL (issue #241);
        колбэк с несовпадающим/отсутствующим ``state`` отклоняется как
        потенциальный Login-CSRF.
    timeout:
        Максимальное время ожидания в секундах (по умолчанию 120).

    Raises
    ------
    RuntimeError:
        Если Stepik вернул error-параметр в колбэке, либо ``state`` колбэка
        не совпал с ``expected_state``.
    TimeoutError:
        Если код не получен в течение timeout секунд.
    """
    auth_data: dict[str, Any] = {}
    handler_class = _make_oauth_handler(auth_data, path, expected_state)
    server = HTTPServer((host, port), handler_class)  # type: ignore[arg-type]
    server.timeout = timeout

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)
    server.server_close()

    code = auth_data.get("code")
    error = auth_data.get("error")
    if error:
        raise RuntimeError(f"OAuth error: {error}")
    if not code:
        raise TimeoutError(f"OAuth code not received within {timeout}s")
    return str(code)


def authorize_via_browser(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Открывает браузер, ожидает OAuth-код, обменивает на токены.

    Возвращает dict с ключами: access_token, refresh_token, expires_in, expires_at.

    Отправляет криптографически случайный ``state`` в authorize URL и требует
    его точного совпадения в колбэке (issue #241, F-02) — защита от
    Login-CSRF, когда злоумышленник подсовывает жертве ссылку на локальный
    callback-сервер со своим кодом авторизации.
    """
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    path = parsed.path or "/"

    state = secrets_module.token_urlsafe(32)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    auth_url = f"{API_HOST}/oauth2/authorize/?{urlencode(params)}"
    print(f"Открываю браузер: {auth_url}")
    with contextlib.suppress(OSError):
        webbrowser.open(auth_url)

    code = wait_for_auth_code(host, port, path, state)
    register_secret(client_secret)
    register_secret(code)
    _log.info("обмениваю authorization_code на токены через %s/oauth2/token/", API_HOST)
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    token_data: dict[str, Any] = response.json()
    register_secret(str(token_data.get("access_token", "")))
    register_secret(str(token_data.get("refresh_token", "")))
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    _log.info("получены токены (expires_in=%s)", token_data.get("expires_in"))
    return token_data


def create_user_session(
    secrets: dict[str, Any],
    secrets_path: pathlib.Path,
) -> requests.Session:
    """Возвращает аутентифицированную requests.Session.

    Логика приоритетов:
    1. Валидный access_token → вернуть сессию сразу.
    2. Есть refresh_token → попробовать обновить.
    3. Иначе → полный OAuth flow через браузер.

    secrets обновляется на месте; новые токены сохраняются в secrets_path.
    """
    client_id = str(secrets["client_id"])
    client_secret = str(secrets["client_secret"])
    redirect_uri = str(secrets["redirect_uri"])

    if token_is_valid(secrets):
        return make_session(str(secrets["access_token"]))

    refresh_token = str(secrets.get("refresh_token", "")).strip()
    if refresh_token:
        try:
            token_data = refresh_access_token(client_id, client_secret, refresh_token)
            token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
            secrets.update(token_data)
            save_secrets(secrets_path, secrets)
            return make_session(str(secrets["access_token"]))
        except requests.HTTPError:
            print("Refresh token истёк, выполняется повторная авторизация...")

    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)
    secrets.update(token_data)
    save_secrets(secrets_path, secrets)
    return make_session(str(secrets["access_token"]))


# ---------------------------------------------------------------------------
# HTTP helpers: retry + file cache
# ---------------------------------------------------------------------------


def _get_with_retry(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    backoff: float = 1.0,
    timeout: int = 30,
) -> requests.Response:
    """GET-запрос с повтором при сетевых ошибках (exponential backoff).

    Parameters
    ----------
    retries:
        Максимальное число попыток (включая первую).
    backoff:
        Базовая задержка в секундах; удваивается с каждой попыткой.
    """
    last_exc: requests.RequestException | None = None
    for attempt in range(retries):
        try:
            # URL/параметры санитизируются редакцией логгера (issue #148)
            _log.debug("GET %s params=%s (попытка %d/%d)", url, params, attempt + 1, retries)
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            _log.debug("GET %s → %d", url, response.status_code)
            return response
        except requests.RequestException as exc:
            last_exc = exc
            _log.warning("GET %s не удался (попытка %d/%d): %s", url, attempt + 1, retries, exc)
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
    if last_exc is None:
        raise RuntimeError(f"_get_with_retry called with retries={retries}, no attempts made")
    raise last_exc


CACHE_DIR = pathlib.Path(".stepik_cache")
CACHE_TTL_SECONDS = 3600  # 1 hour


def _cached_api_get(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET-запрос с файловым кэшем (TTL=1ч) и retry при сетевых ошибках.

    Кэшируется полный JSON-ответ по ключу SHA-256(url + params).
    Используется только для read-only API-эндпоинтов Stepik.
    """
    key_data = _json_mod.dumps({"url": url, "params": params or {}}, sort_keys=True)
    key = hashlib.sha256(key_data.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"

    CACHE_DIR.mkdir(exist_ok=True)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                return cast(
                    dict[str, Any],
                    _json_mod.loads(cache_file.read_text(encoding="utf-8")),
                )
            except (_json_mod.JSONDecodeError, OSError):
                pass

    response = _get_with_retry(session, url, params=params)
    data: dict[str, Any] = response.json()
    with contextlib.suppress(OSError):
        cache_file.write_text(_json_mod.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# Stepik REST API — fetch helpers
# ---------------------------------------------------------------------------


def fetch_step_data(
    session: requests.Session,
    lesson_id: int,
    step_position: int,
) -> dict[str, Any]:
    """Возвращает объект шага по позиции внутри урока (с пагинацией)."""
    page = 1
    while True:
        response = _get_with_retry(
            session,
            f"{API_HOST}/api/steps",
            params={"lesson": lesson_id, "page": page},
        )
        data = response.json()
        steps: list[dict[str, Any]] = data.get("steps", [])
        for step in steps:
            if step.get("position") == step_position:
                return step
        meta = data.get("meta", {})
        if not meta.get("has_next"):
            break
        page += 1
    raise ValueError(f"Шаг с позицией {step_position} не найден в уроке {lesson_id}")


def fetch_lesson_data(session: requests.Session, lesson_id: int) -> dict[str, Any]:
    """Возвращает объект урока по lesson_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/lessons/{lesson_id}")
    lessons: list[dict[str, Any]] = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"Урок {lesson_id} не найден")
    return lessons[0]


def fetch_unit_data(
    session: requests.Session,
    lesson_id: int,
    unit_id: int | None,
) -> dict[str, Any]:
    """Возвращает объект юнита для урока."""
    params: dict[str, int] = {"lesson": lesson_id}
    if unit_id is not None:
        params["id"] = unit_id
    data = _cached_api_get(session, f"{API_HOST}/api/units", params=params)
    units: list[dict[str, Any]] = data.get("units", [])
    if not units:
        raise ValueError(f"Юнит для урока {lesson_id} не найден")
    return units[0]


def fetch_section_data(session: requests.Session, section_id: int) -> dict[str, Any]:
    """Возвращает объект секции по section_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/sections/{section_id}")
    sections: list[dict[str, Any]] = data.get("sections", [])
    if not sections:
        raise ValueError(f"Секция {section_id} не найдена")
    return sections[0]


def fetch_course_data(session: requests.Session, course_id: int) -> dict[str, Any]:
    """Возвращает объект курса по course_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/courses/{course_id}")
    courses: list[dict[str, Any]] = data.get("courses", [])
    if not courses:
        raise ValueError(f"Курс {course_id} не найден")
    return courses[0]


def fetch_submission_data(
    session: requests.Session,
    step_id: int,
) -> dict[str, Any] | None:
    """Возвращает последний сабмишн для шага или None если их нет."""
    response = _get_with_retry(
        session,
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
    )
    submissions: list[dict[str, Any]] = response.json().get("submissions", [])
    return submissions[0] if submissions else None


# ---------------------------------------------------------------------------
# Discussions / solutions (issue #55): ветка обсуждений шага и закреплённые
# решения. Read-only публичные данные → кэшируются как fetch_lesson/course.
# ---------------------------------------------------------------------------


def fetch_discussion_threads(
    session: requests.Session,
    thread_ids: list[str],
) -> list[dict[str, Any]]:
    """Возвращает объекты discussion-thread'ов шага по их id.

    У шага ``discussion_threads`` — список id вида ``"77-2506803-1"``. Каждый
    объект несёт поле ``thread`` (``"default"`` — обычные обсуждения,
    ``"solutions"`` — ветка решений, открывающаяся после сдачи).
    """
    if not thread_ids:
        return []
    data = _cached_api_get(
        session,
        f"{API_HOST}/api/discussion-threads",
        params={"ids[]": thread_ids},
    )
    threads: list[dict[str, Any]] = data.get("discussion-threads", [])
    return threads


def fetch_discussion_proxy(
    session: requests.Session,
    proxy_id: str,
) -> dict[str, Any]:
    """Возвращает discussion-proxy по id (списки id комментариев ветки).

    Содержит ``discussions`` (все id), ``discussions_most_liked`` (топ по
    лайкам, отсортировано убыв.) и др. ``proxy_id`` берётся из объекта thread
    (``discussion_proxy``).
    """
    data = _cached_api_get(session, f"{API_HOST}/api/discussion-proxies/{proxy_id}")
    proxies: list[dict[str, Any]] = data.get("discussion-proxies", [])
    if not proxies:
        raise ValueError(f"discussion-proxy {proxy_id} не найден")
    return proxies[0]


def fetch_comments_with_submissions(
    session: requests.Session,
    comment_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Возвращает ``(comments, submissions)`` для списка id комментариев.

    Ключ — параметр ``expand=submission`` (issue #55): без него сабмишн
    закреплённого решения приватен (``/api/submissions/{id}`` → 403,
    ``?ids[]=`` → пусто), а с ним код приходит в top-level массиве
    ``submissions`` ответа ``/api/comments``. Каждый comment ссылается на свой
    сабмишн полем ``submission`` (id), связываемым с ``submissions[].id``.
    """
    if not comment_ids:
        return [], []
    data = _cached_api_get(
        session,
        f"{API_HOST}/api/comments",
        params={"ids[]": comment_ids, "expand": "submission"},
    )
    return data.get("comments", []), data.get("submissions", [])
