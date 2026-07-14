"""downloader.py — координатор загрузки задач Stepik (issue #302).

Архитектурный слой: Application.
Отвечает за оркестрацию:
  - построение директорий задач (build_task_directory),
  - сохранение файлов задачи (task{N}_1.py, task{N}_2.py, solution.py,
    meta.json, task.md) и выбор источника тестов (save_task_files),
  - обработку одного URL шага (process_step_url) и CLI-цикл (main).

После SRP-разбиения (issue #302) специализированные роли вынесены; downloader
реэкспортирует их публичные имена для обратной совместимости (см. импорты):
  - HTTP/OAuth               → core/stepik_client.py, core/oauth_flow.py
  - файловый I/O             → core/storage.py
  - разбор HTML текста задачи → core/task_page_parser.py
  - запись форматов тестов   → core/tests_writer.py
  - скачивание ZIP/GitHub    → core/test_source_fetcher.py
  - разбор Stepik API/URL     → core/step_content.py
  - конфиг + интерактив       → downloader_config.py

Схема именования рабочих файлов:
  task{step_position}_1.py  — основное решение (заполняется из template_code)
  task{step_position}_2.py  — заглушка для альтернативного решения 1
  task{step_position}_3.py  — (добавляется вручную) альтернативное решение 2
"""

from __future__ import annotations

import pathlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from stepik_grader.core.diag_log import configure_diagnostics, get_logger
from stepik_grader.core.oauth_flow import create_user_session, load_secrets_dict
from stepik_grader.core.step_content import (
    extract_function_name,
    extract_python_code,
    extract_submission_code,
    parse_stepik_step_url,
)
from stepik_grader.core.stepik_client import (
    fetch_course_data,
    fetch_lesson_data,
    fetch_section_data,
    fetch_step_data,
    fetch_submission_data,
    fetch_unit_data,
)
from stepik_grader.core.storage import save_json_file

# issue #302 (SRP): downloader.py стал координатором. Вынесены — чистый разбор
# HTML текста задачи (core/task_page_parser), запись форматов тестов
# (core/tests_writer), скачивание тестов из ZIP/GitHub (core/test_source_fetcher),
# разбор Stepik API-контента и URL (core/step_content), конфиг+интерактив
# (downloader_config). Публичные имена реэкспортируются здесь для обратной
# совместимости: тесты, web-адаптер и diagnostic_stepik импортируют их из
# stepik_grader.downloader, а функции ниже зовут их как локальные.
# `is_function_style`/`download_*` сохраняют старые приватные имена
# `_is_function_style`/`_download_*` — на них завязаны test_analyzer.py и
# патчи save_task_files.
from stepik_grader.core.task_page_parser import (
    extract_external_test_links,
    extract_tests_from_html,
)
from stepik_grader.core.task_page_parser import (
    is_function_style as _is_function_style,  # noqa: F401 — back-compat реэкспорт
)
from stepik_grader.core.test_source_fetcher import (
    download_github_tests as _download_github_tests,
)
from stepik_grader.core.test_source_fetcher import (
    download_zip_tests as _download_zip_tests,
)
from stepik_grader.core.tests_writer import save_tests
from stepik_grader.downloader_config import (
    DEFAULT_ROOT_DIR,  # noqa: F401 — back-compat реэкспорт
    ask_value,  # noqa: F401 — back-compat реэкспорт
    create_or_update_config,  # noqa: F401 — back-compat реэкспорт
    load_or_create_config,
    normalize_config_paths,
    slugify,
)

