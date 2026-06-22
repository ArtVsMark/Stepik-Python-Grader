"""at_first.py — бизнес-логика: конфиг, файловая система, оркестрация.

Архитектурный слой: Domain / Application.
Отвечает за:
  - управление конфигом (stepik_config.json) и secrets.json,
  - разбор URL шага Stepik,
  - построение директорий задач (slugify, build_task_directory),
  - сохранение файлов задачи (template.py, solution.py, meta.json, task.md),
  - оркестрацию вызовов к Stepik API через stepik_client.

HTTP/OAuth логика вынесена в stepik_client.py (Sprint 3, июнь 2026).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_client import (
    create_user_session,
    download_and_extract_submissions,
    fetch_course_data,
    fetch_lesson_data,
    fetch_section_data,
    fetch_step_data,
    fetch_submission_data,
    fetch_unit_data,
)

CONFIG_FILE = "stepik_config.json"


# ---------------------------------------------------------------------------
# Утилиты: JSON и файловая система
# ---------------------------------------------------------------------------

def load_json_file(file_path: pathlib.Path) -> dict[str, Any]:
    """Читает JSON-файл и возвращает dict. Бросает ValueError если корень не объект."""
    with open(file_path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_json_file(file_path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Сохраняет dict как JSON-файл, создавая родительские директории."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def slugify(text: str) -> str:
    """Преобразует произвольный текст в slug пригодный для имени директории.

    Максимум 80 символов. Возвращает 'task' если результат пустой.
    """
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE)
    text = text.strip(".- ")
    return text[:80] or "task"


# ---------------------------------------------------------------------------
# Утилиты: ввод-вывод
# ---------------------------------------------------------------------------

def ask_value(prompt: str, default: str = "") -> str:
    """Запрашивает значение у пользователя с опциональным дефолтом."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

def create_or_update_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Интерактивно создаёт или перезаписывает stepik_config.json."""
    print("\nНастройка конфигурации...")
    root_dir = ask_value("Укажи базовую директорию для сохранения задач", "P2.2")
    secrets_path = ask_value("Укажи путь к secrets.json", "secrets.json")
    config: dict[str, Any] = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    print(f"✅ Конфиг сохранён: {config_path.resolve()}")
    return config


def load_or_create_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Загружает конфиг; если не существует — запускает интерактивное создание."""
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


