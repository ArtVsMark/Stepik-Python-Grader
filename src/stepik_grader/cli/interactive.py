"""cli/interactive.py — интерактивное меню и prompt-хелперы (issue #121 Phase 2).

Архитектурный слой: Application / CLI (leaf-модуль).

Реализация `_interactive_menu`, `_ask_bench_profile`/`_ask_micro_profile`/
`_ask_number`, `_print_menu`, `_pick_path_via_dialog`/`_prompt_path`/
`_resolve_cli_path_or_error`, вынесенная из `cli/__init__.py`. Не импортирует
`stepik_grader.cli` — зависимости, которые тесты патчат через facade
(`_pick_path_via_dialog`, `_ask_bench_profile`, `_ask_micro_profile`,
`_run_mode_1..4`, `_t`), приходят явно через `CliContext` (см.
`cli/context.py`), а не читаются как module-global имена этого файла.
`cli/__init__.py` держит тонкие обёртки с теми же публичными сигнатурами,
что и раньше, строит `CliContext` заново на каждый вызов
(`_build_cli_context()`) и делегирует сюда — так monkeypatch на
`cli._pick_path_via_dialog`/`cli._ask_bench_profile`/т.д. по-прежнему
долетает до реального исполнения без миграции существующих тестов.

`_LANG`/`_LOCALE_MESSAGES`/`_t` НЕ переезжают сюда и остаются в
`cli/__init__.py` — `_LANG` реально мутируется в `main()` (`global _LANG`), и
перенос сделал бы `cli._LANG` одноразовым snapshot вместо живой ссылки,
незаметно ломая `monkeypatch.setattr(cli, "_LANG", ...)` (issue #121 Phase 2,
намеренно ограниченный scope).

`_print_menu`/`_prompt_path` вызываются внутри `_interactive_menu` как
обычные same-module bare-имена (не через `ctx`) — ни то, ни другое не
патчится напрямую через `cli.X` ни в одном тесте (подтверждено grep), в
отличие от `_pick_path_via_dialog`, которую `_prompt_path`/
`_resolve_cli_path_or_error` обязаны читать через `ctx.pick_path_via_dialog`.
"""

from __future__ import annotations

import argparse
import pathlib

from stepik_grader.cli.context import CliContext
from stepik_grader.config import CONFIG
from stepik_grader.core.grader_core import collect_grouped_files, find_all_solution_files

__all__ = [
    "_ask_bench_profile",
    "_ask_micro_profile",
    "_ask_number",
    "_interactive_menu",
    "_pick_path_via_dialog",
    "_print_menu",
    "_prompt_path",
    "_resolve_cli_path_or_error",
]

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


def _ask_number(prompt: str, *, default: int) -> int:
    raw = input(prompt).strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _ask_bench_profile(ctx: CliContext) -> int:
    """Запросить профиль нагрузки для subprocess-бенчмарка (режим 3)."""
    print(ctx.t("bench_profile_header"))
    print(ctx.t("bench_profile_1"))
    print(ctx.t("bench_profile_2"))
    print(ctx.t("bench_profile_3"))
    print(ctx.t("bench_profile_4"))
    choice = input(ctx.t("select_profile_prompt")).strip() or "2"
    repeats = _BENCH_PROFILES.get(choice)
    if repeats is None:
        repeats = _BENCH_PROFILES["2"]
    if repeats == 0:
        repeats = _ask_number(ctx.t("enter_repeats_prompt"), default=15)
        repeats = max(5, min(100, repeats))
    return repeats


def _ask_micro_profile(ctx: CliContext) -> int:
    """Запросить профиль нагрузки для timeit micro-bench (режим 4)."""
    print(ctx.t("micro_profile_header"))
    print(ctx.t("micro_profile_1"))
    print(ctx.t("micro_profile_2"))
    print(ctx.t("micro_profile_3"))
    print(ctx.t("micro_profile_4"))
    print(ctx.t("micro_profile_5"))
    print(ctx.t("micro_profile_6"))
    choice = input(ctx.t("select_profile_prompt")).strip() or "2"
    number = _MICRO_PROFILES.get(choice)
    if number is None:
        number = _MICRO_PROFILES["2"]
    if number == 0:
        number = _ask_number(ctx.t("enter_calls_prompt"), default=1000)
        number = max(100, min(500_000, number))
    return number


def _print_menu(ctx: CliContext) -> None:
    print("\n" + "=" * 50)
    print(ctx.t("menu_title"))
    print("=" * 50)
    print(ctx.t("menu_1"))
    print(ctx.t("menu_2"))
    print(ctx.t("menu_3"))
    print(ctx.t("menu_4"))
    print(ctx.t("menu_5"))
    print(ctx.t("menu_0"))
    print("=" * 50)