__all__ = [
    "CONFIG_FILE",
    "build_task_directory",
    "save_task_files",
    "process_step_url",
    "main",
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


CONFIG_FILE = "stepik_config.json"

_log = get_logger("downloader")  # issue #147: диагностический лог загрузки (opt-in)


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
    step_dir_name = f"{step_position:02d}"
    if step_title.strip():
        step_dir_name = f"{step_position:02d}-{slugify(step_title)}"

    parts = [
        slugify(course_title),
        slugify(section_title),
        slugify(lesson_title),
        step_dir_name,
    ]
    return root_dir.joinpath(*parts)


def save_task_files(
    task_dir: pathlib.Path,
    step: dict[str, Any],
    submission: dict[str, Any] | None,
    lesson: dict[str, Any],
    section: dict[str, Any],
    course: dict[str, Any],
    session: requests.Session,
) -> tuple[int, str]:
    """Сохраняет рабочие файлы, solution.py, meta.json, task.md и tests/ в task_dir.

    Схема рабочих файлов:
      task{pos}_1.py  — основное решение (из template_code или пустая заглушка)
      task{pos}_2.py  — заглушка для альтернативного решения 1 (всегда создаётся)

    Порядок поиска тестов:
      1. ZIP-ссылка в HTML (скачать и сконвертировать в Format 3);
      2. HTML-таблица в тексте задачи;
      3. Ссылка на GitHub (скачать через GitHub Contents API);
      4. Ничего нет — предупреждение, остальные файлы уже сохранены.

    Returns:
        (count, source) — число извлечённых тест-кейсов и источник
        ("zip"|"html_table"|"github_link"|"none"), issue #186: нужно
        web-адаптеру для контракта ``TestCaseSet``, не восстановимо post-hoc
        (ZIP и GitHub-вариант А дают одинаковый на диске Format 3).
    """
    task_dir.mkdir(parents=True, exist_ok=True)

    template_code = extract_python_code(step)
    submitted_code = extract_submission_code(submission)
    step_position = int(step.get("position") or 0)

    # Рабочие файлы: task{pos}_1.py и task{pos}_2.py
    main_file = task_dir / f"task{step_position}_1.py"
    alt_file = task_dir / f"task{step_position}_2.py"

    main_content = template_code if template_code else ""
    main_file.write_text(main_content, encoding="utf-8")

    if not alt_file.exists():
        alt_file.write_text("", encoding="utf-8")

    if submitted_code:
        (task_dir / "solution.py").write_text(submitted_code, encoding="utf-8")

    # Определяем имя функции из template_code для function-mode runner
    function_name: str | None = None
    if template_code:
        function_name = extract_function_name(template_code)

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
        # Имя функции для function-mode runner в grader.py.
        # None если задача не является функциональной (stdin-режим).
        "function_name": function_name,
    }
    save_json_file(task_dir / "meta.json", meta)

    block: dict[str, Any] = step.get("block") or {}
    text = str(block.get("text", ""))
    if not text:
        return 0, "none"

    (task_dir / "task.md").write_text(text, encoding="utf-8")

    zip_links, github_links = extract_external_test_links(text)

    # 1. ZIP — скачиваем автоматически
    for zip_url in zip_links:
        _print(f"  📦 Найдена ZIP-ссылка: {zip_url}")
        count = _download_zip_tests(task_dir, zip_url, session)
        if count:
            _print(f"  📦 Скачано тестов из ZIP: {count}")
            return count, "zip"

    # 2. HTML-таблица
    tests = extract_tests_from_html(text)
    if tests:
        count = save_tests(task_dir, tests)
        _print(f"  📋 Извлечено тестов из таблицы: {count}")
        return count, "html_table"

    # 3. GitHub — скачиваем через GitHub Contents API
    if github_links:
        for gh_url in github_links:
            _print(f"  🔗 Пробую скачать тесты с GitHub: {gh_url}")
            count = _download_github_tests(task_dir, gh_url)
            if count:
                _print(f"  🔗 Скачано {count} тестов с GitHub")
                return count, "github_link"
        _print("  ⚠️ GitHub: ни одна ссылка не дала тестов")
        return 0, "none"

    # 4. Ничего не нашли
    _print("  ⚠️ Тесты не найдены (нет ZIP, таблицы и GitHub-ссылок) — остальные файлы сохранены")
    return 0, "none"


