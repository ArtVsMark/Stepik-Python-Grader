"""cli/__init__.py — интерактивное меню и argparse CLI грейдера (режимы 0-4).

Архитектурный слой: Application / CLI.
Оркестрирует core.grader_core (загрузка/исполнение) и core.reporter
(вывод таблиц) — не содержит собственной бизнес-логики запуска решений.

Compatibility facade (issue #117/#119): `stepik_grader.cli` остаётся
единственной точкой доступа для entrypoint (`stepik-grader`), `grader.py`
и существующих monkeypatch-тестов. Парсинг/options helpers вынесены в
leaf-модуль `cli/options.py` и реэкспортированы здесь ниже — вызовы внутри
этого модуля (`main()` и др.) продолжают резолвить их как global-имена
`cli`-namespace, поэтому `monkeypatch.setattr(cli, "_build_arg_parser", ...)`
по-прежнему работает.

Non-interactive запуск (Sprint 8.1):
    python grader.py --mode 1 --file path/to/task.py
    python grader.py --mode 2 --dir StepikTasks/module1
    python grader.py --mode 3 --dir StepikTasks/module1/task1 --repeats 15
    python grader.py --mode 4 --dir StepikTasks/module1/task1 --number 1000
    python grader.py --version

Sprint E (issue #50/#51):
    --lang {ru,en}      — язык меню и сообщений (по умолчанию ru), issue #51 D-01
    --verbose / --quiet — управление подробностью вывода режимов 1/2, issue #50 D-03
    --output {text,json,csv,markdown} — машиночитаемый вывод, issues #50 D-04 / #53 / #58

Roadmap (issue #54):
    --watch — перезапускать --mode 1/2 при изменении файла решения

Без --mode main() показывает интерактивное меню (как раньше).

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 3).
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import io
import os
import pathlib
from collections.abc import Callable
from typing import Any

# issue #120: mode handlers — вынесены в leaf-модуль cli/commands.py; получают
# зависимости через CliContext (cli/context.py), а не читают module globals
# этого файла напрямую (см. docstring выше и _build_cli_context() ниже).
from stepik_grader.cli import commands
from stepik_grader.cli.context import CliContext

# issue #119: parsing/options helpers — вынесены в leaf-модуль cli/options.py,
# реэкспортированы здесь для backward compatibility фасада (см. docstring выше).
from stepik_grader.cli.options import (
    _build_arg_parser,
    _force_utf8_stdio,
    _resolve_use_cache,
    _resolve_verbosity,
)
from stepik_grader.core.cache import GraderCache
from stepik_grader.core.grader_core import (
    collect_grouped_files,
    find_all_solution_files,
    resolve_test_dir,
    run_benchmark,
    run_microbench_mode,
    run_tests,
)
from stepik_grader.core.i18n import load_locale_messages

__all__ = ["main"]


def _resolve_version() -> str:
    """Читает версию из package-метаданных (Issue #36 — единый источник:
    pyproject.toml). Не хардкодим строку здесь: importlib.metadata читает
    её из установленных package-метаданных (обновляются через `pip install
    -e .`, см. CONTRIBUTING.md). Fallback — для запуска без установки
    пакета (например, прямой git clone без `pip install -e .`), где
    package-метаданных ещё нет.
    """
    try:
        return importlib.metadata.version("stepik-python-grader")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()


def _is_dev_build(raw_version: str) -> bool:
    """True, если версия не соответствует чистому релизному тегу ``vX.Y.0``.

    setuptools-scm (``version_scheme = "post-release"``, issue #162) даёт
    ровно ``X.Y.0`` на точном теге и ``X.Y.0.postN+g<hash>`` (иногда с
    ``.dYYYYMMDD`` при "грязном" рабочем дереве) вне тега — наличие
    локального сегмента после ``+`` (PEP 440) однозначно значит dev-сборку.
    """
    return "+" in raw_version


def _format_version_for_display(raw_version: str) -> str:
    """Отформатировать версию для ``--version`` (issue #163).

    On-tag: версия не меняется — уже чистый ``X.Y.0``, без суффикса.
    Off-tag: та же строка setuptools-scm плюс явная dev-пометка, чтобы
    пользователь не принял ``X.Y.0.postN+g<hash>`` за официальный релиз.
    """
    if _is_dev_build(raw_version):
        return f"{raw_version} (dev build, not a release)"
    return raw_version


# ---------------------------------------------------------------------------
# i18n (issue #51 D-01) — русский по умолчанию, --lang en переключает на
# английский. Минимальный словарь сообщений вместо полноценной gettext-
# инфраструктуры: достаточно для меню/CLI этого масштаба.
#
# issue #144: локали также можно грузить из core/locales/<lang>.json — задел
# на будущее, чтобы новые сообщения добавлялись через JSON, не трогая
# _MESSAGES ниже (см. core/i18n.py и _t()).
# ---------------------------------------------------------------------------

_LANG: str = "ru"

_LOCALE_MESSAGES: dict[str, dict[str, str]] = {
    "ru": load_locale_messages("ru"),
    "en": load_locale_messages("en"),
}

_MESSAGES: dict[str, dict[str, str]] = {
    "bench_profile_header": {
        "ru": "  Профили нагрузки (повторов на решение):",
        "en": "  Load profiles (repeats per solution):",
    },
    "bench_profile_1": {
        "ru": "    1  низкий      —   5 повторов",
        "en": "    1  low       —   5 runs",
    },
    "bench_profile_2": {
        "ru": "    2  средний     —  15 повторов",
        "en": "    2  medium    —  15 runs",
    },
    "bench_profile_3": {
        "ru": "    3  высокий     —  50 повторов",
        "en": "    3  high      —  50 runs",
    },
    "bench_profile_4": {
        "ru": "    4  свой        —  5–100 повторов",
        "en": "    4  custom    —  5–100 runs",
    },
    "select_profile_prompt": {"ru": "  Выберите профиль [2]: ", "en": "  Select profile [2]: "},
    "enter_repeats_prompt": {
        "ru": "  Введите число повторов (5–100): ",
        "en": "  Enter repeats (5–100): ",
    },
    "micro_profile_header": {
        "ru": "  Профили нагрузки (вызовов за прогон):",
        "en": "  Load profiles (calls per run):",
    },
    "micro_profile_1": {"ru": "    1  быстрый      —     500", "en": "    1  fast      —     500"},
    "micro_profile_2": {"ru": "    2  обычный      —   1 000", "en": "    2  normal    —   1 000"},
    "micro_profile_3": {"ru": "    3  тщательный   —   5 000", "en": "    3  thorough  —   5 000"},
    "micro_profile_4": {"ru": "    4  глубокий     —  50 000", "en": "    4  deep      —  50 000"},
    "micro_profile_5": {
        "ru": "    5  тяжёлый      — 100 000  (только короткие детерминированные функции)",
        "en": "    5  hard      — 100 000  (short deterministic functions only)",
    },
    "micro_profile_6": {
        "ru": "    6  свой         — 100–500 000",
        "en": "    6  custom    — 100–500 000",
    },
    "enter_calls_prompt": {
        "ru": "  Введите число вызовов (100–500 000): ",
        "en": "  Enter calls (100–500 000): ",
    },
    "menu_title": {"ru": "  Stepik Python Grader", "en": "  Stepik Python Grader"},
    "menu_1": {"ru": "  1. Проверить одно решение", "en": "  1. Check one solution"},
    "menu_2": {
        "ru": "  2. Проверить все решения в папке",
        "en": "  2. Check all solutions in folder",
    },
    "menu_3": {"ru": "  3. Бенчмарк решений в папке", "en": "  3. Benchmark solutions in folder"},
    "menu_4": {
        "ru": "  4. Микро-бенчмарк (timeit) для папки",
        "en": "  4. Micro-benchmark (timeit) for folder",
    },
    "menu_0": {"ru": "  0. Выход", "en": "  0. Exit"},
    "select_mode_prompt": {"ru": "Выберите режим [0-4]: ", "en": "Select mode [0-4]: "},
    "goodbye": {"ru": "До свидания!", "en": "Goodbye!"},
    "enter_solution_path": {
        "ru": "Введите путь к файлу решения: ",
        "en": "Enter path to solution file: ",
    },
    "enter_folder_path": {"ru": "Введите путь к папке: ", "en": "Enter path to folder: "},
    "enter_folder_with_solutions_path": {
        "ru": "Введите путь к папке с решениями: ",
        "en": "Enter path to folder with solutions: ",
    },
    "unknown_choice": {
        "ru": "Неизвестный выбор. Введите число от 0 до 4.",
        "en": "Unknown choice. Please enter 0-4.",
    },
    "file_not_found": {"ru": "Файл не найден: {path}", "en": "File not found: {path}"},
    "dir_not_found": {"ru": "Папка не найдена: {path}", "en": "Directory not found: {path}"},
    "no_solutions_found": {"ru": "Файлы решений не найдены.", "en": "No solution files found."},
    # issue #50 D-05: richer diagnostic than a bare "not found" -- tells the
    # user WHERE a tests/ folder was expected and HOW to get one.
    "test_dir_not_found": {
        "ru": (
            "⚠️ Тесты не найдены для: {name}\n"
            "   Ожидалась папка: {expected}\n"
            "   Запустите: python -m stepik_grader.downloader\n"
            "   Или создайте вручную: tests/1, tests/1.clue"
        ),
        "en": (
            "⚠️ Tests not found for: {name}\n"
            "   Expected folder: {expected}\n"
            "   Run: python -m stepik_grader.downloader\n"
            "   Or create manually: tests/1, tests/1.clue"
        ),
    },
    "micro_bench_header": {
        "ru": "\n⚡ Микро-бенчмарк (timeit): {label}",
        "en": "\n⚡ Micro-bench (timeit): {label}",
    },
    "tests_not_found": {
        "ru": "  ⚠ Тесты не найдены: {test_dir}",
        "en": "  ⚠ Tests not found: {test_dir}",
    },
    "expected_tests_subfolder": {
        "ru": "  Ожидалась папка tests/ рядом с файлами решений.",
        "en": "  Expected: tests/ subfolder next to solution files.",
    },
    "no_test_cases_found": {
        "ru": "  ⚠ Тест-кейсы не найдены в: {test_dir}",
        "en": "  ⚠ No test cases found in: {test_dir}",
    },
    "no_results": {"ru": "  Нет результатов.", "en": "  No results."},
    # issue #66: колонка "Py-heap" в режиме 4 — это tracemalloc (пик Python-
    # объектов), а не RSS процесса; для function-блоков берётся RSS. Сноска
    # честно проговаривает смешанную методику.
    "micro_mem_note": {
        "ru": (
            "  * Py-heap — пик Python-heap (tracemalloc) для stdin-блоков / RSS "
            "для function-блоков;\n"
            "    tracemalloc не видит аллокации C-расширений (numpy и т.п.)."
        ),
        "en": (
            "  * Py-heap — peak Python-heap (tracemalloc) for stdin blocks / RSS "
            "for function blocks;\n"
            "    tracemalloc does not see C-extension allocations (numpy, etc.)."
        ),
    },
    # issue #56: opt-in кэш результатов (--cache / --clear-cache).
    "cache_hit": {
        "ru": "✅ Кэш актуален: тесты не перезапускались (0 изменений).",
        "en": "✅ Cache is up to date: no tests re-run (0 changes).",
    },
    "cache_summary": {
        "ru": "✅ Кэш: {hits} из {total} решений взято из кэша.",
        "en": "✅ Cache: {hits} of {total} solutions served from cache.",
    },
    "cache_cleared": {
        "ru": "🗑 Кэш очищен: удалено записей — {count}.",
        "en": "🗑 Cache cleared: {count} entries removed.",
    },
    "watch_dependency_missing": {
        "ru": ('--watch требует пакет watchfiles: pip install "stepik-grader[watch]"'),
        "en": ('--watch requires the watchfiles package: pip install "stepik-grader[watch]"'),
    },
    "watch_waiting": {
        "ru": "👀 Слежу за изменениями: {path} (Ctrl+C — остановить)",
        "en": "👀 Watching for changes: {path} (Ctrl+C to stop)",
    },
    # issue #79: заголовки нативного файлового диалога (tkinter).
    "dialog_pick_file": {
        "ru": "Выберите файл решения (.py)",
        "en": "Select solution file (.py)",
    },
    "dialog_pick_dir": {
        "ru": "Выберите папку с решениями",
        "en": "Select folder with solutions",
    },
    # эпик #80 Tier 2: генерация .vscode/tasks.json (--init-vscode).
    "vscode_written": {
        "ru": (
            "✅ VS Code задачи созданы: {path}\n"
            "   Запуск: Terminal → Run Task → «Stepik: …»,\n"
            "   либо Ctrl+Shift+B — «проверить текущий файл»."
        ),
        "en": (
            "✅ VS Code tasks created: {path}\n"
            "   Run: Terminal → Run Task → “Stepik: …”,\n"
            "   or Ctrl+Shift+B for “check current file”."
        ),
    },
    "vscode_exists": {
        "ru": (
            "⚠️ Файл уже существует: {path}\n"
            "   Удалите/переименуйте его, чтобы пересоздать, или добавьте задачи "
            "вручную (см. README)."
        ),
        "en": (
            "⚠️ File already exists: {path}\n"
            "   Delete/rename it to regenerate, or add the tasks manually "
            "(see README)."
        ),
    },
}


def _t(key: str, /, **kwargs: object) -> str:
    """Вернуть сообщение по ключу на текущем языке (_LANG), подставив kwargs.

    Сначала проверяет JSON-локаль (``core/locales/<lang>.json``, issue #144) —
    так новые сообщения можно добавлять через JSON, не трогая ``_MESSAGES``;
    при отсутствии ключа там — откат на статический ``_MESSAGES`` (issue #51 D-01).
    """
    template = _LOCALE_MESSAGES.get(_LANG, {}).get(key) or _MESSAGES[key][_LANG]
    return template.format(**kwargs) if kwargs else template


# ---------------------------------------------------------------------------
# Табличный вывод: csv/markdown (issues #53, #58)
# ---------------------------------------------------------------------------


def _rows_to_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """Отрендерить список flat-словарей в CSV-строку (заголовок + строки).

    Отсутствующие в конкретной строке ключи (например, "error" у успешных
    бенчмарк-строк) печатаются как пустая ячейка -- extrasaction="ignore"
    отбрасывает лишние ключи, не входящие в fieldnames.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _rows_to_markdown(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """Отрендерить список flat-словарей в Markdown-таблицу."""
    header = "| " + " | ".join(fieldnames) + " |"
    separator = "| " + " | ".join("---" for _ in fieldnames) + " |"
    lines = [header, separator]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(f, "")) for f in fieldnames) + " |")
    return "\n".join(lines)


def _print_tabular(output: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Напечатать rows как csv или markdown (output уже проверен вызывающей стороной)."""
    if output == "csv":
        print(_rows_to_csv(rows, fieldnames), end="")
    else:
        print(_rows_to_markdown(rows, fieldnames))


# ---------------------------------------------------------------------------
# Профили нагрузки
# ---------------------------------------------------------------------------

_BENCH_PROFILES: dict[str, int] = {
    "1": 5,
    "2": 15,
    "3": 50,
    "4": 0,
}

_MICRO_PROFILES: dict[str, int] = {
    "1": 500,
    "2": 1_000,
    "3": 5_000,
    "4": 50_000,
    "5": 100_000,
    "6": 0,
}


def _ask_bench_profile() -> int:
    """Запросить профиль нагрузки для subprocess-бенчмарка (режим 3)."""
    print(_t("bench_profile_header"))
    print(_t("bench_profile_1"))
    print(_t("bench_profile_2"))
    print(_t("bench_profile_3"))
    print(_t("bench_profile_4"))
    choice = input(_t("select_profile_prompt")).strip() or "2"
    repeats = _BENCH_PROFILES.get(choice)
    if repeats is None:
        repeats = _BENCH_PROFILES["2"]
    if repeats == 0:
        repeats = _ask_number(_t("enter_repeats_prompt"), default=15)
        repeats = max(5, min(100, repeats))
    return repeats


def _ask_micro_profile() -> int:
    """Запросить профиль нагрузки для timeit micro-bench (режим 4)."""
    print(_t("micro_profile_header"))
    print(_t("micro_profile_1"))
    print(_t("micro_profile_2"))
    print(_t("micro_profile_3"))
    print(_t("micro_profile_4"))
    print(_t("micro_profile_5"))
    print(_t("micro_profile_6"))
    choice = input(_t("select_profile_prompt")).strip() or "2"
    number = _MICRO_PROFILES.get(choice)
    if number is None:
        number = _MICRO_PROFILES["2"]
    if number == 0:
        number = _ask_number(_t("enter_calls_prompt"), default=1000)
        number = max(100, min(500_000, number))
    return number


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------


def _print_menu() -> None:
    print("\n" + "=" * 50)
    print(_t("menu_title"))
    print("=" * 50)
    print(_t("menu_1"))
    print(_t("menu_2"))
    print(_t("menu_3"))
    print(_t("menu_4"))
    print(_t("menu_0"))
    print("=" * 50)


def _ask_number(prompt: str, *, default: int) -> int:
    raw = input(prompt).strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _resolve_test_dir_from_input(solution_or_dir: str, *, is_dir: bool = False) -> str | None:
    if is_dir:
        p = pathlib.Path(solution_or_dir)
        # tests/ subdir takes priority
        candidate = p / "tests"
        if candidate.is_dir():
            return str(candidate)
        # Format 3: input.txt + output.txt directly in the given dir
        if (p / "input.txt").exists() and (p / "output.txt").exists():
            return str(p)
        # fallback: return as-is, load_test_cases will handle it
        return str(p)
    return resolve_test_dir(solution_or_dir)


def _build_cli_context() -> CliContext:
    """Собрать CliContext заново на каждый вызов (issue #120).

    Бар-имена ниже резолвятся в globals() этого модуля В МОМЕНТ ВЫЗОВА —
    то же late-binding, что уже используют _build_arg_parser/_print_tabular
    и т.д. (issue #119). Поэтому monkeypatch.setattr(cli, "run_tests", ...)
    и подобные, сделанные до cli.main(...)/_run_mode_N(...), по-прежнему
    долетают до handlers в cli/commands.py — контекст не кэшируется между
    вызовами, а строится из текущего состояния facade-namespace.
    """
    return CliContext(
        t=_t,
        run_tests=run_tests,
        run_benchmark=run_benchmark,
        run_microbench_mode=run_microbench_mode,
        resolve_test_dir_from_input=_resolve_test_dir_from_input,
        print_tabular=_print_tabular,
    )


def _run_mode_1(
    solution: str, *, verbose: bool = True, output: str = "text", use_cache: bool = False
) -> None:
    """Режим 1: проверить одно решение (verbose). Тонкая обёртка над commands._run_mode_1."""
    commands._run_mode_1(
        _build_cli_context(), solution, verbose=verbose, output=output, use_cache=use_cache
    )


def _run_mode_2(
    directory: str, *, verbose: bool = False, output: str = "text", use_cache: bool = False
) -> None:
    """Режим 2: проверить все решения в папке. Тонкая обёртка над commands._run_mode_2."""
    commands._run_mode_2(
        _build_cli_context(), directory, verbose=verbose, output=output, use_cache=use_cache
    )


def _run_mode_3(directory: str, repeats: int, *, output: str = "text") -> None:
    """Режим 3: subprocess-бенчмарк папки. Тонкая обёртка над commands._run_mode_3."""
    commands._run_mode_3(_build_cli_context(), directory, repeats, output=output)


def _run_mode_4(directory: str, number: int, *, output: str = "text") -> None:
    """Режим 4: timeit micro-bench папки. Тонкая обёртка над commands._run_mode_4."""
    commands._run_mode_4(_build_cli_context(), directory, number, output=output)


def _pick_path_via_dialog(*, want_dir: bool) -> str | None:
    """Открыть нативный диалог выбора файла (.py) или папки через tkinter.

    Возвращает выбранный путь или None (отмена, tkinter не установлен, либо
    headless-окружение без дисплея). Только fallback для интерактивного
    text-режима — вызывающая сторона обязана НЕ звать это при
    ``--output``/``--watch``/машинном контексте (issue #79).
    """
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None

    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        # tkinter установлен, но нет дисплея (headless Linux, урезанный Python).
        return None

    root.withdraw()
    try:
        if want_dir:
            path = filedialog.askdirectory(title=_t("dialog_pick_dir"))
        else:
            path = filedialog.askopenfilename(
                title=_t("dialog_pick_file"),
                filetypes=[("Python files", "*.py"), ("All files", "*.*")],
            )
    finally:
        root.destroy()
    return path or None


def _prompt_path(prompt_key: str, *, want_dir: bool) -> str:
    """Спросить путь в интерактивном меню; при пустом вводе — файловый диалог.

    Пустая строка на выходе (пустой ввод + отмена/недоступность диалога)
    корректно обрабатывается вызывающими режимами через их обычные
    "file/dir not found" сообщения (issue #79).
    """
    path = input(_t(prompt_key)).strip()
    if not path:
        path = _pick_path_via_dialog(want_dir=want_dir) or ""
    return path


def _resolve_cli_path_or_error(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    want_dir: bool,
    flag: str,
) -> str:
    """Вернуть путь для non-interactive режима, когда флаг пути не задан.

    В интерактивном text-режиме (без ``--output``-машинного вывода и без
    ``--watch``) предлагает нативный файловый диалог; иначе — или при отмене
    диалога / отсутствии tkinter — завершает работу через ``parser.error``
    (чистое сообщение argparse, не трейсбек). issue #79.
    """
    if args.output == "text" and not args.watch:
        picked = _pick_path_via_dialog(want_dir=want_dir)
        if picked:
            return picked
    parser.error(f"--mode {args.mode} requires {flag}")


def _interactive_menu() -> None:
    """Показать меню один раз, выполнить выбранный режим и завершить работу."""
    _print_menu()
    choice = input(_t("select_mode_prompt")).strip()

    if choice == "0":
        print(_t("goodbye"))
        return

    if choice == "1":
        solution = _prompt_path("enter_solution_path", want_dir=False)
        _run_mode_1(solution)

    elif choice == "2":
        directory = _prompt_path("enter_folder_path", want_dir=True)
        _run_mode_2(directory)

    elif choice == "3":
        directory = _prompt_path("enter_folder_path", want_dir=True)
        if not pathlib.Path(directory).is_dir():
            print(_t("dir_not_found", path=directory))
            return
        if not find_all_solution_files(directory):
            print(_t("no_solutions_found"))
            return
        repeats = _ask_bench_profile()
        _run_mode_3(directory, repeats)

    elif choice == "4":
        directory = _prompt_path("enter_folder_with_solutions_path", want_dir=True)
        if not pathlib.Path(directory).is_dir():
            print(_t("dir_not_found", path=directory))
            return
        if not collect_grouped_files(directory):
            print(_t("no_solutions_found"))
            return
        number = _ask_micro_profile()
        _run_mode_4(directory, number)

    else:
        print(_t("unknown_choice"))


def _watch_and_rerun(watch_path: str, rerun: Callable[[], None]) -> None:
    """Перезапускать rerun() при изменении файлов внутри watch_path (issue #54).

    watchfiles — опциональная зависимость (`pip install stepik-grader[watch]`);
    её отсутствие не должно ронять грейдер, если пользователь не просил --watch.
    Перезапускает ВЕСЬ вызов rerun() на любое изменение внутри watch_path —
    не отслеживает, какой именно файл изменился, для простоты и надёжности
    (в отличие от идеи "перезапускать только изменённый файл" из issue #54,
    которая для --mode 2 потребовала бы сопоставлять путь изменения с его
    собственной test_dir и печатать частичный результат отдельно).
    """
    try:
        from watchfiles import watch
    except ImportError:
        print(_t("watch_dependency_missing"))
        return

    rerun()
    print(_t("watch_waiting", path=watch_path))
    try:
        for _changes in watch(watch_path):
            os.system("cls" if os.name == "nt" else "clear")
            rerun()
            print(_t("watch_waiting", path=watch_path))
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> None:
    """Точка входа CLI: argparse для non-interactive режимов, иначе меню.

    python grader.py                                          — интерактивное меню
    python grader.py --version                                — версия и выход
    python grader.py --mode 1 --file path/to/task.py          — проверить один файл
    python grader.py --mode 2 --dir path/to/folder            — проверить папку
    python grader.py --mode 3 --dir path/to/folder --repeats 15  — бенчмарк
    python grader.py --mode 4 --dir path/to/folder --number 1000 — micro-bench
    python grader.py --mode 1 --file task.py --output json     — машиночитаемый вывод

    argv=None (по умолчанию) читает sys.argv[1:], как обычный CLI;
    явный список используется в тестах, чтобы не зависеть от sys.argv
    (который во время pytest содержит аргументы самого pytest).
    """
    _force_utf8_stdio()

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    global _LANG
    _LANG = args.lang

    if args.version:
        print(f"grader.py {_format_version_for_display(__version__)}")
        return

    if args.clear_cache:
        removed = GraderCache().clear()
        print(_t("cache_cleared", count=removed))
        return

    if args.init_vscode:
        from stepik_grader import ide

        written, path = ide.write_vscode_tasks()
        print(_t("vscode_written" if written else "vscode_exists", path=path))
        return

    if args.serve:
        # Ленивый импорт: http.server-стек тянем только когда реально нужен.
        from stepik_grader import web

        web.run_server(port=args.port)
        return

    if args.mode is None:
        _interactive_menu()
        return

    if args.mode == 1:
        if not args.file:
            args.file = _resolve_cli_path_or_error(parser, args, want_dir=False, flag="--file")
        verbose = _resolve_verbosity(args, default=True)
        # Режим 1 — один файл; инкрементальность (issue #71) неприменима,
        # поэтому кэш под --watch автоматически не включаем.
        use_cache = _resolve_use_cache(args, incremental=False)
        if args.watch:
            _watch_and_rerun(
                args.file,
                lambda: _run_mode_1(
                    args.file, verbose=verbose, output=args.output, use_cache=use_cache
                ),
            )
        else:
            _run_mode_1(args.file, verbose=verbose, output=args.output, use_cache=use_cache)
    elif args.mode == 2:
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        verbose = _resolve_verbosity(args, default=False)
        # issue #71: под --watch кэш включается по умолчанию — на событие
        # перезапускается только изменённый файл, остальные строки берутся из кэша.
        use_cache = _resolve_use_cache(args, incremental=args.watch)
        if args.watch:
            _watch_and_rerun(
                args.dir,
                lambda: _run_mode_2(
                    args.dir, verbose=verbose, output=args.output, use_cache=use_cache
                ),
            )
        else:
            _run_mode_2(args.dir, verbose=verbose, output=args.output, use_cache=use_cache)
    elif args.mode == 3:
        if args.watch:
            parser.error("--watch is only supported for --mode 1/2")
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        _run_mode_3(args.dir, args.repeats, output=args.output)
    elif args.mode == 4:
        if args.watch:
            parser.error("--watch is only supported for --mode 1/2")
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        _run_mode_4(args.dir, args.number, output=args.output)
