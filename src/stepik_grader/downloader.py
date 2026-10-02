"""downloader.py — координатор загрузки задач Stepik (issue #302).

Архитектурный слой: Application.
Отвечает за оркестрацию:
  - построение директорий задач (build_task_directory),
  - сохранение файлов задачи (task{N}_1.py, task{N}_2.py, solution.py,
    meta.json, task.html + task.md) и выбор источника тестов (save_task_files),
  - обработку одного URL шага (process_step_url) и CLI-цикл (main).

После SRP-разбиения (issue #302) специализированные роли вынесены; downloader
реэкспортирует их публичные имена для обратной совместимости (см. импорты):
  - HTTP/OAuth               → core/stepik_client.py, core/oauth_flow.py
  - файловый I/O             → atomic_io.py (общий атомарный писатель JSON)
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

import argparse
import pathlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from stepik_grader import downloader_config
from stepik_grader.atomic_io import atomic_write_json
from stepik_grader.core.attachments import download_attachments
from stepik_grader.core.diag_log import configure_diagnostics, get_logger
from stepik_grader.core.html_to_markdown import html_to_markdown
from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.core.oauth_flow import (
    OAuthCallbackPortBusy,
    StepikNetworkError,
    create_user_session,
    load_secrets_dict,
)
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
    fetch_submission_data,  # noqa: F401 — back-compat реэкспорт
    fetch_submission_history,
    fetch_unit_data,
)
from stepik_grader.core.submission_archive import archive_dir_for, save_submission_history

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
    extract_attachment_links,
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
from stepik_grader.core.tests_writer import backup_dir_for, reset_tests_dir, save_tests
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
from stepik_grader.stdio_encoding import force_utf8_stdio

__all__ = [
    "CONFIG_FILE",
    "build_task_directory",
    "main",
    "process_step_url",
    "run_cli",
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


def _report_tests_backup(tests_dir: pathlib.Path) -> None:
    """Освободить ``tests/`` под новый набор и сказать, что спасено (issue #951).

    ``reset_tests_dir`` переносит прежнее содержимое в ``tests.bak/`` вместо
    удаления, но молчащий перенос ничем не лучше молчащего удаления: студент
    видит, что его контрпримеров в ``tests/`` больше нет, и не знает, что они
    рядом. Число берётся из возврата функции — повторный сброс внутри
    ``save_tests`` найдёт каталог уже пустым и вернёт 0.
    """
    moved = reset_tests_dir(tests_dir)
    if moved:
        _print(_t("dl_tests_backed_up", count=moved, path=backup_dir_for(tests_dir)))


def _warn_if_stale_tests(task_dir: pathlib.Path) -> None:
    """Предупредить, что в ``tests/`` остались кейсы от прошлого скачивания.

    Очистка ``tests/`` (``reset_tests_dir``) выполняется только когда новый
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
    *,
    submissions: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Сохраняет рабочие файлы, solution.py, meta.json, task.html/task.md и tests/ в task_dir.

    Схема рабочих файлов:
      task{pos}_1.py  — основное решение (из template_code или пустая заглушка);
                        повторное скачивание НЕ перезаписывает непустой файл
                        (issue #554)
      task{pos}_2.py  — заглушка для альтернативного решения 1 (всегда создаётся)

    Args:
        submissions: вся история отправок по шагу (issue #1055). Раскладывается
            в ``submissions/`` рядом с задачей и ничего не затирает, в отличие
            от ``solution.py``, который держит только последнюю отправку.
            ``None`` — истории нет, каталог не создаётся.

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

    # issue #1055: solution.py держит ТОЛЬКО последнюю отправку и переписывается
    # при каждой перекачке шага — прошлые попытки, включая неверные, исчезали.
    # История уезжает в submissions/ и не затирается; имена там намеренно вне
    # маски решений, поэтому режимы 2-4 не примут старые попытки за конкурентов.
    archived = save_submission_history(task_dir, submissions or [])
    if archived:
        _print(
            _t(
                "dl_submissions_saved",
                count=len(archived),
                path=archive_dir_for(task_dir),
            )
        )

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
        # Сколько попыток по шагу сохранено в submissions/ (issue #1055): по
        # этому числу видно, есть ли у задачи история, не открывая каталог.
        "submissions_archived": len(archived),
        # Имя функции для function-mode runner в grader.py.
        # None если задача не является функциональной (stdin-режим).
        "function_name": function_name,
    }
    atomic_write_json(task_dir / "meta.json", meta)

    block: dict[str, Any] = step.get("block") or {}
    text = str(block.get("text", ""))
    if not text:
        _warn_if_stale_tests(task_dir)
        return 0, "none"

    # issue #1176: сырое тело шага — отдельным файлом. `task.md` теперь
    # производный: правила конвертации поменяются — пересоберём из `task.html`
    # локально, не перекачивая задачи по сети.
    (task_dir / "task.html").write_text(text, encoding="utf-8")

    # issue #1112: вложения условия качаются ДО тестов и независимо от них.
    # Решение открывает такой файл по имени из рабочего каталога, поэтому без
    # него задача не воспроизводится вовсе — и провал выглядит как ошибка
    # студента (`FileNotFoundError`), а не как недоскачанная задача.
    local_files = _save_attachments(task_dir, text, session, meta)

    # Порядок важен: вложения качаются раньше `task.md`, потому что ссылки в
    # нём переписываются на приехавшие файлы. Не приехавшее остаётся сетевой
    # ссылкой — обещать локальный файл, которого нет, хуже, чем ссылку в сеть.
    (task_dir / "task.md").write_text(html_to_markdown(text, local_files), encoding="utf-8")

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
        # issue #951: очистка каталога уносит и дописанные вручную кейсы. Отсюда
        # копия в tests.bak/ — и строка о ней: спасённое, о котором не сказали,
        # для пользователя неотличимо от потерянного. Повторный сброс внутри
        # save_tests найдёт каталог уже пустым и ничего не сделает.
        _report_tests_backup(task_dir / "tests")
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


def _save_attachments(
    task_dir: pathlib.Path,
    text: str,
    session: requests.Session,
    meta: dict[str, Any],
) -> dict[str, str]:
    """Скачать вложения условия и записать их состояние в ``meta.json`` (#1112).

    Отчёт кладётся в ``meta`` всегда, когда в условии есть ссылки: и про
    скачанные файлы, и про те, что не приехали. Иначе задача с файловым вводом
    выглядит как обычная, а её ``RE`` списывают на решение — ровно так дефект и
    дожил до прогона по реальной базе.

    Returns:
        Карта «URL -> имя файла рядом с задачей» только по **приехавшим**
        вложениям (issue #1176): по ней конвертер переписывает ссылки в
        ``task.md`` на локальные. Не приехавшее в карту не попадает и остаётся
        сетевой ссылкой.
    """
    links = extract_attachment_links(text)
    if not links:
        return {}

    report = download_attachments(task_dir, links, session)
    saved = [item for item in report if item["status"] in {"saved", "exists"}]
    failed = [item for item in report if item["status"] not in {"saved", "exists"}]

    meta["attachments"] = report
    atomic_write_json(task_dir / "meta.json", meta)

    if saved:
        _print(_t("dl_attachments_saved", count=len(saved)))
    for item in failed:
        _print(_t("dl_attachment_failed", name=item["name"] or item["url"]))

    return {str(item["url"]): str(item["name"]) for item in saved}


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
    # issue #1055: берём всю историю отправок, а не только последнюю. Стоимость
    # та же — страница API вмещает 20 записей, и у большинства шагов история в
    # неё укладывается, то есть остаётся один запрос.
    submissions = fetch_submission_history(session, step_id)
    submission = submissions[0] if submissions else None

    task_dir = build_task_directory(
        root_dir,
        course_title,
        section_title,
        lesson_title,
        step_position,
        step_title,
    )

    _print(_t("dl_saving_files", path=task_dir))
    count, source = save_task_files(
        task_dir,
        step,
        submission,
        lesson,
        section,
        course,
        session,
        submissions=submissions,
    )
    _log.info("тест-кейсы: %d шт., источник=%s (task_dir=%s)", count, source, task_dir)
    _print(_t("dl_step_saved", path=task_dir))
    return task_dir, count, source


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def _download(lang: str, urls: list[str] | None) -> tuple[list[pathlib.Path], int]:
    """Скачать задачи и вернуть пару «каталоги, код возврата» (issue #997).

    Код исхода нужен точке входа CLI: ``0`` — работа состоялась, ``1`` —
    загрузчик не дошёл до скачивания (конфиг или авторизация). Прежде обе
    ситуации давали пустой список и код ``0``, поэтому скрипт не отличал «шагов
    не заказывали» от «сломана авторизация» (INS-3-03, RUN-5-05).

    ``urls`` — список URL для неинтерактивного режима; ``None`` — спрашивать в
    цикле, как раньше.
    """
    set_lang(lang)
    downloader_config.set_lang(lang)
    configure_diagnostics()  # issue #146: opt-in по STEPIK_GRADER_LOG (по умолч. тихо)
    config_path = pathlib.Path(CONFIG_FILE)

    try:
        # issue #1109: с URL в аргументах мастер молчит — пользователь уже
        # сказал, чего хочет, и «Нужно изменить настройку?» тут только мешает.
        config = load_or_create_config(config_path, ask_to_change=urls is None)
        config = normalize_config_paths(config, config_path)
    except (EOFError, OSError) as error:
        # issue #1109: ввод недоступен (пайп, скрипт, CI, GUI-обёртка). Прежде
        # это приходило сюда общим `except Exception` и печаталось как «ошибка
        # работы с конфигом… исправьте файл или удалите его» — совет удалить
        # ИСПРАВНЫЙ файл, вместе с путём к базе задач в нём.
        _log.exception("нет интерактивного ввода для мастера конфигурации: %s", config_path)
        _print(_t("dl_config_needs_input", path=config_path.resolve(), error=error))
        return [], 1
    except Exception as error:
        # issue #831 (DEV-12): стек — в диагностический лог (opt-in, с редакцией
        # секретов). Пользователю остаётся короткое сообщение, но баг-репорт
        # приходит с местом падения, а не с одной строкой текста.
        _log.exception("сбой чтения конфигурации загрузчика: %s", config_path)
        # issue #997 (JRN-3A-01): раньше сообщение не называло файл, а мастер к
        # тому моменту уже был недоступен — пользователь оставался с «ошибка
        # работы с конфигом» и без единого следующего шага.
        _print(_t("dl_config_error_at", path=config_path.resolve(), error=error))
        return [], 1

    root_dir = pathlib.Path(str(config["root_dir"]))
    secrets_path = pathlib.Path(str(config["secrets_path"]))

    try:
        secrets = load_secrets_dict(secrets_path)
        session = create_user_session(secrets, secrets_path)
    except OAuthCallbackPortBusy as error:
        # issue #997 (JRN-3A-04): занятый порт колбэка приходил сюда обычным
        # OSError и получал совет «проверьте client_id / client_secret» —
        # пользователь правил заведомо исправные учётные данные.
        _log.exception("порт колбэка OAuth занят (secrets=%s)", secrets_path)
        _print(_t("dl_auth_port_busy", error=error))
        _print(_t("dl_auth_port_busy_hint"))
        _print(_t("dl_auth_diagnostics"))
        return [], 1
    except StepikNetworkError as error:
        # issue #997 (DEV-3-06): обрыв сети — не отказ авторизации.
        _log.exception("нет связи со Stepik (secrets=%s)", secrets_path)
        _print(_t("dl_auth_network", error=error))
        _print(_t("dl_auth_network_hint"))
        return [], 1
    except Exception as error:
        _log.exception("сбой авторизации Stepik (secrets=%s)", secrets_path)
        # issue #433: дружелюбная ошибка со следующим шагом, а не голый текст.
        _print(_t("dl_auth_failed", error=error))
        _print(_t("dl_auth_check_fields", url=STEPIK_OAUTH_APPS_URL))
        _print(_t("dl_auth_diagnostics"))
        _print(_t("dl_auth_docs"))
        return [], 1

    downloaded: list[pathlib.Path] = []
    failed = 0
    for step_url in urls if urls is not None else _iter_urls_interactively():
        try:
            task_dir, _, _ = process_step_url(step_url, session, root_dir)
        except Exception as error:
            _log.exception("сбой обработки шага: %s", step_url)
            _print(_t("dl_step_error", error=error))
            failed += 1
        else:
            downloaded.append(task_dir)
    # Отказ по конкретному шагу — тоже исход, а не «просто сообщение»: скрипт,
    # заказавший пять URL и получивший три, должен об этом узнать.
    return downloaded, 1 if failed else 0


def _iter_urls_interactively() -> Iterator[str]:
    """Спрашивать URL шагов до пустой строки; на EOF — штатное завершение.

    issue #997 (RUN-5-04): закрытый stdin ронял цикл трейсбеком ``EOFError``,
    хотя «ввода больше нет» — это ровно то же, что пустая строка. ``OSError``
    ловится наравне: подменённый поток даёт то одно, то другое.
    """
    _print(f"\n{_t('dl_enter_urls')}")
    while True:
        try:
            step_url = input(f"{_t('dl_step_url_prompt')}: ").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            print()
            return
        if not step_url:
            return
        yield step_url


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

    Контракт сохранён намеренно: функцию зовёт пункт 8 интерактивного меню и
    ждёт именно список каталогов. Код возврата нужен другой аудитории —
    процессу, — и живёт в ``run_cli`` (issue #997).
    """
    downloaded, _ = _download(lang, urls=None)
    return downloaded


def _build_parser() -> argparse.ArgumentParser:
    """Парсер аргументов загрузчика (issue #997: INS-5-01, RUN-4-06, TW-3-04).

    Модуль сразу уходил в мастер конфигурации, поэтому ``--help`` не печатал
    справку, а создавал ``stepik_config.json`` в текущем каталоге и падал на
    EOF — документированная команда не разбирала собственных флагов.
    """
    parser = argparse.ArgumentParser(
        prog="python -m stepik_grader.downloader",
        description=_t("dl_description"),
    )
    parser.add_argument("url", nargs="*", help=_t("dl_arg_url"))
    parser.add_argument("--lang", choices=["ru", "en"], default="ru", help=_t("dl_arg_lang"))
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    """Точка входа CLI загрузчика: код возврата вместо списка каталогов.

    ``0`` — все заказанные шаги скачаны (или пользователь завершил ввод),
    ``1`` — не удалось прочитать конфиг, авторизоваться или обработать шаг.
    """
    # issue #1108: до первой печати. Загрузчик показывает названия уроков из
    # Stepik, а они бывают с эмодзи — в консоли cp1251 это роняло процесс
    # раньше, чем пользователь видел хоть строку.
    force_utf8_stdio()
    args = _build_parser().parse_args(argv)
    _, code = _download(args.lang, urls=args.url or None)
    return code


if __name__ == "__main__":
    # issue #997 (INS-3-03, RUN-5-05): исход виден процессу, а не только глазам.
    raise SystemExit(run_cli())
