"""diagnostic_stepik.py — OAuth-диагностика шага Stepik через API.

Архитектурный слой: CLI-инструмент / диагностика.
OAuth делегируется в oauth_flow (фасад поверх stepik_client):
  - load_secrets — импортируется из oauth_flow
  - authorize_via_browser — импортируется из oauth_flow
  - make_session — импортируется из oauth_flow
  - API_HOST — импортируется из stepik_client (константа)

Запуск:
    python -m stepik_grader.diagnostic_stepik
"""

from __future__ import annotations

import html
import json
import pathlib
import re
from urllib.parse import urlencode

import requests

from stepik_grader.core.oauth_flow import authorize_via_browser, load_secrets, make_session
from stepik_grader.core.stepik_client import API_HOST
from stepik_grader.downloader import parse_stepik_step_url

# Задача 6: таймаут ожидания OAuth-кода от браузера
OAUTH_TIMEOUT_SECONDS = 120


# ---------------------------------------------------------------------------
# OAuth2 — адаптер поверх oauth_flow.authorize_via_browser
# ---------------------------------------------------------------------------


def create_user_session(client_id: str, client_secret: str, redirect_uri: str) -> requests.Session:
    """Провести OAuth2-авторизацию и вернуть сессию с Bearer-токеном.

    Делегирует полный OAuth-flow в oauth_flow.authorize_via_browser.
    Принимает три отдельных аргумента (диагностический интерфейс),
    а не secrets-dict (интерфейс downloader).
    """
    auth_url = f"{API_HOST}/oauth2/authorize/?" + urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri}
    )
    print("\nОткрой в браузере и подтверди доступ приложению:")
    print(auth_url)
    print(f"\nОжидание редиректа с code (таймаут {OAUTH_TIMEOUT_SECONDS}s)...")

    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Stepik не вернул access_token.")
    print("✅ Authorization code получен.")
    return make_session(str(access_token))


# ---------------------------------------------------------------------------
# Диагностические API-обёртки
# ---------------------------------------------------------------------------


def api_get(session: requests.Session, url: str) -> dict:  # type: ignore[type-arg]
    """GET-запрос к Stepik API; проверяет Content-Type и статус."""
    response = session.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        raise ValueError(f"Ожидался JSON от API, но получен Content-Type: {content_type}")
    return response.json()  # type: ignore[return-value]


def get_lesson_data(session: requests.Session, lesson_id: int) -> dict:  # type: ignore[type-arg]
    """Получить данные урока по lesson_id."""
    data = api_get(session, f"{API_HOST}/api/lessons/{lesson_id}")
    lessons = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"API не вернул lesson для id={lesson_id}")
    return lessons[0]


def get_step_data(session: requests.Session, step_id: int) -> dict:  # type: ignore[type-arg]
    """Получить данные шага по step_id."""
    data = api_get(session, f"{API_HOST}/api/steps/{step_id}")
    steps = data.get("steps", [])
    if not steps:
        raise ValueError(f"API не вернул step для step_id={step_id}")
    return steps[0]


def get_step_data_by_position(
    session: requests.Session,
    lesson_id: int,
    step_position: int,
) -> tuple[int, dict, dict]:  # type: ignore[type-arg]
    """Получить step_id, lesson, step_data по позиции шага в уроке."""
    lesson = get_lesson_data(session, lesson_id)
    steps = lesson.get("steps", [])
    if not steps:
        raise ValueError("В lesson нет списка steps.")
    if step_position < 1 or step_position > len(steps):
        raise ValueError(f"В уроке {len(steps)} шаг(ов), но запрошен step={step_position}")
    step_id = steps[step_position - 1]
    step_data = get_step_data(session, step_id)
    return step_id, lesson, step_data


# ---------------------------------------------------------------------------
# Утилиты: JSON, ZIP-поиск, диагностический вывод
# ---------------------------------------------------------------------------


def save_json(
    output_dir: pathlib.Path,
    filename: str,
    payload: dict,  # type: ignore[type-arg]
) -> pathlib.Path:
    """Сохранить payload как JSON-файл в output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / filename
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


def extract_zip_url_from_text(text: str) -> str | None:
    """Найти первый ZIP-URL в произвольном тексте (HTML, Markdown, JSON)."""
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


def collect_string_candidates(payload: object) -> list[str]:
    """Рекурсивно собрать все строки из произвольной JSON-структуры."""
    candidates: list[str] = []

    def walk(value: object) -> None:
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


def extract_zip_url_from_step_data(step_data: dict) -> str | None:  # type: ignore[type-arg]
    """Найти ZIP-URL во всех строковых полях step_data."""
    for candidate in collect_string_candidates(step_data):
        zip_url = extract_zip_url_from_text(candidate)
        if zip_url:
            return zip_url
    return None


def build_diagnostic_result(
    lesson: dict,  # type: ignore[type-arg]
    step_id: int,
    step_data: dict,  # type: ignore[type-arg]
    zip_url: str | None,
    lesson_path: pathlib.Path,
    step_path: pathlib.Path,
) -> dict:  # type: ignore[type-arg]
    """Собрать итоговый словарь диагностики."""
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


def print_result_summary(
    result: dict,  # type: ignore[type-arg]
    output_dir: pathlib.Path,
) -> None:
    """Вывести читаемый итог диагностики в консоль."""
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


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    """Точка входа: диагностика шага Stepik через OAuth API."""
    step_url = input("Enter Stepik step URL: ").strip()
    secrets_file = input("Enter secrets.json path [secrets.json]: ").strip() or "secrets.json"
    output_dir_input = (
        input("Enter diagnostics output dir [stepik_diagnostics]: ").strip() or "stepik_diagnostics"
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
    except Exception as error:  # noqa: BLE001
        print(f"❌ Ошибка диагностики: {error}")
        return
    lesson_path = save_json(output_dir, "lesson_debug.json", lesson)
    step_path = save_json(output_dir, "step_debug.json", step_data)
    zip_url = extract_zip_url_from_step_data(step_data)
    result = build_diagnostic_result(
        lesson=lesson,
        step_id=step_id,
        step_data=step_data,
        zip_url=zip_url,
        lesson_path=lesson_path,
        step_path=step_path,
    )
    save_json(output_dir, "diagnostic_result.json", result)
    print_result_summary(result, output_dir)


if __name__ == "__main__":
    main()
