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

from stepik_grader import downloader_config
from stepik_grader.core.diag_log import configure_diagnostics, get_logger
from stepik_grader.core.i18n import load_locale_messages
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
    STEPIK_OAUTH_APPS_URL,
    ask_value,  # noqa: F401 — back-compat реэкспорт
    create_or_update_config,  # noqa: F401 — back-compat реэкспорт
    create_secrets_interactively,  # noqa: F401 — back-compat реэкспорт (issue #433)
    load_or_create_config,
    normalize_config_paths,
    slugify,
)

__all__ = [
    "CONFIG_FILE",
    "build_task_directory",
    "main",
    "process_step_url",
    "save_task_files",
    "set_lang",
]

# -- Язык интерактива (issue #821) -------------------------------------------
#
# Загрузчик вызывается из меню (пункт 8), которое уже знает язык пользователя,
# и из `python -m stepik_grader.downloader` напрямую. Язык — модульное
# состояние с единственной точкой установки `set_lang`, поэтому сигнатуры
# `process_step_url`/`save_task_files` (их зовёт web-адаптер) не меняются.
_LANG = "ru"
_FALLBACK_LANG = "ru"


def set_lang(lang: str) -> None:
    """Задать язык статусов загрузчика (issue #821)."""
    global _LANG
    _LANG = lang


def _t(key: str, /, **kwargs: object) -> str:
    """Строка каталога на текущем языке; отсутствующий ключ показывается как есть."""
    messages = load_locale_messages(_LANG) or load_locale_messages(_FALLBACK_LANG)
    template = messages.get(key, key)
    return template.format(**kwargs) if kwargs else template


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


def _is_blank_or_missing(path: pathlib.Path) -> bool:
    """True, если файла нет или он пуст (только пробельные байты).

    issue #554: страхует ``save_task_files`` от затирания написанного
    студентом ``task{N}_1.py`` при повторном скачивании шага. Читает байты
    (не ``read_text``), чтобы решение в не-UTF-8 кодировке (частый cp1251 у
    студентов на Windows) считалось непустым и сохранялось, а не роняло
    загрузчик ``UnicodeDecodeError``.
    """
    if not path.exists():
        return True
    try:
        return not path.read_bytes().strip()
    except OSError:
        return False


