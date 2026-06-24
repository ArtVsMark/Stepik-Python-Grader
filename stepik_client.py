"""stepik_client.py — HTTP/OAuth клиент для Stepik API.

Архитектурный слой: Infrastructure.
Отвечает исключительно за:
  - аутентификацию (OAuth2 Authorization Code + Refresh Token),
  - создание авторизованной requests.Session,
  - GET-запросы к Stepik REST API (/api/steps, /api/lessons и др.),
  - скачивание сабмишнов.

Бизнес-логика (slugify, build_task_directory, save_task_files и т.д.)
находится в at_first.py.

Типичный вызов из at_first.py:
    from stepik_client import create_user_session, fetch_step_data, ...
    session = create_user_session(secrets, secrets_path)
    step    = fetch_step_data(session, lesson_id, step_position)
"""

from __future__ import annotations

import pathlib
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.auth import HTTPBasicAuth

from storage import save_secrets

API_HOST = "https://stepik.org"

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


def make_session(access_token: str) -> requests.Session:
    """Возвращает requests.Session с Bearer-токеном в заголовках."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"
    return session


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def token_is_valid(secrets: dict[str, Any]) -> bool:
    """True если access_token существует и не истечёт в ближайшие 60 секунд."""
    access_token = str(secrets.get("access_token", "")).strip()
    expires_at = float(str(secrets.get("expires_at", 0) or 0))
    return bool(access_token) and time.time() < expires_at - 60


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Обменивает refresh_token на новую пару access/refresh токенов."""
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


# ---------------------------------------------------------------------------
# OAuth2 Authorization Code flow
# ---------------------------------------------------------------------------


def _make_oauth_handler(
    auth_data: dict[str, Any],
    path: str,
) -> type[BaseHTTPRequestHandler]:
    """Фабрика OAuthHandler: захватывает auth_data и ожидаемый path колбэка."""

    class OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            req = urlparse(self.path)
            params = parse_qs(req.query)
            if req.path != path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return
            auth_data["code"] = params.get("code", [None])[0]
            auth_data["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>OK. You can close this tab.</h1>")

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
            pass

    return OAuthHandler


def wait_for_auth_code(host: str, port: int, path: str, timeout: int = 120) -> str:
    """Запускает временный HTTP-сервер и ожидает OAuth-колбэк с кодом авторизации.

    Parameters
    ----------
    host, port, path:
        Параметры redirect_uri из secrets.json.
    timeout:
        Максимальное время ожидания в секундах (по умолчанию 120).

    Raises
    ------
    RuntimeError:
        Если Stepik вернул error-параметр в колбэке.
    TimeoutError:
        Если код не получен в течение timeout секунд.
    """
    auth_data: dict[str, Any] = {}
    handler_class = _make_oauth_handler(auth_data, path)
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
    """
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    path = parsed.path or "/"

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    auth_url = f"{API_HOST}/oauth2/authorize/?{urlencode(params)}"
    print(f"Открываю браузер: {auth_url}")
    try:
        webbrowser.open(auth_url)
    except OSError:
        pass

    code = wait_for_auth_code(host, port, path)
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
    token_data["expires_at"] = time.time() + float(str(token_data.get("expires_in", 3600)))
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
            token_data["expires_at"] = time.time() + float(str(token_data.get("expires_in", 3600)))
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
        response = session.get(
            f"{API_HOST}/api/steps",
            params={"lesson": lesson_id, "page": page},
            timeout=30,
        )
        response.raise_for_status()
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
    response = session.get(f"{API_HOST}/api/lessons/{lesson_id}", timeout=30)
    response.raise_for_status()
    lessons: list[dict[str, Any]] = response.json().get("lessons", [])
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
    response = session.get(f"{API_HOST}/api/units", params=params, timeout=30)
    response.raise_for_status()
    units: list[dict[str, Any]] = response.json().get("units", [])
    if not units:
        raise ValueError(f"Юнит для урока {lesson_id} не найден")
    return units[0]


def fetch_section_data(session: requests.Session, section_id: int) -> dict[str, Any]:
    """Возвращает объект секции по section_id."""
    response = session.get(f"{API_HOST}/api/sections/{section_id}", timeout=30)
    response.raise_for_status()
    sections: list[dict[str, Any]] = response.json().get("sections", [])
    if not sections:
        raise ValueError(f"Секция {section_id} не найдена")
    return sections[0]


def fetch_course_data(session: requests.Session, course_id: int) -> dict[str, Any]:
    """Возвращает объект курса по course_id."""
    response = session.get(f"{API_HOST}/api/courses/{course_id}", timeout=30)
    response.raise_for_status()
    courses: list[dict[str, Any]] = response.json().get("courses", [])
    if not courses:
        raise ValueError(f"Курс {course_id} не найден")
    return courses[0]


def fetch_submission_data(
    session: requests.Session,
    step_id: int,
) -> dict[str, Any] | None:
    """Возвращает последний сабмишн для шага или None если их нет."""
    response = session.get(
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
        timeout=30,
    )
    response.raise_for_status()
    submissions: list[dict[str, Any]] = response.json().get("submissions", [])
    return submissions[0] if submissions else None


def download_and_extract_submissions(
    session: requests.Session,
    step_id: int,
    task_dir: pathlib.Path,
) -> None:
    """Скачивает все сабмишны для step_id и сохраняет .py-файлы в task_dir/submissions/.

    Использует эндпоинт:
        GET /api/submissions?step=<step_id>&order=desc
    """
    response = session.get(
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
        timeout=30,
    )
    response.raise_for_status()
    submissions: list[dict[str, Any]] = response.json().get("submissions", [])

    if not submissions:
        print("  Сабмишны для данного шага не найдены.")
        return

    submissions_dir = task_dir / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for sub in submissions:
        sub_id = sub.get("id")
        reply: dict[str, Any] = sub.get("reply") or {}
        code = reply.get("code")
        if code and isinstance(code, str):
            file_name = f"submission_{sub_id}.py"
            (submissions_dir / file_name).write_text(code, encoding="utf-8")
            saved += 1

    if saved:
        print(f"  Сабмишны сохранены в: {submissions_dir} ({saved} файлов)")
    else:
        print("  Сабмишны найдены, но Python-код в ответах API не обнаружен.")