# ---------------------------------------------------------------------------
# Оркестрация: один шаг
# ---------------------------------------------------------------------------


def process_step_url(
    step_url: str,
    session: requests.Session,
    root_dir: pathlib.Path,
) -> tuple[pathlib.Path, int, str]:
    """Скачивает все данные одного шага и сохраняет в файловую систему.

    Returns:
        (task_dir, tests_count, tests_source) — issue #186: web-адаптеру
        Downloader'а нужен путь и информация о тестах, чтобы не дублировать
        эту оркестрацию (fetch_lesson_data/fetch_unit_data/...) заново.
    """
    parsed_url = urlparse(step_url)
    url_params = parse_qs(parsed_url.query)
    unit_id_list = url_params.get("unit", [])
    unit_id = int(unit_id_list[0]) if unit_id_list else None

    lesson_id, step_position = parse_stepik_step_url(step_url)
    _log.info("разбор URL шага: lesson=%s step=%s unit=%s", lesson_id, step_position, unit_id)

    _print(f"  Получаю данные урока {lesson_id}...")
    lesson = fetch_lesson_data(session, lesson_id)
    lesson_title = str(lesson.get("title") or f"lesson-{lesson_id}")

    _print("  Получаю данные юнита...")
    unit = fetch_unit_data(session, lesson_id, unit_id)
    section_id = int(unit.get("section") or 0)

    _print(f"  Получаю данные секции {section_id}...")
    section = fetch_section_data(session, section_id)
    section_title = str(section.get("title") or f"section-{section_id}")
    course_id = int(section.get("course") or 0)

    _print(f"  Получаю данные курса {course_id}...")
    course = fetch_course_data(session, course_id)
    course_title = str(course.get("title") or f"course-{course_id}")

    _print(f"  Получаю данные шага {step_position}...")
    step = fetch_step_data(session, lesson_id, step_position)
    step_id = int(step.get("id") or 0)
    step_title = str(step.get("title") or "").strip()

    _print(f"  Получаю последний ответ для шага {step_id}...")
    submission = fetch_submission_data(session, step_id)

    task_dir = build_task_directory(
        root_dir,
        course_title,
        section_title,
        lesson_title,
        step_position,
        step_title,
    )

    _print(f"  Сохраняю файлы в: {task_dir}")
    count, source = save_task_files(task_dir, step, submission, lesson, section, course, session)
    _log.info("тест-кейсы: %d шт., источник=%s (task_dir=%s)", count, source, task_dir)
    _print(f"  ✅ Шаг сохранён: {task_dir}")
    return task_dir, count, source


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    """Главная функция: конфиг → авторизация → цикл обработки URL шагов."""
    configure_diagnostics()  # issue #146: opt-in по STEPIK_GRADER_LOG (по умолч. тихо)
    config_path = pathlib.Path(CONFIG_FILE)

    try:
        config = load_or_create_config(config_path)
        config = normalize_config_paths(config, config_path)
    except Exception as error:  # noqa: BLE001
        _print(f"❌ Ошибка работы с конфигом: {error}")
        return

    root_dir = pathlib.Path(str(config["root_dir"]))
    secrets_path = pathlib.Path(str(config["secrets_path"]))

    try:
        secrets = load_secrets_dict(secrets_path)
        session = create_user_session(secrets, secrets_path)
    except Exception as error:  # noqa: BLE001
        _print(f"❌ Ошибка подготовки данных: {error}")
        return

    _print("\nВведите URL шагов (по одному, пустая строка — завершение):")
    while True:
        step_url = input("URL шага: ").strip()
        if not step_url:
            break
        try:
            process_step_url(step_url, session, root_dir)
        except Exception as error:  # noqa: BLE001
            _print(f"❌ Ошибка обработки шага: {error}")


if __name__ == "__main__":
    main()