def _warn_if_stale_tests(task_dir: pathlib.Path) -> None:
    """Предупредить, что в ``tests/`` остались кейсы от прошлого скачивания.

    Очистка ``tests/`` (``_reset_tests_dir``) выполняется только когда новый
    набор кейсов реально получен. Если перекачивание не принесло тестов, старые
    файлы остаются, и решение молча грейдится против устаревших кейсов —
    тихий неверный вердикт (issue #626).

    Удалять их нельзя: документация прямо разрешает заполнять ``tests/``
    вручную, поэтому автоматический снос затирал бы работу пользователя.
    Вместо этого — явное предупреждение.
    """
    tests_dir = task_dir / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.iterdir()):
        return
    _print(
        "  ⚠️ В tests/ остались файлы от прошлого скачивания (или добавленные "
        "вручную) — они НЕ обновлены. Проверь их актуальность: иначе вердикт "
        "будет посчитан по устаревшим кейсам."
    )


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
      task{pos}_1.py  — основное решение (из template_code или пустая заглушка);
                        повторное скачивание НЕ перезаписывает непустой файл
                        (issue #554)
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
    # issue #554: писать шаблон только в отсутствующий или пустой файл, чтобы
    # повторное скачивание шага НЕ затирало уже написанное решение (раньше
    # write_text был безусловным — асимметрия с защищённым alt_file ниже и
    # реальная потеря данных студента).
    if _is_blank_or_missing(main_file):
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
        _warn_if_stale_tests(task_dir)
        return 0, "none"

    (task_dir / "task.md").write_text(text, encoding="utf-8")

    zip_links, github_links = extract_external_test_links(text)

    # 1. ZIP — скачиваем автоматически
    for zip_url in zip_links:
        _print(_t("dl_zip_found", url=zip_url))
        count = _download_zip_tests(task_dir, zip_url, session)
        if count:
            _print(_t("dl_zip_downloaded", count=count))
            return count, "zip"

    # 2. HTML-таблица
    tests = extract_tests_from_html(text)
    if tests:
        count = save_tests(task_dir, tests)
        _print(_t("dl_table_extracted", count=count))
        return count, "html_table"

    # 3. GitHub — скачиваем через GitHub Contents API
    if github_links:
        for gh_url in github_links:
            _print(_t("dl_github_trying", url=gh_url))
            count = _download_github_tests(task_dir, gh_url)
            if count:
                _print(_t("dl_github_downloaded", count=count))
                return count, "github_link"
        _print(_t("dl_github_empty"))
        _warn_if_stale_tests(task_dir)
        return 0, "none"

    # 4. Ничего не нашли
    _print(_t("dl_tests_not_found"))
    _warn_if_stale_tests(task_dir)
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

    _print(_t("dl_fetch_lesson", id=lesson_id))
    lesson = fetch_lesson_data(session, lesson_id)
    lesson_title = str(lesson.get("title") or f"lesson-{lesson_id}")

    _print(_t("dl_fetch_unit"))
    unit = fetch_unit_data(session, lesson_id, unit_id)
    section_id = int(unit.get("section") or 0)

    _print(_t("dl_fetch_section", id=section_id))
    section = fetch_section_data(session, section_id)
    section_title = str(section.get("title") or f"section-{section_id}")
    course_id = int(section.get("course") or 0)

    _print(_t("dl_fetch_course", id=course_id))
    course = fetch_course_data(session, course_id)
    course_title = str(course.get("title") or f"course-{course_id}")

    _print(_t("dl_fetch_step", position=step_position))
    step = fetch_step_data(session, lesson_id, step_position)
    step_id = int(step.get("id") or 0)
    step_title = str(step.get("title") or "").strip()

    _print(_t("dl_fetch_submission", id=step_id))
    submission = fetch_submission_data(session, step_id)

    task_dir = build_task_directory(
        root_dir,
        course_title,
        section_title,
        lesson_title,
        step_position,
        step_title,
    )

    _print(_t("dl_saving_files", path=task_dir))
    count, source = save_task_files(task_dir, step, submission, lesson, section, course, session)
    _log.info("тест-кейсы: %d шт., источник=%s (task_dir=%s)", count, source, task_dir)
    _print(_t("dl_step_saved", path=task_dir))
    return task_dir, count, source


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main(lang: str = "ru") -> list[pathlib.Path]:
    """Главная функция: конфиг → авторизация → цикл обработки URL шагов.

    ``lang`` (issue #821) — язык интерактива: меню уже знает выбор пользователя
    и передаёт его сюда, поэтому мастер OAuth и статусы скачивания больше не
    остаются русскими под ``--lang en``. Значение прокидывается и в
    ``downloader_config``, где живёт вся конфигурационная часть мастера.

    Returns:
        Каталоги успешно скачанных задач, в порядке скачивания (issue #822).
        Меню предлагает по ним сразу запустить проверку — иначе пользователь
        вручную набирал многосегментный путь с кириллическими slug'ами ровно в
        момент активации, «задача скачана → первый зелёный прогон». Сбой
        конфига/авторизации и отказ по одному URL дают пустой список, а не
        исключение: цикл ввода URL остаётся best-effort, как и был.
    """
    set_lang(lang)
    downloader_config.set_lang(lang)
    configure_diagnostics()  # issue #146: opt-in по STEPIK_GRADER_LOG (по умолч. тихо)
    config_path = pathlib.Path(CONFIG_FILE)

    try:
        config = load_or_create_config(config_path)
        config = normalize_config_paths(config, config_path)
    except Exception as error:
        # issue #831 (DEV-12): стек — в диагностический лог (opt-in, с редакцией
        # секретов). Пользователю остаётся короткое сообщение, но баг-репорт
        # приходит с местом падения, а не с одной строкой текста.
        _log.exception("сбой чтения конфигурации загрузчика: %s", config_path)
        _print(_t("dl_config_error", error=error))
        return []

    root_dir = pathlib.Path(str(config["root_dir"]))
    secrets_path = pathlib.Path(str(config["secrets_path"]))

    try:
        secrets = load_secrets_dict(secrets_path)
        session = create_user_session(secrets, secrets_path)
    except Exception as error:
        _log.exception("сбой авторизации Stepik (secrets=%s)", secrets_path)
        # issue #433: дружелюбная ошибка со следующим шагом, а не голый текст.
        _print(_t("dl_auth_failed", error=error))
        _print(_t("dl_auth_check_fields", url=STEPIK_OAUTH_APPS_URL))
        _print(_t("dl_auth_diagnostics"))
        _print(_t("dl_auth_docs"))
        return []

    downloaded: list[pathlib.Path] = []
    _print(f"\n{_t('dl_enter_urls')}")
    while True:
        step_url = input(f"{_t('dl_step_url_prompt')}: ").strip()
        if not step_url:
            break
        try:
            task_dir, _, _ = process_step_url(step_url, session, root_dir)
        except Exception as error:
            _log.exception("сбой обработки шага: %s", step_url)
            _print(_t("dl_step_error", error=error))
        else:
            downloaded.append(task_dir)
    return downloaded


if __name__ == "__main__":
    main()
