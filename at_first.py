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


def load_json_file(file_path: pathlib.Path) -> dict:
    with open(file_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_json_file(file_path: pathlib.Path, payload: dict) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def ask_value(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def create_or_update_config(config_path: pathlib.Path) -> dict:
    print("\nНастройка конфигурации...")
    root_dir = ask_value("Укажи базовую директорию для сохранения задач", "P2.2")
    secrets_path = ask_value("Укажи путь к secrets.json", "secrets.json")
    config = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    print(f"✅ Конфиг сохранён: {config_path.resolve()}")
    return config


def load_or_create_config(config_path: pathlib.Path) -> dict:
    if not config_path.exists():
        print("⚠️ Конфиг не найден. Будет создан новый.")
        return create_or_update_config(config_path)
    config = load_json_file(config_path)
    print("\nТекущая конфигурация:")
    print(f"root_dir:     {config.get('root_dir', '')}")
    print(f"secrets_path: {config.get('secrets_path', '')}")
    change = input("Нужно изменить настройки? [y/N]: ").strip().lower()
    if change in {"y", "yes", "д", "да"}:
        return create_or_update_config(config_path)
    return config


def normalize_config_paths(config: dict, config_path: pathlib.Path) -> dict:
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
        root_dir = pathlib.Path(config["root_dir"])
        secrets_path = pathlib.Path(config["secrets_path"])
        if not root_dir.is_absolute():
            root_dir = pathlib.Path.cwd() / root_dir
        if not secrets_path.is_absolute():
            secrets_path = pathlib.Path.cwd() / secrets_path
    normalized = {"root_dir": str(root_dir), "secrets_path": str(secrets_path)}
    save_json_file(config_path, normalized)
    return normalized


def load_secrets(secrets_path: pathlib.Path) -> dict:
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Файл secrets не найден: {secrets_path}\n"
            "Создай secrets.json рядом со скриптом или укажи корректный путь."
        )
    if secrets_path.is_dir():
        raise IsADirectoryError(
            f"Указан путь к папке, а нужен путь к файлу secrets.json: {secrets_path}"
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


def save_secrets(secrets_path: pathlib.Path, data: dict) -> None:
    save_json_file(secrets_path, data)


def make_session(access_token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"
    return session


def token_is_valid(secrets: dict) -> bool:
    access_token = str(secrets.get("access_token", "")).strip()
    expires_at = float(secrets.get("expires_at", 0) or 0)
    return bool(access_token) and time.time() < expires_at - 60


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


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
    auth_data: dict, path: str
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
                "<html><body><h2>Авторизация завершена.</h2>"
                "<p>Можно закрыть это окно и вернуться в консоль.</p></body></html>".encode(
                    "utf-8"
                )
            )

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
            """Suppress server request logging."""
            return

    return OAuthHandler


def wait_for_auth_code(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    raw_port = parsed.port
    path = parsed.path or "/"
    if raw_port is None:
        raise ValueError(
            "В redirect_uri должен быть указан порт, например http://localhost:8080/callback"
        )
    port: int = int(raw_port)
    auth_data: dict[str, str | None] = {"code": None, "error": None}
    handler_class = _make_oauth_handler(auth_data, path)
    server = HTTPServer((host, port), handler_class)  # type: ignore[arg-type]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if auth_data["error"]:
        raise RuntimeError(f"OAuth вернул ошибку: {auth_data['error']}")
    code = auth_data["code"]
    if not code:
        raise RuntimeError("Не удалось получить code через redirect_uri.")
    return code


def authorize_via_browser(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    auth_url = (
        f"{API_HOST}/oauth2/authorize/?"
        + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri})
    )
    print("\nОткрой в браузере и подтверди доступ приложению:")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except OSError:
        pass
    print("\nОжидание редиректа с code...")
    code = wait_for_auth_code(redirect_uri)
    print("✅ Authorization code получен.")
    token_response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    token_response.raise_for_status()
    return token_response.json()


def get_user_session(secrets_path: pathlib.Path) -> requests.Session:
    secrets = load_secrets(secrets_path)
    client_id = str(secrets["client_id"]).strip()
    client_secret = str(secrets["client_secret"]).strip()
    redirect_uri = str(secrets["redirect_uri"]).strip()
    if token_is_valid(secrets):
        print("✅ Используется сохранённый access_token.")
        raw_token = secrets.get("access_token")
        return make_session(str(raw_token))
    refresh_token = str(secrets.get("refresh_token", "")).strip()
    if refresh_token:
        try:
            print("🔄 Пробуем обновить access_token через refresh_token...")
            token_data = refresh_access_token(client_id, client_secret, refresh_token)
            raw_access = token_data.get("access_token")
            if not raw_access:
                raise RuntimeError("Stepik не вернул access_token при refresh.")
            access_token: str = str(raw_access)
            secrets["access_token"] = access_token
            secrets["expires_at"] = time.time() + token_data.get("expires_in", 3600)
            if token_data.get("refresh_token"):
                secrets["refresh_token"] = token_data["refresh_token"]
            save_secrets(secrets_path, secrets)
            print("✅ access_token обновлён без браузера.")
            return make_session(access_token)
        except requests.RequestException as error:
            print(f"⚠️ Не удалось обновить token через refresh_token: {error}")
    print("🌐 Нужна первичная авторизация через браузер...")
    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)
    raw_access = token_data.get("access_token")
    if not raw_access:
        raise RuntimeError("Stepik не вернул access_token.")
    access_token = str(raw_access)
    secrets["access_token"] = access_token
    secrets["expires_at"] = time.time() + token_data.get("expires_in", 3600)
    if token_data.get("refresh_token"):
        secrets["refresh_token"] = token_data["refresh_token"]
    save_secrets(secrets_path, secrets)
    print("✅ Токены сохранены в secrets.json.")
    return make_session(access_token)


def api_get(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        raise ValueError(f"Ожидался JSON от API, но получен Content-Type: {content_type}")
    return response.json()


def get_lesson_data(session: requests.Session, lesson_id: int) -> dict:
    data = api_get(session, f"{API_HOST}/api/lessons/{lesson_id}")
    lessons = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"API не вернул lesson для id={lesson_id}")
    return lessons[0]


def get_step_data(session: requests.Session, step_id: int) -> dict:
    data = api_get(session, f"{API_HOST}/api/steps/{step_id}")
    steps = data.get("steps", [])
    if not steps:
        raise ValueError(f"API не вернул step для step_id={step_id}")
    return steps[0]


def get_step_data_by_position(
    session: requests.Session, lesson_id: int, step_position: int
) -> tuple[int, dict, dict]:
    lesson = get_lesson_data(session, lesson_id)
    steps = lesson.get("steps", [])
    if not steps:
        raise ValueError("В lesson нет списка steps.")
    if step_position < 1 or step_position > len(steps):
        raise ValueError(f"В уроке {len(steps)} шаг(ов), но запрошен step={step_position}")
    step_id = steps[step_position - 1]
    step_data = get_step_data(session, step_id)
    return step_id, lesson, step_data


def extract_title_from_step_data(step_data: dict) -> str:
    import html as _html
    block = step_data.get("block", {})
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            for pattern in [r"<h1[^>]*>(.*?)</h1>", r"<h2[^>]*>(.*?)</h2>", r"<h3[^>]*>(.*?)</h3>"]:
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    title = _html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
                    if title:
                        return title
    title = step_data.get("title")
    if title and str(title).strip():
        return str(title).strip()
    if isinstance(block, dict):
        block_name = block.get("name")
        if block_name and str(block_name).strip() and str(block_name).strip().lower() != "code":
            return str(block_name).strip()
    return f"step-{step_data.get('position', 'task')}"


def extract_description_from_step_data(step_data: dict) -> str:
    import html as _html
    block = step_data.get("block", {})
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            return _html.unescape(text).strip()
    return "Описание задания не удалось автоматически извлечь."


def extract_zip_url_from_text(text: str) -> str | None:
    import html as _html
    decoded = _html.unescape(text)
    patterns = [
        r"https://stepik\.org/media/attachments/[^\s\"'<>()]+\.zip",
        r"https://stepic\.org/media/attachments/[^\s\"'<>()]+\.zip",
        r"https://ucarecdn\.com/[^\s\"'<>()]+\.zip",
        r"https://[^\s\"'<>()]+\.zip",
    ]
    for pattern in patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return match.group(0)
    markdown_patterns = [
        r"\[[^\]]+\]\((https://stepik\.org/media/attachments/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://stepic\.org/media/attachments/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://ucarecdn\.com/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://[^)]+\.zip)\)",
    ]
    for pattern in markdown_patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def collect_string_candidates(payload) -> list[str]:
    candidates: list[str] = []

    def walk(value) -> None:
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(payload)
    candidates.append(json.dumps(payload, ensure_ascii=False))
    return candidates


def extract_zip_url_from_step_data(step_data: dict) -> str | None:
    for candidate in collect_string_candidates(step_data):
        zip_url = extract_zip_url_from_text(candidate)
        if zip_url:
            return zip_url
    return None


def create_task_structure(
    root_folder: pathlib.Path, task_name: str
) -> tuple[pathlib.Path, pathlib.Path]:
    task_dir = root_folder / task_name
    tests_dir = task_dir / "tests"
    task_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    # Шаблоны файлов-решений в стиле task_N.py (совместимо с is_solution_file в test.py)
    file_templates = {
        "task_1.py": "# Решение 1\n",
        "task_2.py": "# Решение 2\n",
        "task_3.py": "# Решение 3\n",
    }
    for file_name, content in file_templates.items():
        file_path = task_dir / file_name
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")
    return task_dir, tests_dir


def save_readme(
    task_dir: pathlib.Path, title: str, url: str, description: str, zip_url: str | None,
) -> None:
    readme = (
        f"# {title}\n\n"
        f"Источник: {url}\n\n"
        f"ZIP с тестами: {zip_url or 'не найден автоматически'}\n\n"
        f"## Условие\n\n{description}\n"
    )
    (task_dir / "README.md").write_text(readme, encoding="utf-8")


def save_debug_json(task_dir: pathlib.Path, file_name: str, payload: dict) -> None:
    (task_dir / file_name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def download_and_extract_zip(
    session: requests.Session, zip_url: str, tests_dir: pathlib.Path
) -> None:
    response = session.get(zip_url, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if "zip" not in content_type and not zip_url.lower().endswith(".zip"):
        raise ValueError(f"Похоже, это не ZIP. Content-Type={content_type}, URL={zip_url}")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        archive.extractall(tests_dir)


def main() -> None:
    config_path = pathlib.Path(CONFIG_FILE)
    try:
        config = load_or_create_config(config_path)
        config = normalize_config_paths(config, config_path)
    except Exception as error:
        print(f"❌ Ошибка работы с конфигом: {error}")
        return
    step_url = input("\nEnter Stepik step URL: ").strip()
    if not step_url:
        print("❌ URL шага не указан.")
        return
    root_dir = pathlib.Path(config["root_dir"])
    secrets_file = pathlib.Path(config["secrets_path"])
    root_dir.mkdir(parents=True, exist_ok=True)
    try:
        lesson_id, step_position = parse_stepik_step_url(step_url)
        print(f"✅ URL распознан: lesson_id={lesson_id}, step={step_position}")
        session = get_user_session(secrets_file)
        print("✅ Пользовательская сессия готова.")
        step_id, lesson, step_data = get_step_data_by_position(session, lesson_id, step_position)
        print(f"✅ Step data получены. step_id={step_id}")
    except Exception as error:
        print(f"❌ Ошибка подготовки данных: {error}")
        return
    title = extract_title_from_step_data(step_data)
    description = extract_description_from_step_data(step_data)
    zip_url = extract_zip_url_from_step_data(step_data)
    print(f"\nНазвание задачи: {title}")
    task_name = slugify(f"step-{step_position} {title}")
    task_dir, tests_dir = create_task_structure(root_dir, task_name)
    save_debug_json(task_dir, "lesson_debug.json", lesson)
    save_debug_json(task_dir, "step_debug.json", step_data)
    if zip_url:
        print(f"✅ ZIP найден автоматически: {zip_url}")
    else:
        print("⚠️ ZIP не найден автоматически в step JSON.")
        zip_url = input("Enter ZIP url manually: ").strip()
    if not zip_url:
        print("❌ ZIP URL не указан.")
        return
    save_readme(task_dir, title, step_url, description, zip_url)
    try:
        download_and_extract_zip(session, zip_url, tests_dir)
        print("✅ Архив скачан и распакован.")
    except Exception as error:
        print(f"❌ Ошибка скачивания или распаковки архива: {error}")
        return
    print("\nГотово!")
    print(f"Task dir:  {task_dir}")
    print(f"Tests dir: {tests_dir}")
    print(f"Title:     {title}")


if __name__ == "__main__":
    main()
