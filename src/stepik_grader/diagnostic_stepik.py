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
from typing import Any
from urllib.parse import urlencode

import requests

from stepik_grader.core.diag_log import configure_diagnostics, get_logger
from stepik_grader.core.oauth_flow import authorize_via_browser, load_secrets, make_session
from stepik_grader.core.stepik_client import API_HOST
from stepik_grader.downloader import parse_stepik_step_url

__all__ = [
    "OAUTH_TIMEOUT_SECONDS",
    "api_get",
    "build_diagnostic_result",
    "collect_string_candidates",
    "create_user_session",
    "extract_zip_url_from_step_data",
    "extract_zip_url_from_text",
    "get_lesson_data",
    "get_step_data",
    "get_step_data_by_position",
    "main",
    "print_result_summary",
    "save_json",
]

# Вывод через rich с graceful fallback на print() (инвариант CLAUDE.md).
# Свой локальный _console (leaf-совместимо, не тянет core/reporter), issue #354.
try:
    from rich.console import Console

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False


def _print(text: str) -> None:
    """Печать статусной строки через rich (markup off — безопасно для путей/URL)."""
    if _RICH and _console is not None:
        _console.print(text, markup=False)
    else:
        print(text)


# Задача 6: таймаут ожидания OAuth-кода от браузера
_log = get_logger("diagnostic")  # issue #831 (DEV-12): стек падения в opt-in лог

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
    _print("\nОткрой в браузере и подтверди доступ приложению:")
    _print(auth_url)
    _print(f"\nОжидание редиректа с code (таймаут {OAUTH_TIMEOUT_SECONDS}s)...")

    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Stepik не вернул access_token.")
    _print("✅ Authorization code получен.")
    return make_session(str(access_token))


# ---------------------------------------------------------------------------
# Диагностические API-обёртки
# ---------------------------------------------------------------------------


def api_get(session: requests.Session, url: str) -> dict[str, Any]:
    """GET-запрос к Stepik API; проверяет Content-Type и статус."""
    response = session.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        raise ValueError(f"Ожидался JSON от API, но получен Content-Type: {content_type}")
    data: dict[str, Any] = response.json()
    return data


def get_lesson_data(session: requests.Session, lesson_id: int) -> dict[str, Any]:
    """Получить данные урока по lesson_id."""
    data = api_get(session, f"{API_HOST}/api/lessons/{lesson_id}")
    lessons = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"API не вернул lesson для id={lesson_id}")
    lesson: dict[str, Any] = lessons[0]
    return lesson


def get_step_data(session: requests.Session, step_id: int) -> dict[str, Any]:
    """Получить данные шага по step_id."""
    data = api_get(session, f"{API_HOST}/api/steps/{step_id}")
    steps = data.get("steps", [])
    if not steps:
        raise ValueError(f"API не вернул step для step_id={step_id}")
    step: dict[str, Any] = steps[0]
    return step


def get_step_data_by_position(
    session: requests.Session,
    lesson_id: int,
    step_position: int,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
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
    payload: dict[str, Any],
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


def extract_zip_url_from_step_data(step_data: dict[str, Any]) -> str | None:
    """Найти ZIP-URL во всех строковых полях step_data."""
    for candidate in collect_string_candidates(step_data):
        zip_url = extract_zip_url_from_text(candidate)
        if zip_url:
            return zip_url
    return None


def build_diagnostic_result(
    lesson: dict[str, Any],
    step_id: int,
    step_data: dict[str, Any],
    zip_url: str | None,
    lesson_path: pathlib.Path,
    step_path: pathlib.Path,
) -> dict[str, Any]:
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
    result: dict[str, Any],
    output_dir: pathlib.Path,
) -> None:
    """Вывести читаемый итог диагностики в консоль."""
    _print("\n=== Результат диагностики ===")
    _print(f"Lesson ID:        {result['lesson_id']}")
    _print(f"Lesson title:     {result['lesson_title']}")
    _print(f"Step ID:          {result['step_id']}")
    _print(f"Step position:    {result['step_position']}")
    _print(f"Block name:       {result['block_name']}")
    _print(f"Block keys:       {result['block_keys']}")
    _print(f"Block text exists:{result['block_text_exists']}")
    _print(f"ZIP найден:       {'да' if result['zip_found'] else 'нет'}")
    if result["zip_url"]:
        _print(f"ZIP URL:          {result['zip_url']}")
    _print(f"Результаты сохранены в: {output_dir.resolve()}")


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
    # issue #146: диагностическая утилита — включаем общий логгер (debug) в тот же
    # каталог; сетевые вызовы через stepik_client логируются с редакцией секретов.
    configure_diagnostics("debug", log_dir=output_dir)
    try:
        client_id, client_secret, redirect_uri = load_secrets(pathlib.Path(secrets_file))
        _print("✅ secrets.json успешно прочитан.")
        lesson_id, step_position = parse_stepik_step_url(step_url)
        _print(f"✅ URL распознан: lesson_id={lesson_id}, step={step_position}")
        session = create_user_session(client_id, client_secret, redirect_uri)
        _print("✅ OAuth access token пользователя успешно получен.")
        step_id, lesson, step_data = get_step_data_by_position(session, lesson_id, step_position)
        _print("✅ Step data получены через API /steps/{id}.")
    except Exception as error:
        # issue #831 (DEV-12): в лог — стек, пользователю — короткая строка.
        # Диагностика без места падения бесполезна ровно там, где нужна.
        _log.exception("сбой диагностики Stepik (secrets=%s, url=%s)", secrets_file, step_url)
        _print(f"❌ Ошибка диагностики: {error}")
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
