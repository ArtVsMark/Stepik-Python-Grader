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
import contextlib
import pathlib

from stepik_grader.cli.context import CliContext
from stepik_grader.config import CONFIG
from stepik_grader.core import user_settings
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


def _ask_number(prompt: str, *, default: int, ctx: CliContext | None = None) -> int:
    """Спросить целое число: пустой ввод → default, нечисловой → default с подсказкой.

    issue #445: при нечисловом (но непустом) вводе печатаем сообщение о
    подстановке значения по умолчанию через ``ctx`` вместо тихого проглатывания.
    ``ctx=None`` сохраняет обратную совместимость для вызовов без локали.
    """
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        if ctx is not None:
            print(ctx.t("input_default_used", value=raw, default=default))
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
        repeats = _ask_number(ctx.t("enter_repeats_prompt"), default=15, ctx=ctx)
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
        number = _ask_number(ctx.t("enter_calls_prompt"), default=1000, ctx=ctx)
        number = max(100, min(500_000, number))
    return number


def _print_menu(ctx: CliContext, *, record_history: bool = False) -> None:
    """Отрисовать меню. ``record_history`` — состояние тумблера истории (issue #430).

    issue #643: подсказка про скачивание печатается сразу под заголовком — новичок
    без задачи на диске видит точку входа в воронку (пункт 8: скачать со Stepik)
    раньше режимов проверки, а не упирается в «нечего проверять».
    """
    print("\n" + "=" * 50)
    print(ctx.t("menu_title"))
    print(ctx.t("menu_download_hint"))
    print("=" * 50)
    print(ctx.t("menu_1"))
    print(ctx.t("menu_2"))
    print(ctx.t("menu_3"))
    print(ctx.t("menu_4"))
    print(ctx.t("menu_5"))
    print(ctx.t("menu_6"))
    state = ctx.t("toggle_on") if record_history else ctx.t("toggle_off")
    print(ctx.t("menu_7", state=state))
    print(ctx.t("menu_8"))
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

    issue #445: перед вводом печатаем мягкую подсказку про файловый диалог
    (открывается на пустой Enter). Формулировка не обещает диалог в
    headless-окружении, где ``_pick_path_via_dialog`` вернёт ``None``.
    """
    print(ctx.t("path_dialog_hint"))
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


def _show_insights(ctx: CliContext) -> None:
    """Пункт «Подучить» (5): карточки частых ошибок из истории прогонов."""
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


def _maybe_nudge_history(
    ctx: CliContext, had_failures: bool, *, record_history: bool, nudged: bool
) -> bool:
    """Однократный (за сессию меню) nudge про «Подучить» после прогона с падениями.

    issue #430: печатается только при выключенной истории и только если ещё не
    показывали в этой сессии меню («не чаще раза за запуск»). Возвращает
    обновлённый флаг ``nudged``.
    """
    if had_failures and not record_history and not nudged:
        print(ctx.t("nudge_enable_history"))
        return True
    return nudged


# issue #645: сколько успешных прогонов подряд собрать, прежде чем предложить
# включить историю. Достаточно большой порог, чтобы nudge не срабатывал на
# первом же успехе, но и не терялся в длинной серии.
_SUCCESS_STREAK_NUDGE_K = 3


def _maybe_nudge_success_streak(
    ctx: CliContext, success_streak: int, *, record_history: bool, nudged_success: bool
) -> bool:
    """Однократный (за сессию меню) позитивный nudge после серии успешных прогонов.

    issue #645: петля удержания (история → «Подучить»/«Прогресс») выключена по
    умолчанию (ADR-0002), поэтому раньше habit loop подталкивался ТОЛЬКО провалом
    (``_maybe_nudge_history``). После серии из ``_SUCCESS_STREAK_NUDGE_K`` успешных
    прогонов подряд при выключенной истории предлагаем включить запись, чтобы
    стрик и разборы начали накапливаться. Печатается не чаще раза за сессию.
    Возвращает обновлённый флаг ``nudged_success``.
    """
    if not record_history and not nudged_success and success_streak >= _SUCCESS_STREAK_NUDGE_K:
        print(ctx.t("nudge_success_streak", count=success_streak))
        return True
    return nudged_success


def _interactive_menu(ctx: CliContext) -> None:
    """Цикл интерактивного меню: показывать меню и выполнять режимы до «0»/EOF.

    issue #445: меню зациклено — после завершения режима оно снова доступно,
    процесс не нужно перезапускать на каждую проверку. Выход — пункт «0» или
    конец потока ввода (EOF/Ctrl+D). Ранние ошибки пути в режимах 3/4 больше не
    выкидывают из всего меню (``continue`` вместо прежнего ``return``).

    issue #430: пункт 7 переключает запись истории прогонов; выбор сохраняется
    в ``.grader_settings.json`` между запусками (user-state переопределяет
    ``CONFIG.record_history``). После прогона с падениями (режимы 1/2) при
    выключенной истории печатается однократный nudge про «Подучить».

    issue #643: пункт 8 скачивает задачу со Stepik по URL шага (downloader
    in-process), замыкая воронку download→grade в одной точке входа.

    issue #645: серию успешных прогонов подряд при выключенной истории тоже
    сопровождает однократный positive-nudge — habit loop подталкивается не
    только провалом, но и успехом (дефолт истории по ADR-0002 не меняется).
    """
    settings_path = user_settings.default_settings_path()
    settings = user_settings.load_settings(settings_path)
    # issue #268/#344: интерактивное меню не проходит через argparse, поэтому
    # --stats/--history и их --no-* недоступны — record_stats читаем из CONFIG.
    record_stats = CONFIG.record_stats
    # issue #430: тумблер истории из user-state (если задан) переопределяет
    # CONFIG-дефолт; None → пользователь не переопределял, наследуем pyproject.
    record_history = (
        settings.record_history if settings.record_history is not None else CONFIG.record_history
    )
    # issue #430: nudge «Подучить» показываем не чаще раза за сессию меню.
    nudged = False
    # issue #645: серия успешных прогонов подряд (режимы 1/2) для позитивного
    # nudge про включение истории; nudged_success ограничивает его разом за сессию.
    success_streak = 0
    nudged_success = False

    while True:
        _print_menu(ctx, record_history=record_history)
        try:
            choice = input(ctx.t("select_mode_prompt")).strip()

            if choice == "0":
                print(ctx.t("goodbye"))
                return

            if choice == "1":
                solution = _prompt_path(ctx, "enter_solution_path", want_dir=False)
                had_failures = ctx.run_mode_1(
                    solution, record_stats=record_stats, record_history=record_history
                )
                nudged = _maybe_nudge_history(
                    ctx, had_failures, record_history=record_history, nudged=nudged
                )
                success_streak = 0 if had_failures else success_streak + 1
                nudged_success = _maybe_nudge_success_streak(
                    ctx,
                    success_streak,
                    record_history=record_history,
                    nudged_success=nudged_success,
                )

            elif choice == "2":
                directory = _prompt_path(ctx, "enter_folder_path", want_dir=True)
                had_failures = ctx.run_mode_2(
                    directory, record_stats=record_stats, record_history=record_history
                )
                nudged = _maybe_nudge_history(
                    ctx, had_failures, record_history=record_history, nudged=nudged
                )
                success_streak = 0 if had_failures else success_streak + 1
                nudged_success = _maybe_nudge_success_streak(
                    ctx,
                    success_streak,
                    record_history=record_history,
                    nudged_success=nudged_success,
                )

            elif choice == "3":
                directory = _prompt_path(ctx, "enter_folder_path", want_dir=True)
                if not directory.is_dir():
                    print(ctx.t("dir_not_found", path=directory))
                    continue
                # find_all_solution_files/collect_grouped_files: импортированы
                # напрямую из core.grader_core, а не через ctx — ни один тест не
                # патчит их через cli.X (проверено grep).
                if not find_all_solution_files(directory):
                    print(ctx.t("no_solutions_found", path=directory))
                    continue
                repeats = ctx.ask_bench_profile()
                ctx.run_mode_3(
                    directory, repeats, record_stats=record_stats, record_history=record_history
                )

            elif choice == "4":
                directory = _prompt_path(ctx, "enter_folder_with_solutions_path", want_dir=True)
                if not directory.is_dir():
                    print(ctx.t("dir_not_found", path=directory))
                    continue
                if not collect_grouped_files(directory):
                    print(ctx.t("no_solutions_found", path=directory))
                    continue
                number = ctx.ask_micro_profile()
                ctx.run_mode_4(
                    directory, number, record_stats=record_stats, record_history=record_history
                )

            elif choice == "5":
                _show_insights(ctx)

            elif choice == "6":
                # issue #445: запуск web-интерфейса из меню. run_server блокирует
                # поток до Ctrl+C; ловим KeyboardInterrupt, чтобы вернуться в меню,
                # а не завершать процесс. Ленивый импорт http.server-стека — как в
                # cli.main(--serve). Песочница не включается (меню без --sandbox).
                # issue #430/#395: web-канал пишет историю ПО УМОЛЧАНИЮ (как --serve),
                # если пользователь явно не выключил тумблер: None (не трогали) → True,
                # True → True, False (явно выкл) → False. Симметрично `args.history is
                # not False` в cli.main(--serve) — иначе web из меню расходился бы с
                # --serve и с empty-state «история пишется автоматически».
                from stepik_grader import web

                web_history = settings.record_history is not False
                try:
                    web.run_server(record_history=web_history)
                except KeyboardInterrupt:
                    # Ctrl+C в сервере возвращает в меню, не завершает процесс.
                    pass
                except OSError as exc:
                    # issue #555: занятый порт / отказ bind не должны ронять меню —
                    # печатаем сообщение и возвращаемся к выбору режима (симметрично
                    # OSError-контуру пункта 7 и обработке в cli.main(--serve)).
                    print(ctx.t("server_start_failed", error=exc))

            elif choice == "7":
                # issue #430: тумблер записи истории, сохраняемый между запусками.
                record_history = not record_history
                settings.record_history = record_history
                try:
                    user_settings.save_settings(settings, settings_path)
                except OSError as exc:
                    # Best-effort, симметрично load_settings: read-only cwd / полный
                    # диск не должны ронять меню. Переключение действует на сессию.
                    print(ctx.t("history_save_failed", error=exc))
                else:
                    print(ctx.t("history_toggled_on" if record_history else "history_toggled_off"))

            elif choice == "8":
                # issue #643: скачивание задачи со Stepik прямо из меню — цикл
                # download→grade больше не распадается на отдельный вызов
                # `python -m stepik_grader.downloader`. Ленивый импорт (как web в
                # пункте 6). downloader.main() сам грузит конфиг, при отсутствии
                # secrets.json запускает пошаговый мастер (#433), авторизуется и
                # крутит цикл ввода URL шагов до пустой строки; по завершении —
                # обратно в меню. Ctrl+C прерывает скачивание, не роняя меню.
                from stepik_grader import downloader

                # Ctrl+C прерывает скачивание и возвращает в меню, не роняя процесс.
                with contextlib.suppress(KeyboardInterrupt):
                    downloader.main()

            else:
                print(ctx.t("unknown_choice"))
        except EOFError:
            # issue #555: EOF/Ctrl+D на ЛЮБОМ prompt (выбор режима ИЛИ вложенный
            # ввод пути/числа/профиля в _prompt_path/_ask_*) — корректный выход,
            # не трейсбек. Пайповый ввод (`printf '1\n' | ... grader`) завершается
            # штатно, а не падает на вложенном input() после исчерпания потока.
            print(ctx.t("goodbye"))
            return
