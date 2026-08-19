"""cli/__init__.py — интерактивное меню и argparse CLI грейдера (режимы 0-5).

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

Non-interactive запуск (обе формы равнозначны: console script после
`pip install` и запуск модулем из исходников):
    stepik-grader --mode 1 --file path/to/task.py
    python -m stepik_grader --mode 2 --dir StepikTasks/module1
    python -m stepik_grader --mode 3 --dir StepikTasks/module1/task1 --repeats 15
    python -m stepik_grader --mode 4 --dir StepikTasks/module1/task1 --number 1000
    python -m stepik_grader --version

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
import importlib.metadata
import json
import os
import pathlib
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # только для аннотаций: insights грузится лениво
    from stepik_grader.core.insights import InsightCard, TaskProgress

from stepik_grader import config

# issue #120: mode handlers — вынесены в leaf-модуль cli/commands.py; получают
# зависимости через CliContext (cli/context.py), а не читают module globals
# этого файла напрямую (см. docstring выше и _build_cli_context() ниже).
from stepik_grader.cli import commands, interactive
from stepik_grader.cli.context import CliContext
from stepik_grader.cli.exit_codes import ExitCode

# issue #121 Phase 2: интерактивное меню/профили — вынесены в leaf-модуль
# cli/interactive.py. _ask_number/_BENCH_PROFILES/_MICRO_PROFILES нигде не
# патчатся напрямую через cli.X — реэкспорт нужен только для facade-доступа
# (grader.py импортирует _BENCH_PROFILES/_MICRO_PROFILES) и обратной
# совместимости.
from stepik_grader.cli.interactive import (  # noqa: F401
    _BENCH_PROFILES,
    _MICRO_PROFILES,
    _ask_number,
)

# issue #119: parsing/options helpers — вынесены в leaf-модуль cli/options.py,
# реэкспортированы здесь для backward compatibility фасада (см. docstring выше).
from stepik_grader.cli.options import (
    _build_arg_parser,
    _force_utf8_stdio,
    _resolve_record_history,
    _resolve_record_stats,
    _resolve_use_cache,
    _resolve_verbosity,
    apply_launch_profile,
    peek_lang,
    validate_output_format,
    validate_serve_arguments,
)
from stepik_grader.cli.prompts import EXPLICIT_YES

# issue #121 Phase 1: pure rendering helpers — вынесены в leaf-модуль
# cli/rendering.py, реэкспортированы здесь для backward compatibility фасада.
# _rows_to_csv/_rows_to_markdown не используются напрямую в этом файле (только
# внутри rendering.py собственным _print_tabular) — реэкспорт нужен только для
# facade-доступа (cli._rows_to_csv), которым пользуются тесты.
from stepik_grader.cli.rendering import (  # noqa: F401
    _print_tabular,
    _rows_to_csv,
    _rows_to_markdown,
)
from stepik_grader.config import CONFIG
from stepik_grader.core import stats
from stepik_grader.core.cache import GraderCache
from stepik_grader.core.diag_log import configure_diagnostics
from stepik_grader.core.grader_core import (
    preflight_solution,
    resolve_test_dir,
    run_benchmark,
    run_microbench_mode,
    run_tests,
    set_runner,
)
from stepik_grader.core.history import PurgePreview
from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.core.reporter import (
    print_insights_summary,
    print_progress_summary,
    print_stats_summary,
)
from stepik_grader.core.settings_resolver import apply_user_run_settings

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
# i18n (issue #51 D-01, #144, #355) — русский по умолчанию, --lang en
# переключает на английский. Единый каталог сообщений — JSON-локали
# core/locales/<lang>.json (грузятся через core/i18n.load_locale_messages);
# _t() — тонкая обёртка над ними. Парность ru/en стережёт guardrail
# scripts/check_locale_guardrails.py. Раньше здесь жил параллельный
# захардкоженный словарь _MESSAGES — он слит в JSON (issue #355).
# ---------------------------------------------------------------------------

_LANG: str = "ru"

_LOCALE_MESSAGES: dict[str, dict[str, str]] = {
    "ru": load_locale_messages("ru"),
    "en": load_locale_messages("en"),
}


def _t(key: str, /, **kwargs: object) -> str:
    """Вернуть сообщение по ключу на текущем языке (``_LANG``), подставив kwargs.

    Тонкая обёртка над JSON-локалями (``core/locales/<lang>.json``, единый
    каталог после issue #355): читает шаблон и подставляет ``kwargs``.
    ``KeyError`` при отсутствии ключа — программная ошибка (ключ обязан быть в
    обеих локалях, это стережёт ``scripts/check_locale_guardrails.py``).
    """
    template = _LOCALE_MESSAGES[_LANG][key]
    return template.format(**kwargs) if kwargs else template


# issue #824 (DESC-04): подписи табличных отчётов собираются здесь и передаются в
# reporter готовыми строками — сам reporter про локали не знает и остаётся leaf'ом.
# Единицы длительности («42с») намеренно не сюда: у них уже есть ключи
# progress_export_unit_*, общие с HTML-экспортом прогресса (issue #823).
INSIGHTS_SCHEMA = "stepik-grader/insights/1"


#: Схема машинного вывода `stats --output json` (issue #1192). Версия отдельная
#: от INSIGHTS_SCHEMA: это другой набор полей, и потребители у них разные.
STATS_SCHEMA = 1


def _stats_payload(summary: dict[str, Any], queue: dict[str, int]) -> dict[str, Any]:
    """Сводка `stats` в машинном виде (issue #1192).

    Пустая история — не ошибка, а объект с нулями: скрипту так проще, чем
    отличать «данных нет» от «команда упала». Тот же принцип, что у
    :func:`_insights_payload`.
    """
    return {
        "schema": STATS_SCHEMA,
        "total_runs": summary["total_runs"],
        "total_time": summary["total_time"],
        "avg_time": summary["avg_time"],
        "by_mode": summary["by_mode"],
        "by_os": summary["by_os"],
        "verdict_totals": summary["verdict_totals"],
        "skipped": summary["skipped"],
        "cache_queue": queue,
    }


def _cache_queue_summary() -> dict[str, int]:
    """Очередь задач из кэша; при недоступном кэше — нули, а не отказ.

    Кэш опционален и регенерируем: его отсутствие или порча не повод ронять
    сводку, ради которой пользователь и позвал команду.
    """
    from stepik_grader.core.cache import GraderCache

    try:
        return GraderCache().queue_summary()
    except OSError:
        return {"tasks": 0, "stale": 0}


def _run_stats_command(args: argparse.Namespace) -> ExitCode:
    """`stepik-grader stats` — статистика обучения одной командой (issue #1192).

    Собирает то, что раньше приходилось доставать четырьмя разными флагами:
    сводку прогонов (`--stats-summary`) и отчёт о прогрессе
    (`--export-progress`). Флаги остались рабочими — здесь только один вход к
    тем же данным.

    ``--output md|html`` пишет файл ровно так же, как `--export-progress`:
    формат отчёта общий, дублировать рендеринг незачем.
    """
    from stepik_grader.core import progress_export
    from stepik_grader.core.history_recording import default_history_db_path

    fmt = args.output
    summary = stats.read_summary()
    queue = _cache_queue_summary()

    if fmt == "json":
        print(json.dumps(_stats_payload(summary, queue), ensure_ascii=False, indent=2))
        return ExitCode.OK

    if fmt in ("markdown", "html"):
        report = progress_export.build_progress_report(default_history_db_path())
        if report["total_runs"] == 0:
            print(_t("insights_no_data"))
            return ExitCode.OK
        suffix = "md" if fmt == "markdown" else "html"
        rendered = (
            progress_export.render_markdown(report, lang=_LANG)
            if fmt == "markdown"
            else progress_export.render_html(report, lang=_LANG)
        )
        out = pathlib.Path.cwd() / f"grader-progress.{suffix}"
        out.write_text(rendered, encoding="utf-8")
        print(_t("stats_report_written", path=out))
        return ExitCode.OK

    if summary["total_runs"] == 0:
        # Та же развилка, что у `--stats-summary` (issue #1005): битый журнал и
        # пустой журнал — разные причины с разными действиями.
        skipped = int(summary.get("skipped", 0))
        if skipped:
            print(_t("stats_all_entries_broken", skipped=skipped, path=stats.stats_path()))
        else:
            print(_t("stats_no_data"))
    else:
        print_stats_summary(summary)

    cache_line = (
        _t("stats_cache_queue_stale", tasks=queue["tasks"], stale=queue["stale"])
        if queue["stale"]
        else str(queue["tasks"])
    )
    print(f"{_t('stats_cache_queue')}: {cache_line}")
    return ExitCode.OK


def _insights_payload(
    cards: list[InsightCard],
    progress: list[TaskProgress],
) -> dict[str, object]:
    """Сводка «Подучить» в машинном виде (issue #997, VIS-1-03).

    Схема версионирована (``schema``) — потребитель может отличить формат от
    будущих изменений, как это уже сделано для экспорта прогресса. Пустая
    история — не ошибка, а объект с пустыми списками: скрипту так проще, чем
    отличать «данных нет» от «команда упала».
    """
    return {
        "schema": INSIGHTS_SCHEMA,
        "cards": [
            {
                "key": card.key,
                "category": card.category,
                "status": card.status,
                "hits": card.hits,
                "runs_considered": card.runs_considered,
                "glossary_id": card.glossary_id,
            }
            for card in cards
        ],
        "tasks": [
            {
                "task_key": item.task_key,
                "attempts": item.attempts,
                "solved": item.solved,
                "seconds_to_first_ac": item.seconds_to_first_ac,
            }
            for item in progress
        ],
    }


def _insights_labels() -> dict[str, str]:
    """Подписи таблицы «Подучить» на текущем языке."""
    return {
        "title": _t("insights_table_title"),
        "col_key": _t("insights_col_key"),
        "col_status": _t("insights_col_status"),
        "col_seen": _t("insights_col_seen"),
        "col_link": _t("insights_col_link"),
        "status_active": _t("insights_status_active"),
        "status_fading": _t("insights_status_fading"),
        "status_watch": _t("insights_status_watch"),
        "status_archived": _t("insights_status_archived"),
    }


def _progress_labels() -> dict[str, str]:
    """Подписи таблицы «Прогресс» на текущем языке."""
    return {
        "title": _t("progress_table_title"),
        "col_task": _t("progress_col_task"),
        "col_solved": _t("progress_col_solved"),
        "col_attempts": _t("progress_col_attempts"),
        "col_time": _t("progress_col_time"),
        "no_task": _t("progress_export_no_task"),
        "unit_sec": _t("progress_export_unit_sec"),
        "unit_min": _t("progress_export_unit_min"),
        "unit_hour": _t("progress_export_unit_hour"),
    }


def _lint_labels() -> dict[str, str]:
    """Подписи блока «Стиль» на текущем языке."""
    return {"title": _t("lint_block_title"), "lines": _t("lint_lines_label")}


# ---------------------------------------------------------------------------
# Интерактивное меню / профили нагрузки — issue #121 Phase 2.
#
# Реализация в cli/interactive.py; тонкие обёртки ниже сохраняют публичные
# сигнатуры и facade-имена для существующих monkeypatch-тестов и re-export
# в grader.py (см. docstring cli/interactive.py). _ask_number/_BENCH_PROFILES/
# _MICRO_PROFILES нигде не патчатся напрямую через cli.X — просто реэкспорт.
# ---------------------------------------------------------------------------


def _ask_bench_profile() -> int:
    """Запросить профиль нагрузки для subprocess-бенчмарка (режим 3)."""
    return interactive._ask_bench_profile(_build_cli_context())


def _ask_micro_profile() -> int:
    """Запросить профиль нагрузки для timeit micro-bench (режим 4)."""
    return interactive._ask_micro_profile(_build_cli_context())


def _print_menu() -> None:
    interactive._print_menu(_build_cli_context())


def _resolve_test_dir_from_input(
    solution_or_dir: pathlib.Path, *, is_dir: bool = False
) -> pathlib.Path | None:
    if is_dir:
        p = solution_or_dir
        # tests/ subdir takes priority
        candidate = p / "tests"
        if candidate.is_dir():
            return candidate
        # Format 3: input.txt + output.txt directly in the given dir
        if (p / "input.txt").exists() and (p / "output.txt").exists():
            return p
        # fallback: return as-is, load_test_cases will handle it
        return p
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
        preflight_solution=preflight_solution,
        run_microbench_mode=run_microbench_mode,
        resolve_test_dir_from_input=_resolve_test_dir_from_input,
        print_tabular=_print_tabular,
        pick_path_via_dialog=_pick_path_via_dialog,
        ask_bench_profile=_ask_bench_profile,
        ask_micro_profile=_ask_micro_profile,
        run_mode_1=_run_mode_1,
        run_mode_2=_run_mode_2,
        run_mode_3=_run_mode_3,
        run_mode_4=_run_mode_4,
        lang=_LANG,
    )


def _run_mode_1(
    solution: pathlib.Path,
    *,
    verbose: bool = True,
    output: str = "text",
    use_cache: bool = False,
    record_stats: bool = False,
    record_history: bool = False,
    record_lint: bool = False,
    ai_hints: bool = False,
) -> ExitCode:
    """Режим 1: проверить одно решение (verbose). Тонкая обёртка над commands._run_mode_1."""
    return commands._run_mode_1(
        _build_cli_context(),
        solution,
        verbose=verbose,
        output=output,
        use_cache=use_cache,
        record_stats=record_stats,
        record_history=record_history,
        record_lint=record_lint,
        ai_hints=ai_hints,
    )


def _run_mode_2(
    directory: pathlib.Path,
    *,
    verbose: bool = False,
    output: str = "text",
    use_cache: bool = False,
    record_stats: bool = False,
    record_history: bool = False,
    record_lint: bool = False,
    ai_hints: bool = False,
) -> ExitCode:
    """Режим 2: проверить все решения в папке. Тонкая обёртка над commands._run_mode_2."""
    return commands._run_mode_2(
        _build_cli_context(),
        directory,
        verbose=verbose,
        output=output,
        use_cache=use_cache,
        record_stats=record_stats,
        record_history=record_history,
        record_lint=record_lint,
        ai_hints=ai_hints,
    )


def _run_mode_3(
    directory: pathlib.Path,
    repeats: int,
    *,
    output: str = "text",
    record_stats: bool = False,
    record_history: bool = False,
    ai_hints: bool = False,
) -> None:
    """Режим 3: subprocess-бенчмарк папки. Тонкая обёртка над commands._run_mode_3."""
    commands._run_mode_3(
        _build_cli_context(),
        directory,
        repeats,
        output=output,
        record_stats=record_stats,
        record_history=record_history,
        ai_hints=ai_hints,
    )


def _run_mode_4(
    directory: pathlib.Path,
    number: int,
    *,
    output: str = "text",
    record_stats: bool = False,
    record_history: bool = False,
    ai_hints: bool = False,
) -> None:
    """Режим 4: timeit micro-bench папки. Тонкая обёртка над commands._run_mode_4."""
    commands._run_mode_4(
        _build_cli_context(),
        directory,
        number,
        output=output,
        record_stats=record_stats,
        record_history=record_history,
        ai_hints=ai_hints,
    )


def _pick_path_via_dialog(*, want_dir: bool) -> pathlib.Path | None:
    """Открыть нативный диалог выбора файла (.py) или папки через tkinter.

    Тонкая обёртка над interactive._pick_path_via_dialog (issue #121 Phase 2).
    """
    return interactive._pick_path_via_dialog(_build_cli_context(), want_dir=want_dir)


def _prompt_path(prompt_key: str, *, want_dir: bool) -> pathlib.Path:
    """Спросить путь в интерактивном меню. Тонкая обёртка над interactive._prompt_path."""
    return interactive._prompt_path(_build_cli_context(), prompt_key, want_dir=want_dir)


def _resolve_cli_path_or_error(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    want_dir: bool,
    flag: str,
) -> pathlib.Path:
    """Путь для non-interactive режима без флага. Тонкая обёртка над
    interactive._resolve_cli_path_or_error."""
    return interactive._resolve_cli_path_or_error(
        _build_cli_context(), parser, args, want_dir=want_dir, flag=flag
    )


def _interactive_menu() -> None:
    """Цикл интерактивного меню до «0»/EOF (issue #445). Тонкая обёртка над
    interactive._interactive_menu."""
    interactive._interactive_menu(_build_cli_context())


def _watch_and_rerun(watch_path: pathlib.Path, rerun: Callable[[], object]) -> None:
    """Перезапускать rerun() при изменении файлов внутри watch_path (issue #54).

    watchfiles — опциональная зависимость (`pip install stepik-python-grader[watch]`);
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


def _dispatch_with_watch(
    target: pathlib.Path, run: Callable[[], ExitCode], *, watch: bool
) -> ExitCode:
    """Запустить ``run`` один раз или, под ``--watch``, перезапускать при
    изменениях ``target``.

    issue #354 — общий раннер вместо двух почти одинаковых watch/no-watch
    веток в режимах 1 и 2.

    issue #936: возвращает код исхода одиночного прогона. Под ``--watch`` кода
    нет по сути — цикл живёт до Ctrl+C, и «итога» у него не бывает; такой
    запуск завершается ``OK``.
    """
    if watch:
        _watch_and_rerun(target, run)
        return ExitCode.OK
    return run()


def _confirm_purge(preview: PurgePreview, task_key: str | None) -> bool:
    """Показать объём удаления истории и спросить подтверждение (issue #990).

    ``True`` — можно удалять. Удалять нечего (пустая или отсутствующая база) —
    тоже ``True``: спрашивать не о чем, а сообщение о нуле удалённых напечатает
    сам вызывающий.

    Подтверждение спрашивается только в интерактивной сессии. Без TTY (CI,
    пайп) вопрос не задаётся: зависший на вводе скрипт хуже, чем отсутствие
    подтверждения, — но объём всё равно печатается, чтобы он остался в логе.
    """
    runs = preview["runs"]
    if runs == 0:
        return True
    tasks = preview["tasks"]
    solutions = preview["solutions"]
    print(
        _t(
            "history_purge_preview",
            runs=runs,
            tasks=", ".join(tasks) or "—",
            solutions=", ".join(solutions) or "—",
        )
    )
    # Совпадение ключей — не гипотеза, а текущее поведение: ключ задачи равен
    # имени папки, поэтому точечное удаление может задеть одноимённую задачу
    # другого курса. Пользователь должен узнать об этом до, а не после.
    if task_key is not None and len(tasks) > 1:
        print(_t("history_purge_shared_key", task=task_key))
    if not sys.stdin.isatty():
        return True
    try:
        answer = input(_t("history_purge_confirm")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in EXPLICIT_YES


def main(argv: list[str] | None = None) -> ExitCode:
    """Точка входа CLI: argparse для non-interactive режимов, иначе меню.

    stepik-grader                                             — интерактивное меню
    stepik-grader --version                                   — версия и выход
    stepik-grader --mode 1 --file path/to/task.py             — проверить один файл
    stepik-grader --mode 2 --dir path/to/folder               — проверить папку
    stepik-grader --mode 3 --dir path/to/folder --repeats 15  — бенчмарк
    stepik-grader --mode 4 --dir path/to/folder --number 1000 — micro-bench
    stepik-grader --mode 1 --file task.py --output json       — машиночитаемый вывод

    argv=None (по умолчанию) читает sys.argv[1:], как обычный CLI;
    явный список используется в тестах, чтобы не зависеть от sys.argv
    (который во время pytest содержит аргументы самого pytest).
    """
    _force_utf8_stdio()

    # issue #997 (INS-5-03): язык нужен ДО сборки парсера — иначе тексты справки
    # уже зафиксированы, и `--lang en --help` печатает русскую справку, то есть
    # самая первая поверхность игнорирует выбор языка.
    parser = _build_arg_parser(peek_lang(argv))
    args = parser.parse_args(argv)

    # issue #993: источник конфигурации фиксируется до всего остального —
    # вердикт не должен зависеть от того, что лежит в родительских каталогах.
    # Несуществующий путь — ошибка разбора аргументов, а не тихий откат на
    # автопоиск: пользователь просил конкретный файл.
    if args.config is not None:
        if not args.config.is_file():
            parser.error(f"--config: файл не найден — {args.config}")
        config.set_config_path(args.config)
    # issue #984: один корень настроек для CLI и веба. Без --root корень
    # резолвится от рабочей папки вверх до границы проекта.
    if args.root is not None:
        config.set_workspace_root(args.root)

    # issue #1133 (шаг 2): именованный профиль применяется ПОСЛЕ --root (файл
    # настроек читается из выбранного корня) и ДО всего остального — дальше
    # аргументы разъезжаются по конфигу и веб-серверу, и профиль, применённый
    # позже, действовал бы наполовину.
    apply_launch_profile(args, argv, parser)
    # issue #930: проверяем ПОСЛЕ профиля — порт мог приехать из него, и
    # ругаться на значение, которого пользователь не писал, но выбрал
    # профилем, всё равно правильно: сервер с ним не поднимется.
    validate_output_format(args, parser)
    validate_serve_arguments(args, argv, parser)

    # issue #1136: настройки, выбранные во вкладке «Дополнительно», ложатся
    # поверх pyproject.toml — и ДО флагов ниже, потому что флаг обязан
    # перекрывать сохранённое: разовое `--timeout 30` иначе проигрывало бы
    # значению, выбранному месяц назад.
    rejected = apply_user_run_settings()
    if rejected:
        # Настройка, которая «не сработала» без единого слова, неотличима от
        # неработающей функции — поэтому отброшенное называется вслух.
        print(_t("user_settings_rejected", names=", ".join(rejected)))

    # issue #997 (SET-3-03): лимиты правились только в pyproject.toml, которого
    # у pipx-установки нет. Применяем ДО диспетчеризации — прогон, паспорт
    # условий и ключ кэша должны видеть одно и то же значение.
    overrides: dict[str, object] = {}
    if args.timeout is not None:
        if args.timeout <= 0:
            parser.error(f"--timeout: ожидалось положительное число секунд — {args.timeout}")
        overrides["timeout_seconds"] = args.timeout
    if args.memory_limit is not None:
        if args.memory_limit < 0:
            parser.error(f"--memory-limit: ожидалось 0 или больше — {args.memory_limit}")
        # 0 — «снять лимит»: в конфиге это None, а не ноль мегабайт.
        overrides["max_memory_mb"] = args.memory_limit or None
    # issue #1111: режим сравнения вывода на один прогон. Значения проверяет
    # argparse (`choices`), поэтому здесь только перенос в конфиг.
    if args.compare is not None:
        overrides["compare_mode"] = args.compare
    if overrides:
        config.override_config(**overrides)

    # issue #146: opt-in диагностический лог. --diagnostic → debug; иначе уровень
    # берётся из STEPIK_GRADER_LOG (по умолчанию выключено, файл не создаётся).
    configure_diagnostics("debug" if args.diagnostic else None)

    global _LANG
    _LANG = args.lang

    if args.version:
        # Имя дистрибутива, а не файла: `grader.py` при src-layout не существует
        # ни как команда, ни как файл в корне (issue #820).
        print(f"stepik-grader {_format_version_for_display(__version__)}")
        return ExitCode.OK

    if args.clear_cache:
        # issue #816 (DEV-11): чистим ОБА кэша. Раньше флаг трогал только
        # `.grader_cache` (результаты прогонов), а `.stepik_cache` (ответы API)
        # оставался расти — при том что именно он прибавляет файл на каждую
        # новую скачанную задачу и не чистится ничем, включая TTL.
        from stepik_grader.core.stepik_client import clear_cache as clear_stepik_cache

        removed = GraderCache().clear() + clear_stepik_cache()
        print(_t("cache_cleared", count=removed))
        return ExitCode.OK

    if args.revoke_ai_consent:
        # issue #812 (SECD-06): отозвать согласие было нечем — только правкой
        # .grader_settings.json руками. Согласие на передачу данных, которое
        # нельзя отозвать, согласием не является.
        from stepik_grader.cli.commands import revoke_ai_consent

        print(_t("ai_consent_revoked" if revoke_ai_consent() else "ai_consent_absent"))
        return ExitCode.OK

    if args.purge_history is not None:
        # issue #813 (SECD-03): у локального журнала обучения должен быть
        # штатный способ удаления. Раньше `--no-history` лишь переставал писать,
        # а убрать уже накопленное можно было только `rm .grader_history.db`
        # (плюс -wal/-shm) — то есть зная о файлах, которых пользователь не
        # создавал. Без аргумента чистим и статистику: это те же личные данные.
        from stepik_grader.core import stats as stats_mod
        from stepik_grader.core.history import preview_purge, purge_history
        from stepik_grader.core.history_recording import default_history_db_path

        task_key = args.purge_history or None
        db_path = default_history_db_path()
        # issue #990: удаление необратимо, а ключ задачи сейчас совпадает у
        # одноимённых папок разных курсов — «своя» задача утаскивает чужую.
        # Поэтому объём показывается ДО удаления, а в интерактивной сессии
        # спрашивается подтверждение. Без TTY (CI, скрипт) вопрос не задаётся:
        # зависший на вводе пайплайн хуже, чем отсутствие подтверждения.
        if not _confirm_purge(preview_purge(db_path, task_key=task_key), task_key):
            print(_t("history_purge_cancelled"))
            # Отказ пользователя — не сбой команды: данные целы, как он и просил.
            return ExitCode.OK
        runs_removed = purge_history(db_path, task_key=task_key)
        if task_key is None:
            stats_removed = stats_mod.purge_stats()
            print(_t("history_purged", runs=runs_removed, stats=stats_removed))
        else:
            print(_t("history_purged_task", task=task_key, runs=runs_removed))
        return ExitCode.OK

    if args.create_shortcut:
        # issue #1185: ярлык — явное действие пользователя, поэтому отдельная
        # ветка, а не побочный эффект какой-нибудь другой команды.
        from stepik_grader.core.shortcut import ShortcutError, create_shortcut

        try:
            print(_t("shortcut_created", path=create_shortcut()))
        except ShortcutError as exc:
            # Причина показывается человеку целиком: «не получилось» без
            # объяснения оставляет пользователя ровно там же, где он был.
            print(_t("shortcut_failed", error=exc))
            return ExitCode.FAILURES
        return ExitCode.OK

    # issue #1192: одна точка входа вместо россыпи флагов. Прежние флаги
    # остаются рабочими — CLI-поверхность в проекте обратно совместима, — но
    # чтобы понять, какой из них что показывает, приходилось читать справку
    # целиком.
    if args.command == "stats":
        return _run_stats_command(args)

    if args.stats_summary:
        summary = stats.read_summary()
        if summary["total_runs"] == 0:
            # issue #1005 (FZZ-5-06): битый журнал и пустой журнал — разные
            # причины с разными действиями. Прежнее общее «статистика выключена
            # или ещё не накопилась» называло причину, которой нет: записи были,
            # их не удалось прочитать, и чинится это удалением файла — о котором
            # молчали.
            skipped = int(summary.get("skipped", 0))
            if skipped:
                print(_t("stats_all_entries_broken", skipped=skipped, path=stats.stats_path()))
            else:
                print(_t("stats_no_data"))
        else:
            print_stats_summary(summary)
        return ExitCode.OK

    if args.export_progress:
        from stepik_grader.core import progress_export
        from stepik_grader.core.history_recording import default_history_db_path

        db_path = default_history_db_path()  # issue #818
        report = progress_export.build_progress_report(db_path)
        if report["total_runs"] == 0:
            # issue #997: под json пустая история — тоже JSON, иначе скрипт
            # получает русскую прозу там, где ждёт объект.
            if args.export_progress == "json":
                print(json.dumps({"reason": "no_history_data", **report}, ensure_ascii=False))
            else:
                print(_t("insights_no_data"))  # дружелюбно, не ошибка (issue #432)
            return ExitCode.OK
        fmt = args.export_progress
        # issue #997 (COM-1-07): json — машинный формат со стабильной схемой
        # (`schema`, `tasks[].task_key/attempts/failure_kinds`). Прежде экспорт
        # умел только md/html: чтобы свести прогресс по группе, преподавателю
        # пришлось бы разбирать вёрстку отчёта.
        if fmt == "json":
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        else:
            # issue #821: отчёт следует выбранному языку — раньше он всегда выходил
            # русским, включая атрибут <html lang>, даже под `--lang en`.
            rendered = (
                progress_export.render_markdown(report, lang=_LANG)
                if fmt == "md"
                else progress_export.render_html(report, lang=_LANG)
            )
        out = pathlib.Path.cwd() / f"grader-progress.{fmt}"
        out.write_text(rendered, encoding="utf-8")
        print(_t("progress_exported", path=out))
        return ExitCode.OK

    if args.insights:
        from stepik_grader import rules
        from stepik_grader.core import insights
        from stepik_grader.core.history_recording import default_history_db_path

        db_path = default_history_db_path()  # issue #818
        cards = insights.learning_cards(
            db_path,
            n=CONFIG.insights_window_n,
            t=CONFIG.insights_active_threshold_t,
            k=CONFIG.insights_clean_streak_k,
        )
        progress = insights.time_to_first_green(db_path)  # issue #431: TTFG
        # issue #997 (VIS-1-03): --insights молча игнорировал --output, и
        # единственным способом забрать сводку скриптом был парсинг таблиц rich.
        if args.output == "json":
            print(json.dumps(_insights_payload(cards, progress), ensure_ascii=False, indent=2))
            return ExitCode.OK
        if not cards and not progress:
            print(_t("insights_no_data"))
        else:
            if progress:
                print_progress_summary(progress, labels=_progress_labels())
            if cards:
                print_insights_summary(
                    cards, rules_provider=rules.bundled_rules(), labels=_insights_labels()
                )
        return ExitCode.OK

    if args.init_vscode:
        from stepik_grader import ide

        written, path = ide.write_vscode_tasks()
        print(_t("vscode_written" if written else "vscode_exists", path=path))
        return ExitCode.OK

    if args.import_reference:
        # issue #55: закреплённое решение Stepik + топовые как task{N}_{100+}.py.
        import requests

        from stepik_grader.core.stepik_reference import import_references_from_task_dir

        try:
            saved = import_references_from_task_dir(args.import_reference, max_top=args.import_top)
        except (FileNotFoundError, ValueError, OSError, requests.RequestException) as exc:
            print(f"❌ Импорт reference не удался: {exc}")
            raise SystemExit(1) from exc
        print(f"✅ Импортировано reference-решений: {len(saved)}")
        for saved_path in saved:
            print(f"   {saved_path.name}")
        return ExitCode.OK

    if args.serve:
        # issue #396: --sandbox теперь проброшен в web — run_server ставит
        # SandboxRunner активным _RUNNER, изолируя все пути исполнения. Если
        # backend недоступен на этой машине, честно отказываем (parser.error),
        # как и путь --mode --sandbox, а не запускаем без изоляции.
        # Ленивый импорт: http.server-стек тянем только когда реально нужен.
        from stepik_grader import web
        from stepik_grader.core.sandbox import SandboxUnavailableError

        try:
            web.run_server(
                port=args.port,
                root=args.root,
                confine=not args.no_root_confinement,
                sandbox=args.sandbox,
                # issue #395: для --serve история включена по умолчанию
                # (локальная приватная БД наполняет «Подучить»); --no-history
                # выключает. Отличие от режимов 1-4, где дефолт — opt-in.
                #
                # issue #997: раньше здесь стояло `args.history is not False` —
                # своя, третья лестница приоритета, не знавшая ни про
                # pyproject, ни про сохранённый тумблер меню. Пользователь
                # выключал историю пунктом 7 и получал её обратно при первом же
                # `--serve`. Теперь резолвер один на все поверхности, а дефолт
                # веба передаётся параметром.
                record_history=_resolve_record_history(args, default=True),
                # issue #1131 (LNCH-2-05): выбранный язык доезжает до страницы.
                # Раньше `--lang en --serve` переводил только сообщения API, а
                # интерфейс открывался на русском — флаг молча действовал
                # наполовину.
                lang=args.lang,
            )
        except SandboxUnavailableError as exc:
            parser.error(_t("sandbox_unavailable", reason=str(exc)))
        except OSError as exc:
            # issue #930: bind на занятом порту прилетал голым OSError прямо в
            # консоль. Меню (пункт 6) и GUI-лаунчер тот же отказ объясняют
            # по-человечески — CLI оставался единственной поверхностью из трёх,
            # где пользователь видел трейсбек. Ключ свой, не меню: там текст
            # обещает «возврат в меню», которого в этой ветке не бывает.
            parser.error(_t("serve_start_failed", error=exc))
        return ExitCode.OK

    if args.sandbox:
        # issue #266: жёсткий отказ, если backend недоступен на этой машине --
        # никогда не тихий откат на обычный LocalRunner (см. cli/options.py
        # --sandbox help и SECURITY.md). Ленивый импорт: core/sandbox тянет
        # ОС-специфичные модули (ctypes на Windows, resource на POSIX) только
        # когда флаг реально запрошен.
        #
        # issue #997 (DEV-1-02, LNCH-2-01, SBX-4-03, SEC-2-01 — четыре
        # независимых среза): блок стоял НИЖЕ ветки меню, поэтому `--sandbox`
        # без `--mode` не доходил сюда вовсе. Пользователь просил изоляцию,
        # получал обычное меню и грейдил через LocalRunner — а решение, пишущее
        # файлы мимо задачи, спокойно их писало. Молчание тут хуже отказа:
        # флаг о безопасности либо действует, либо честно падает.
        from stepik_grader.core.sandbox import SandboxRunner, SandboxUnavailableError

        try:
            set_runner(SandboxRunner())
        except SandboxUnavailableError as exc:
            parser.error(_t("sandbox_unavailable", reason=str(exc)))

    if args.mode is None:
        _interactive_menu()
        return ExitCode.OK

    record_stats = _resolve_record_stats(args)
    record_history = _resolve_record_history(args)
    record_lint = args.lint  # разовый флаг режимов 1/2 (issue #349), без config-дефолта
    ai_hints = args.ai_hints  # разовый флаг AI-подсказок режимов 1–4 (issue #435/#542)

    if args.mode == 1:
        if not args.file:
            args.file = _resolve_cli_path_or_error(parser, args, want_dir=False, flag="--file")
        verbose = _resolve_verbosity(args, default=True)
        # Режим 1 — один файл; инкрементальность (issue #71) неприменима,
        # поэтому кэш под --watch автоматически не включаем.
        use_cache = _resolve_use_cache(args, incremental=False)
        return _dispatch_with_watch(
            args.file,
            lambda: _run_mode_1(
                args.file,
                verbose=verbose,
                output=args.output,
                use_cache=use_cache,
                record_stats=record_stats,
                record_history=record_history,
                record_lint=record_lint,
                ai_hints=ai_hints,
            ),
            watch=args.watch,
        )
    elif args.mode == 2:
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        verbose = _resolve_verbosity(args, default=False)
        # issue #71: под --watch кэш включается по умолчанию — на событие
        # перезапускается только изменённый файл, остальные строки берутся из кэша.
        use_cache = _resolve_use_cache(args, incremental=args.watch)
        return _dispatch_with_watch(
            args.dir,
            lambda: _run_mode_2(
                args.dir,
                verbose=verbose,
                output=args.output,
                use_cache=use_cache,
                record_stats=record_stats,
                record_history=record_history,
                record_lint=record_lint,
                ai_hints=ai_hints,
            ),
            watch=args.watch,
        )
    elif args.mode == 3:
        if args.watch:
            parser.error("--watch is only supported for --mode 1/2")
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        _run_mode_3(
            args.dir,
            args.repeats,
            output=args.output,
            record_stats=record_stats,
            record_history=record_history,
            ai_hints=ai_hints,
        )
    elif args.mode == 4:
        if args.watch:
            parser.error("--watch is only supported for --mode 1/2")
        if not args.dir:
            args.dir = _resolve_cli_path_or_error(parser, args, want_dir=True, flag="--dir")
        _run_mode_4(
            args.dir,
            args.number,
            output=args.output,
            record_stats=record_stats,
            record_history=record_history,
            ai_hints=ai_hints,
        )

    # issue #936: режимы 3 и 4 — сравнение и микробенч, у них нет вердикта
    # «правильно/неправильно», по которому строится гейт. Возвращают OK.
    return ExitCode.OK


def run_cli(argv: list[str] | None = None) -> None:
    """Обёртка консольного скрипта: превращает код исхода в статус процесса.

    issue #936: `main()` остаётся вызываемой из кода и тестов без побочного
    `SystemExit`, а точка входа `stepik-grader` завершает процесс кодом прогона —
    иначе CI-гейт, построенный по документации, зеленеет на упавших тестах.
    """
    raise SystemExit(main(argv))
