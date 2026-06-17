import html
import json
import pathlib
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.auth import HTTPBasicAuth

API_HOST = "https://stepik.org"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}


def load_secrets(secrets_path: pathlib.Path) -> tuple[str, str, str]:
    if not secrets_path.exists():
        raise FileNotFoundError(f"Файл secrets не найден: {secrets_path}")
    if secrets_path.is_dir():
        raise IsADirectoryError(
            f"Указан путь к папке, а нужен путь к файлу secrets.json: {secrets_path}"
        )
    with open(secrets_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(
            'secrets.json должен быть словарём формата '
            '{"client_id": "...", "client_secret": "...", "redirect_uri": "..."}'
        )
    client_id = str(data.get("client_id", "")).strip()
    client_secret = str(data.get("client_secret", "")).strip()
    redirect_uri = str(data.get("redirect_uri", "")).strip()
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError(
            "В secrets.json должны быть заполнены client_id, client_secret и redirect_uri."
        )
    return client_id, client_secret, redirect_uri


def parse_stepik_step_url(step_url: str) -> tuple[int, int]:
    parsed = urlparse(step_url.strip())
    match = re.search(r"lesson/(\d+)/step/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            "Не удалось распознать URL шага. Ожидается формат:\n"
            "https://stepik.org/lesson/569749/step/4?unit=564263"
        )
    return int(match.group(1)), int(match.group(2))


def wait_for_auth_code(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port
    path = parsed.path or "/"
    if port is None:
        raise ValueError(
            "В redirect_uri должен быть указан порт, например http://localhost:8080/callback"
        )
    auth_data = {"code": None, "error": None}

    class OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
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
                "<p>Можно закрыть это окно и вернуться в консоль.</p></body></html>".encode("utf-8")
            )

        def log_message(self, format, *args):
            return

    server = HTTPServer((host, port), OAuthHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if auth_data["error"]:
        raise RuntimeError(f"OAuth вернул ошибку: {auth_data['error']}")
    if not auth_data["code"]:
        raise RuntimeError("Не удалось получить code через redirect_uri.")
    return auth_data["code"]


def create_user_session(client_id: str, client_secret: str, redirect_uri: str) -> requests.Session:
    auth_url = (
        f"{API_HOST}/oauth2/authorize/?"
        + urlencode({"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri})
    )
    print("\nОткрой в браузере и подтверди доступ приложению:")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
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
    token_data = token_response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Stepik не вернул access_token.")
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"
    return session


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


def save_json(output_dir: pathlib.Path, filename: str, payload: dict) -> pathlib.Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / filename
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def extract_zip_url_from_text(text: str) -> str | None:
    decoded = html.unescape(text)
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
    def walk(value):
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


def build_diagnostic_result(
    lesson: dict, step_id: int, step_data: dict, zip_url: str | None,
    lesson_path: pathlib.Path, step_path: pathlib.Path,
) -> dict:
    block = step_data.get("block", {}) if isinstance(step_data, dict) else {}
    return {
        "lesson_id": lesson.get("id"),
        "lesson_title": lesson.get("title"),
        "step_id": step_id,
        "step_position": step_data.get("position"),
        "block_name": block.get("name") if isinstance(block, dict) else None,
        "block_keys": list(block.keys()) if isinstance(block, dict) else [],
        "block_text_exists": isinstance(block, dict) and "text" in block,
        "zip_found": bool(zip_url),
        "zip_url": zip_url,
        "lesson_debug_path": str(lesson_path.resolve()),
        "step_debug_path": str(step_path.resolve()),
    }


def print_result_summary(result: dict, output_dir: pathlib.Path) -> None:
    print("\n=== Результат диагностики ===")
    print(f"Lesson ID:        {result['lesson_id']}")
    print(f"Lesson title:     {result['lesson_title']}")
    print(f"Step ID:          {result['step_id']}")
    print(f"Step position:    {result['step_position']}")
    print(f"Block name:       {result['block_name']}")
    print(f"Block keys:       {result['block_keys']}")
    print(f"Block text exists:{result['block_text_exists']}")
    print(f"ZIP найден:       {'да' if result['zip_found'] else 'нет'}")
    if result["zip_url"]:
        print(f"ZIP URL:          {result['zip_url']}")
    print(f"Результаты сохранены в: {output_dir.resolve()}")


def main() -> None:
    step_url = input("Enter Stepik step URL: ").strip()
    secrets_file = input("Enter secrets.json path [secrets.json]: ").strip() or "secrets.json"
    output_dir_input = (
        input("Enter diagnostics output dir [stepik_diagnostics]: ").strip()
        or "stepik_diagnostics"
    )
    output_dir = pathlib.Path(output_dir_input)
    try:
        client_id, client_secret, redirect_uri = load_secrets(pathlib.Path(secrets_file))
        print("✅ secrets.json успешно прочитан.")
        lesson_id, step_position = parse_stepik_step_url(step_url)
        print(f"✅ URL распознан: lesson_id={lesson_id}, step={step_position}")
        session = create_user_session(client_id, client_secret, redirect_uri)
        print("✅ OAuth access token пользователя успешно получен.")
        step_id, lesson, step_data = get_step_data_by_position(session, lesson_id, step_position)
        print("✅ Step data получены через API /steps/{id}.")
    except Exception as error:
        print(f"❌ Ошибка диагностики: {error}")
        return
    lesson_path = save_json(output_dir, "lesson_debug.json", lesson)
    step_path = save_json(output_dir, "step_debug.json", step_data)
    zip_url = extract_zip_url_from_step_data(step_data)
    result = build_diagnostic_result(
        lesson=lesson, step_id=step_id, step_data=step_data, zip_url=zip_url,
        lesson_path=lesson_path, step_path=step_path,
    )
    save_json(output_dir, "diagnostic_result.json", result)
    print_result_summary(result, output_dir)


if __name__ == "__main__":
    main()
