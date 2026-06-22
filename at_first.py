from __future__ import annotations

import json
import pathlib
import re
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.auth import HTTPBasicAuth


API_HOST = "https://stepik.org"
CONFIG_FILE = "stepik_config.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}


def slugify(text: str) -> str:
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE)
    text = text.strip(".- ")
    return text[:80] or "task"


def load_json_file(file_path: pathlib.Path) -> dict[str, object]:
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_json_file(file_path: pathlib.Path, payload: dict[str, object]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def ask_value(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def create_or_update_config(config_path: pathlib.Path) -> dict[str, object]:
    print("\nНастройка конфигурации...")
    root_dir = ask_value("Укажи базовую директорию для сохранения задач", "P2.2")
    secrets_path = ask_value("Укажи путь к secrets.json", "secrets.json")
    config: dict[str, object] = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    print(f"✅ Конфиг сохранён: {config_path.resolve()}")
    return config


def load_or_create_config(config_path: pathlib.Path) -> dict[str, object]:
    if not config_path.exists():
        print("⚠️ Конфиг не найден. Будет создан новый.")
        return create_or_update_config(config_path)
    config = load_json_file(config_path)
    print("\nТекущая конфигурация:")
    print(f"root_dir:     {config.get('root_dir', '')}")
    print(f"secrets_path: {config.get('secrets_path', '')}")
    change = input("Нужно изменить настройку? [y/N]: ").strip().lower()
    if change in {"y", "yes", "д", "да"}:
        return create_or_update_config(config_path)
    return config


def normalize_config_paths(config: dict[str, object], config_path: pathlib.Path) -> dict[str, object]:
    root_dir_value = str(config.get("root_dir", "")).strip()
    secrets_value = str(config.get("secrets_path", "")).strip()
    if not root_dir_value or not secrets_value:
        print("⚠️ В конфиге не хватает обязательных полей.")
        config = create_or_update_config(config_path)
        root_dir_value = str(config["root_dir"]).strip()
        secrets_value = str(config["secrets_path"]).strip()
    root_dir = pathlib.Path(root_dir_value)
    secrets_path = pathlib.Path(secrets_value)
    if not root_dir.is_absolute():
        root_dir = pathlib.Path.cwd() / root_dir
    if not secrets_path.is_absolute():
        secrets_path = pathlib.Path.cwd() / secrets_path
    if not secrets_path.exists() or not secrets_path.is_file():
        print(f"⚠️ Файл secrets не найден: {secrets_path}")
        config = create_or_update_config(config_path)
        root_dir = pathlib.Path(str(config["root_dir"]))
        secrets_path = pathlib.Path(str(config["secrets_path"]))
        if not root_dir.is_absolute():
            root_dir = pathlib.Path.cwd() / root_dir
        if not secrets_path.is_absolute():
            secrets_path = pathlib.Path.cwd() / secrets_path
    normalized: dict[str, object] = {"root_dir": str(root_dir), "secrets_path": str(secrets_path)}
    save_json_file(config_path, normalized)
    return normalized


def load_secrets(secrets_path: pathlib.Path) -> dict[str, object]:
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Файл secrets не найден: {secrets_path}\n"
            "Создай secrets.json рядом со скриптом или укажи корректный путь."
        )
    if secrets_path.is_dir():
        raise IsADirectoryError(
            f"Укажи путь к папке, а нужен путь к файлу secrets.json: {secrets_path}"
        )
    with open(secrets_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("secrets.json должен быть JSON-объектом.")
    required_fields = ("client_id", "client_secret", "redirect_uri")
    for field in required_fields:
        if not str(data.get(field, "")).strip():
            raise ValueError(f"В secrets.json должно быть заполнено поле {field!r}")
    return data


def save_secrets(secrets_path: pathlib.Path, data: dict[str, object]) -> None:
    save_json_file(secrets_path, data)


def make_session(access_token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"
    return session


def token_is_valid(secrets: dict[str, object]) -> bool:
    access_token = str(secrets.get("access_token", "")).strip()
    expires_at = float(str(secrets.get("expires_at", 0) or 0))
    return bool(access_token) and time.time() < expires_at - 60


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, object]:
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def parse_stepik_step_url(step_url: str) -> tuple[int, int]:
    parsed = urlparse(step_url.strip())
    match = re.search(r"lesson/(\d+)/step/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            "Не удалось распознать URL шага. Ожидается формат:\n"
            "https://stepik.org/lesson/569749/step/4?unit=564263"
        )
    return int(match.group(1)), int(match.group(2))


def _make_oauth_handler(
    auth_data: dict[str, object], path: str
) -> type[BaseHTTPRequestHandler]:
    """Factory returning an OAuthHandler class bound to *auth_data* and *path*."""

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
            self.wfile.write(
                b"<h1>OK. You can close this tab.</h1>"
            )

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
            pass

    return OAuthHandler


def wait_for_auth_code(
    host: str, port: int, path: str, timeout: int = 120
) -> str:
    """Start a temporary HTTP server and wait for the OAuth callback."""
    auth_data: dict[str, object] = {}
    handler_class = _make_oauth_handler(auth_data, path)
    server = HTTPServer((host, port), handler_class)  # type: ignore[arg-type]
    server.timeout = timeout

    def serve() -> None:
        server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
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
) -> dict[str, object]:
    """Open browser, wait for OAuth code, exchange for tokens."""
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    path = parsed.path or "/"

    params = {
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
    token_data: dict[str, object] = response.json()
    token_data["expires_at"] = time.time() + float(str(token_data.get("expires_in", 3600)))
    return token_data


def create_user_session(secrets: dict[str, object], secrets_path: pathlib.Path) -> requests.Session:
    """Return an authenticated requests.Session, refreshing tokens as needed."""
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


def fetch_step_data(session: requests.Session, lesson_id: int, step_position: int) -> dict[str, object]:
    response = session.get(
        f"{API_HOST}/api/steps",
        params={"lesson": lesson_id},
        timeout=30,
    )
    response.raise_for_status()
    steps: list[dict[str, object]] = response.json().get("steps", [])
    for step in steps:
        if step.get("position") == step_position:
            return step
    raise ValueError(f"Шаг с позицией {step_position} не найден в уроке {lesson_id}")


def fetch_lesson_data(session: requests.Session, lesson_id: int) -> dict[str, object]:
    response = session.get(f"{API_HOST}/api/lessons/{lesson_id}", timeout=30)
    response.raise_for_status()
    lessons: list[dict[str, object]] = response.json().get("lessons", [])
    if not lessons:
        raise ValueError(f"Урок {lesson_id} не найден")
    return lessons[0]


def fetch_unit_data(session: requests.Session, lesson_id: int, unit_id: int | None) -> dict[str, object]:
    params: dict[str, object] = {"lesson": lesson_id}
    if unit_id is not None:
        params["id"] = unit_id
    response = session.get(f"{API_HOST}/api/units", params=params, timeout=30)
    response.raise_for_status()
    units: list[dict[str, object]] = response.json().get("units", [])
    if not units:
        raise ValueError(f"Юнит для урока {lesson_id} не найден")
    return units[0]


def fetch_section_data(session: requests.Session, section_id: int) -> dict[str, object]:
    response = session.get(f"{API_HOST}/api/sections/{section_id}", timeout=30)
    response.raise_for_status()
    sections: list[dict[str, object]] = response.json().get("sections", [])
    if not sections:
        raise ValueError(f"Секция {section_id} не найдена")
    return sections[0]


def fetch_course_data(session: requests.Session, course_id: int) -> dict[str, object]:
    response = session.get(f"{API_HOST}/api/courses/{course_id}", timeout=30)
    response.raise_for_status()
    courses: list[dict[str, object]] = response.json().get("courses", [])
    if not courses:
        raise ValueError(f"Курс {course_id} не найден")
    return courses[0]


def fetch_submission_data(session: requests.Session, step_id: int) -> dict[str, object] | None:
    response = session.get(
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
        timeout=30,
    )
    response.raise_for_status()
    submissions: list[dict[str, object]] = response.json().get("submissions", [])
    return submissions[0] if submissions else None


def extract_python_code(step: dict[str, object]) -> str | None:
    block: dict[str, object] = step.get("block") or {}
    for option in block.get("options") or []:
        if isinstance(option, dict) and option.get("code_template"):
            return str(option["code_template"])
    text = str(block.get("text", ""))
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def extract_submission_code(submission: dict[str, object] | None) -> str | None:
    if not submission:
        return None
    reply: dict[str, object] = submission.get("reply") or {}
    code = reply.get("code")
    return str(code) if code else None


def build_task_directory(
    root_dir: pathlib.Path,
    course_title: str,
    section_title: str,
    lesson_title: str,
    step_position: int,
    step_title: str,
) -> pathlib.Path:
    parts = [
        slugify(course_title),
        slugify(section_title),
        slugify(lesson_title),
        f"{step_position:02d}-{slugify(step_title)}",
    ]
    return root_dir.joinpath(*parts)


def save_task_files(
    task_dir: pathlib.Path,
    step: dict[str, object],
    submission: dict[str, object] | None,
    lesson: dict[str, object],
    section: dict[str, object],
    course: dict[str, object],
) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)

    template_code = extract_python_code(step)
    submitted_code = extract_submission_code(submission)

    if template_code:
        (task_dir / "template.py").write_text(template_code, encoding="utf-8")
    if submitted_code:
        (task_dir / "solution.py").write_text(submitted_code, encoding="utf-8")

    meta: dict[str, object] = {
        "step_id": step.get("id"),
        "step_position": step.get("position"),
        "step_title": step.get("title", ""),
        "lesson_id": lesson.get("id"),
        "lesson_title": lesson.get("title", ""),
        "section_id": section.get("id"),
        "section_title": section.get("title", ""),
        "course_id": course.get("id"),
        "course_title": course.get("title", ""),
        "submission_id": submission.get("id") if submission else None,
        "submission_status": submission.get("status") if submission else None,
    }
    save_json_file(task_dir / "meta.json", meta)

    block: dict[str, object] = step.get("block") or {}
    text = str(block.get("text", ""))
    if text:
        (task_dir / "task.md").write_text(text, encoding="utf-8")


def download_and_extract_submissions(
    session: requests.Session,
    step_id: int,
    task_dir: pathlib.Path,
) -> None:
    """Скачать архив с решениями для шага step_id и извлечь .py файлы.

    Использует корректный эндпоинт Stepik API:
        GET /api/submissions?step=<step_id>&order=desc
    Для каждого найденного решения сохраняет .py файл в task_dir/submissions/.
    """
    response = session.get(
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
        timeout=30,
    )
    response.raise_for_status()
    submissions: list[dict[str, object]] = response.json().get("submissions", [])

    if not submissions:
        print("  Решения для данного шага не найдены.")
        return

    submissions_dir = task_dir / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for sub in submissions:
        sub_id = sub.get("id")
        reply = sub.get("reply") or {}
        if isinstance(reply, dict):
            code = reply.get("code")
            if code and isinstance(code, str):
                fname = f"submission_{sub_id}.py"
                (submissions_dir / fname).write_text(code, encoding="utf-8")
                saved += 1

    if saved:
        print(f"  Решения сохранены в: {submissions_dir} ({saved} файлов)")
    else:
        print("  Решения найдены, но код Python не обнаружен в ответах API.")


def process_step_url(
    step_url: str,
    session: requests.Session,
    root_dir: pathlib.Path,
) -> None:
    parsed_url = urlparse(step_url)
    params = parse_qs(parsed_url.query)
    unit_id_list = params.get("unit", [])
    unit_id = int(unit_id_list[0]) if unit_id_list else None

    lesson_id, step_position = parse_stepik_step_url(step_url)

    print(f"  Получаю данные урока {lesson_id}...")
    lesson = fetch_lesson_data(session, lesson_id)
    lesson_title = str(lesson.get("title") or f"lesson-{lesson_id}")

    print("  Получаю данные юнита...")
    unit = fetch_unit_data(session, lesson_id, unit_id)
    section_id = int(str(unit.get("section") or 0))

    print(f"  Получаю данные секции {section_id}...")
    section = fetch_section_data(session, section_id)
    section_title = str(section.get("title") or f"section-{section_id}")
    course_id = int(str(section.get("course") or 0))

    print(f"  Получаю данные курса {course_id}...")
    course = fetch_course_data(session, course_id)
    course_title = str(course.get("title") or f"course-{course_id}")

    print(f"  Получаю данные шага {step_position}...")
    step = fetch_step_data(session, lesson_id, step_position)
    step_id = int(str(step.get("id") or 0))
    step_title = str(step.get("title") or lesson_title)

    print(f"  Получаю последний ответ для шага {step_id}...")
    submission = fetch_submission_data(session, step_id)

    task_dir = build_task_directory(
        root_dir,
        course_title,
        section_title,
        lesson_title,
        step_position,
        step_title,
    )

    print(f"  Сохраняю файлы в: {task_dir}")
    save_task_files(task_dir, step, submission, lesson, section, course)
    print(f"  ✅ Шаг сохранён: {task_dir}")


def main() -> None:
    config_path = pathlib.Path(CONFIG_FILE)

    try:
        config = load_or_create_config(config_path)
        config = normalize_config_paths(config, config_path)
    except Exception as error:  # noqa: BLE001
        print(f"❌ Ошибка работы с конфигом: {error}")
        return

    root_dir = pathlib.Path(str(config["root_dir"]))
    secrets_path = pathlib.Path(str(config["secrets_path"]))

    try:
        secrets = load_secrets(secrets_path)
        session = create_user_session(secrets, secrets_path)
    except Exception as error:  # noqa: BLE001
        print(f"❌ Ошибка подготовки данных: {error}")
        return

    print("\nВведите URL шагов (по одному, пустая строка — завершение):")
    while True:
        step_url = input("URL шага: ").strip()
        if not step_url:
            break
        try:
            process_step_url(step_url, session, root_dir)
        except Exception as error:  # noqa: BLE001
            print(f"❌ Ошибка обработки шага: {error}")


if __name__ == "__main__":
    main()