def _pick_path_via_dialog(ctx: CliContext, *, want_dir: bool) -> pathlib.Path | None:
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
            path = filedialog.askdirectory(title=ctx.t("dialog_pick_dir"))
        else:
            path = filedialog.askopenfilename(
                title=ctx.t("dialog_pick_file"),
                filetypes=[("Python files", "*.py"), ("All files", "*.*")],
            )
    finally:
        root.destroy()
    return pathlib.Path(path) if path else None


def _prompt_path(ctx: CliContext, prompt_key: str, *, want_dir: bool) -> pathlib.Path:
    """Спросить путь в интерактивном меню; при пустом вводе — файловый диалог.

    Пустой путь на выходе (пустой ввод + отмена/недоступность диалога)
    корректно обрабатывается вызывающими режимами через их обычные
    "file/dir not found" сообщения (issue #79).
    """
    raw = input(ctx.t(prompt_key)).strip()
    if raw:
        return pathlib.Path(raw)
    picked = ctx.pick_path_via_dialog(want_dir=want_dir)
    return picked if picked is not None else pathlib.Path("")


def _resolve_cli_path_or_error(
    ctx: CliContext,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    want_dir: bool,
    flag: str,
) -> pathlib.Path:
    """Вернуть путь для non-interactive режима, когда флаг пути не задан.

    В интерактивном text-режиме (без ``--output``-машинного вывода и без
    ``--watch``) предлагает нативный файловый диалог; иначе — или при отмене
    диалога / отсутствии tkinter — завершает работу через ``parser.error``
    (чистое сообщение argparse, не трейсбек). issue #79.
    """
    if args.output == "text" and not args.watch:
        picked = ctx.pick_path_via_dialog(want_dir=want_dir)
        if picked:
            return picked
    parser.error(f"--mode {args.mode} requires {flag}")


def _interactive_menu(ctx: CliContext) -> None:
    """Показать меню один раз, выполнить выбранный режим и завершить работу."""
    _print_menu(ctx)
    choice = input(ctx.t("select_mode_prompt")).strip()

    if choice == "0":
        print(ctx.t("goodbye"))
        return

    # issue #268/#344: интерактивное меню не проходит через argparse, поэтому
    # --stats/--history и их --no-* недоступны — читаем [tool.stepik-grader]
    # record_stats/record_history из CONFIG напрямую, в отличие от use_cache
    # (у кэша в меню нет тумблера вовсе, см. ctx.run_mode_N вызовы без use_cache).
    record_stats = CONFIG.record_stats
    record_history = CONFIG.record_history

    if choice == "1":
        solution = _prompt_path(ctx, "enter_solution_path", want_dir=False)
        ctx.run_mode_1(solution, record_stats=record_stats, record_history=record_history)

    elif choice == "2":
        directory = _prompt_path(ctx, "enter_folder_path", want_dir=True)
        ctx.run_mode_2(directory, record_stats=record_stats, record_history=record_history)

    elif choice == "3":
        directory = _prompt_path(ctx, "enter_folder_path", want_dir=True)
        if not directory.is_dir():
            print(ctx.t("dir_not_found", path=directory))
            return
        # find_all_solution_files/collect_grouped_files: импортированы напрямую
        # из core.grader_core, а не через ctx — ни один тест не патчит их
        # через cli.X (проверено grep), в отличие от остальных зависимостей
        # этой функции.
        if not find_all_solution_files(directory):
            print(ctx.t("no_solutions_found"))
            return
        repeats = ctx.ask_bench_profile()
        ctx.run_mode_3(directory, repeats, record_stats=record_stats, record_history=record_history)

    elif choice == "4":
        directory = _prompt_path(ctx, "enter_folder_with_solutions_path", want_dir=True)
        if not directory.is_dir():
            print(ctx.t("dir_not_found", path=directory))
            return
        if not collect_grouped_files(directory):
            print(ctx.t("no_solutions_found"))
            return
        number = ctx.ask_micro_profile()
        ctx.run_mode_4(directory, number, record_stats=record_stats, record_history=record_history)

    elif choice == "5":
        from stepik_grader import rules
        from stepik_grader.core import history, insights
        from stepik_grader.core.reporter import print_insights_summary

        db_path = pathlib.Path.cwd() / history.HISTORY_DB_NAME
        cards = insights.learning_cards(
            db_path,
            n=CONFIG.insights_window_n,
            t=CONFIG.insights_active_threshold_t,
            k=CONFIG.insights_clean_streak_k,
        )
        if not cards:
            print(ctx.t("insights_no_data"))
        else:
            print_insights_summary(cards, rules_provider=rules.bundled_rules())

    else:
        print(ctx.t("unknown_choice"))