def normalize_config_paths(
    config: dict[str, Any],
    config_path: pathlib.Path,
) -> dict[str, Any]:
    """Нормализует пути в конфиге до абсолютных; повторно запрашивает при ошибках."""
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
    normalized: dict[str, Any] = {"root_dir": str(root_dir), "secrets_path": str(secrets_path)}
    save_json_file(config_path, normalized)
    return normalized


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def load_secrets(secrets_path: pathlib.Path) -> dict[str, Any]:
    """Загружает и валидирует secrets.json.

    Raises
    ------
    FileNotFoundError, IsADirectoryError, ValueError:
        При отсутствии файла, если указана папка, или при неполных полях.
    """
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Файл secrets не найден: {secrets_path}\n"
            "Создай secrets.json рядом со скриптом или укажи корректный путь."
        )
    if secrets_path.is_dir():
        raise IsADirectoryError(
            f"Укажи путь к папке, а нужен путь к файлу secrets.json: {secrets_path}"
        )
    with open(secrets_path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("secrets.json должен быть JSON-объектом.")
    required_fields = ("client_id", "client_secret", "redirect_uri")
    for field in required_fields:
        if not str(data.get(field, "")).strip():
            raise ValueError(f"В secrets.json должно быть заполнено поле {field!r}")
    return data


def save_secrets(secrets_path: pathlib.Path, data: dict[str, Any]) -> None:
    """Сохраняет secrets dict в файл."""
    save_json_file(secrets_path, data)


# ---------------------------------------------------------------------------
# URL-парсинг
# ---------------------------------------------------------------------------

def parse_stepik_step_url(step_url: str) -> tuple[int, int]:
    """Извлекает (lesson_id, step_position) из URL шага Stepik.

    Ожидаемый формат:
        https://stepik.org/lesson/569749/step/4?unit=564263
    """
    parsed = urlparse(step_url.strip())
    match = re.search(r"lesson/(\d+)/step/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            "Не удалось распознать URL шага. Ожидается формат:\n"
            "https://stepik.org/lesson/569749/step/4?unit=564263"
        )
    return int(match.group(1)), int(match.group(2))


# ---------------------------------------------------------------------------
# Извлечение кода
# ---------------------------------------------------------------------------

def extract_python_code(step: dict[str, Any]) -> str | None:
    """Извлекает Python code_template из объекта шага или из блока Markdown."""
    block: dict[str, Any] = step.get("block") or {}
    for option in block.get("options") or []:
        if isinstance(option, dict) and option.get("code_template"):
            return str(option["code_template"])
    text = str(block.get("text", ""))
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def extract_submission_code(submission: dict[str, Any] | None) -> str | None:
    """Извлекает Python-код из объекта сабмишна или возвращает None."""
    if not submission:
        return None
    reply: dict[str, Any] = submission.get("reply") or {}
    code = reply.get("code")
    return str(code) if code else None


# ---------------------------------------------------------------------------
# Построение директорий и сохранение файлов задачи
# ---------------------------------------------------------------------------

def build_task_directory(
    root_dir: pathlib.Path,
    course_title: str,
    section_title: str,
    lesson_title: str,
    step_position: int,
    step_title: str,
) -> pathlib.Path:
    """Строит путь к директории задачи по иерархии курс/секция/урок/шаг."""
    parts = [
        slugify(course_title),
        slugify(section_title),
        slugify(lesson_title),
        f"{step_position:02d}-{slugify(step_title)}",
    ]
    return root_dir.joinpath(*parts)


def save_task_files(
    task_dir: pathlib.Path,
    step: dict[str, Any],
    submission: dict[str, Any] | None,
    lesson: dict[str, Any],
    section: dict[str, Any],
    course: dict[str, Any],
) -> None:
    """Сохраняет template.py, solution.py, meta.json и task.md в task_dir."""
    task_dir.mkdir(parents=True, exist_ok=True)

    template_code = extract_python_code(step)
    submitted_code = extract_submission_code(submission)

    if template_code:
        (task_dir / "template.py").write_text(template_code, encoding="utf-8")
    if submitted_code:
        (task_dir / "solution.py").write_text(submitted_code, encoding="utf-8")

    meta: dict[str, Any] = {
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

    block: dict[str, Any] = step.get("block") or {}
    text = str(block.get("text", ""))
    if text:
        (task_dir / "task.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Оркестрация: один шаг
# ---------------------------------------------------------------------------

def process_step_url(
    step_url: str,
    session: "requests.Session",  # type: ignore[name-defined]  # noqa: F821
    root_dir: pathlib.Path,
) -> None:
    """Скачивает все данные одного шага и сохраняет в файловую систему."""
    import requests  # noqa: PLC0415

    parsed_url = urlparse(step_url)
    url_params = parse_qs(parsed_url.query)
    unit_id_list = url_params.get("unit", [])
    unit_id = int(unit_id_list[0]) if unit_id_list else None

    lesson_id, step_position = parse_stepik_step_url(step_url)

    print(f"  Получаю данные урока {lesson_id}...")
    lesson = fetch_lesson_data(session, lesson_id)
    lesson_title = str(lesson.get("title") or f"lesson-{lesson_id}")

    print("  Получаю данные юнита...")
    unit = fetch_unit_data(session, lesson_id, unit_id)
    section_id = int(unit.get("section") or 0)

    print(f"  Получаю данные секции {section_id}...")
    section = fetch_section_data(session, section_id)
    section_title = str(section.get("title") or f"section-{section_id}")
    course_id = int(section.get("course") or 0)

    print(f"  Получаю данные курса {course_id}...")
    course = fetch_course_data(session, course_id)
    course_title = str(course.get("title") or f"course-{course_id}")

    print(f"  Получаю данные шага {step_position}...")
    step = fetch_step_data(session, lesson_id, step_position)
    step_id = int(step.get("id") or 0)
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


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    """Главная функция: конфиг → авторизация → цикл обработки URL шагов."""
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
