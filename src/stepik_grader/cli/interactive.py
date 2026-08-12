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
import webbrowser

from stepik_grader import config
from stepik_grader.cli.context import CliContext
from stepik_grader.cli.exit_codes import ExitCode
from stepik_grader.cli.prompts import CONFIRM_YES
from stepik_grader.config import CONFIG
from stepik_grader.core import feedback, user_settings
from stepik_grader.core.grader_core import collect_grouped_files, find_all_solution_files

__all__ = [
    "_ask_bench_profile",
    "_ask_micro_profile",
    "_ask_number",
    "_feedback_flow",
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
    print(ctx.t("menu_9"))
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
    from stepik_grader.core import history_recording, insights
    from stepik_grader.core.reporter import print_insights_summary

    # issue #818: единая точка резолва — иначе разделы «Подучить»/«Прогресс»
    # смотрели бы в базу текущей папки, пока запись идёт в пользовательскую.
    db_path = history_recording.default_history_db_path()
    cards = insights.learning_cards(
        db_path,
        n=CONFIG.insights_window_n,
        t=CONFIG.insights_active_threshold_t,
        k=CONFIG.insights_clean_streak_k,
    )
    if not cards:
        # issue #822 (PROD-11): в меню историю включают пунктом 7, а не флагом.
        # Общий текст советовал `--history` — из меню это тупик: пользователь
        # уходил искать флаг, которого здесь нет, и обычно не возвращался.
        print(ctx.t("insights_no_data_menu"))
    else:
        from stepik_grader.cli import _insights_labels

        print_insights_summary(
            cards, rules_provider=rules.bundled_rules(), labels=_insights_labels()
        )


def _maybe_grade_downloaded(
    ctx: CliContext,
    downloaded: list[pathlib.Path],
    *,
    record_stats: bool,
    record_history: bool,
) -> ExitCode | None:
    """Предложить проверить только что скачанную задачу (issue #822, PROD-09).

    Возвращает код исхода прогона (issue #936) или ``None``, если проверять было
    нечего либо пользователь отказался — вызывающая сторона по ``None`` понимает,
    что прогона не было, и не трогает серию зачётов.

    Скачано несколько задач — предлагаем последнюю: это та, что пользователь
    ввёл только что, и другие варианты требовали бы ещё одного выбора ровно
    там, где мы экономим ввод. Остальные никуда не делись — они в меню по
    пунктам 1/2, и путь уже напечатан загрузчиком.
    """
    if not downloaded:
        return None
    task_dir = downloaded[-1]
    if len(downloaded) > 1:
        print(ctx.t("grade_downloaded_many", count=len(downloaded)))
    try:
        answer = input(ctx.t("grade_downloaded_prompt", path=task_dir)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        # Симметрично остальному пункту 8: отказ от ответа — это отказ от
        # прогона, а не падение меню.
        print()
        return None
    if answer not in CONFIRM_YES:
        return None
    return ctx.run_mode_2(task_dir, record_stats=record_stats, record_history=record_history)


def _maybe_nudge_history(
    ctx: CliContext, outcome: ExitCode, *, record_history: bool, nudged: bool
) -> bool:
    """Однократный (за сессию меню) nudge про «Подучить» после прогона с падениями.

    issue #430: печатается только при выключенной истории и только если ещё не
    показывали в этой сессии меню («не чаще раза за запуск»). Возвращает
    обновлённый флаг ``nudged``.

    issue #936: сверка именно с ``FAILURES``. Прежний ``bool`` не различал
    «упало» и «проверять было нечего», а после введения кодов исхода
    ``NO_TESTS`` истинен — без явной сверки подсказка «Подучить» выскакивала бы
    там, где прогона не было.
    """
    if outcome is ExitCode.FAILURES and not record_history and not nudged:
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


# issue #753: пункт «Обратная связь». Выбор пользователя → тип обращения
# (YAML-форма в .github/ISSUE_TEMPLATE/); «0»/мусор/пусто — возврат в меню.
_FEEDBACK_KIND_CHOICES: dict[str, feedback.FeedbackKind] = {
    "1": feedback.FeedbackKind.BUG,
    "2": feedback.FeedbackKind.IDEA,
    "3": feedback.FeedbackKind.TASK_PROBLEM,
}

# Главное текстовое поле каждой формы — туда уходит краткое описание из ввода.
# Имена — id полей YAML-форм (контракт core/feedback.py:_FIELD_IDS).
_FEEDBACK_SUMMARY_FIELD: dict[feedback.FeedbackKind, str] = {
    feedback.FeedbackKind.BUG: "what-happened",
    feedback.FeedbackKind.IDEA: "idea",
    feedback.FeedbackKind.TASK_PROBLEM: "details",
}

# Человекочитаемые подписи полей для предпросмотра: id формы → ключ локали.
# Поле без подписи печатается своим id — предпросмотр не должен молчать о
# том, что уедет, даже если подпись забыли добавить.
_FEEDBACK_FIELD_LABELS: dict[str, str] = {
    "what-happened": "feedback_field_description",
    "idea": "feedback_field_description",
    "details": "feedback_field_description",
    "environment": "feedback_field_environment",
    "commit": "feedback_field_commit",
    "step-url": "feedback_field_step_url",
}

# Утвердительные ответы на «Открыть браузер?» — пустой ввод тоже «да» (Y — дефолт).


def _print_feedback_preview(ctx: CliContext, prepared: feedback.PreparedIssue) -> None:
    """Показать ровно то, что уедет в форму, + предупреждения об усечении.

    Предпросмотр обязателен перед открытием браузера (issue #751): пользователь
    должен видеть содержимое обращения до того, как оно попадёт в публичный
    issue, а не узнавать о нём из уже открытой формы.
    """
    print(ctx.t("feedback_preview_header"))
    for name, value in prepared.fields.items():
        label_key = _FEEDBACK_FIELD_LABELS.get(name)
        print(f"  {ctx.t(label_key) if label_key else name}:")
        for line in value.splitlines():
            print(f"    {line}")
    print(ctx.t("feedback_privacy_note"))
    if prepared.truncated:
        print(ctx.t("feedback_truncated", fields=", ".join(prepared.truncated)))
    if prepared.dropped:
        print(ctx.t("feedback_dropped", fields=", ".join(prepared.dropped)))


def _feedback_flow(ctx: CliContext) -> None:
    """Пункт «Сообщить о проблеме / предложить идею» (9): prefilled-URL на GitHub.

    Спрашивает тип обращения и краткое описание, добавляет автособранное
    окружение (версия/ОС/Python — то, что пользователь никогда не пишет руками),
    показывает предпросмотр и только после подтверждения открывает браузер с
    заполненной формой. Issue создаёт сам пользователь кнопкой Submit — грейдер
    не имеет ни токена, ни сервера и ничего не отправляет (эпик #751).
    """
    print(ctx.t("feedback_header"))
    print(ctx.t("feedback_kind_1"))
    print(ctx.t("feedback_kind_2"))
    print(ctx.t("feedback_kind_3"))
    print(ctx.t("feedback_kind_0"))
    print(ctx.t("feedback_discussions_hint", url=feedback.DISCUSSIONS_URL))

    kind = _FEEDBACK_KIND_CHOICES.get(input(ctx.t("feedback_select_kind")).strip())
    if kind is None:  # «0», пустой ввод или мусор — тихий возврат в меню
        return

    fields = {
        "environment": feedback.collect_environment(
            channel="CLI (интерактивное меню)", lang=ctx.lang
        )
    }
    # Коммит есть только у git-клона; при установке через pip поле остаётся
    # пустым и просто не попадает в форму (пустые значения выбрасываются).
    if kind is not feedback.FeedbackKind.IDEA:
        commit = feedback.collect_commit()
        if commit:
            fields["commit"] = commit
    summary = input(ctx.t("feedback_ask_summary")).strip()
    if summary:
        fields[_FEEDBACK_SUMMARY_FIELD[kind]] = summary
    if kind is feedback.FeedbackKind.TASK_PROBLEM:
        step_url = input(ctx.t("feedback_ask_step_url")).strip()
        if step_url:
            fields["step-url"] = step_url

    prepared = feedback.prepare_issue(kind, fields)
    _print_feedback_preview(ctx, prepared)
    print(ctx.t("feedback_account_hint"))

    if input(ctx.t("feedback_confirm")).strip().lower() not in CONFIRM_YES:
        print(ctx.t("feedback_cancelled"))
        return

    # Ссылку печатаем ДО открытия: headless-окружение/отсутствие браузера не
    # должны оставить пользователя без способа дойти до формы.
    print(ctx.t("feedback_opened"))
    print(f"  {prepared.url}")
    try:
        webbrowser.open(prepared.url)
    except Exception as exc:  # webbrowser.Error, OSError — браузера может не быть
        print(ctx.t("feedback_open_failed", error=exc))


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

    issue #753: пункт 9 — обратная связь: собирает окружение и открывает
    заполненную форму issue на GitHub после предпросмотра и подтверждения.
    """
    settings_path = user_settings.default_settings_path(config.workspace_root())
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
                outcome = ctx.run_mode_1(
                    solution, record_stats=record_stats, record_history=record_history
                )
                nudged = _maybe_nudge_history(
                    ctx, outcome, record_history=record_history, nudged=nudged
                )
                # issue #936: серию трогает только состоявшийся прогон — при
                # NO_TESTS проверять было нечего, обнулять её не за что.
                if outcome is ExitCode.FAILURES:
                    success_streak = 0
                elif outcome is ExitCode.OK:
                    success_streak += 1
                nudged_success = _maybe_nudge_success_streak(
                    ctx,
                    success_streak,
                    record_history=record_history,
                    nudged_success=nudged_success,
                )

            elif choice == "2":
                directory = _prompt_path(ctx, "enter_folder_path", want_dir=True)
                outcome = ctx.run_mode_2(
                    directory, record_stats=record_stats, record_history=record_history
                )
                nudged = _maybe_nudge_history(
                    ctx, outcome, record_history=record_history, nudged=nudged
                )
                # issue #936: серию трогает только состоявшийся прогон — при
                # NO_TESTS проверять было нечего, обнулять её не за что.
                if outcome is ExitCode.FAILURES:
                    success_streak = 0
                elif outcome is ExitCode.OK:
                    success_streak += 1
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
                # issue #821: язык меню передаётся мастеру — иначе под `--lang en`
                # пользователь получал английское меню и русский мастер OAuth.
                downloaded: list[pathlib.Path] = []
                with contextlib.suppress(KeyboardInterrupt):
                    downloaded = downloader.main(lang=ctx.lang)
                # issue #822 (PROD-09): замкнуть воронку download→grade. В web то
                # же место закрыто кнопкой (downloader.js подставляет путь и
                # переключает на «Проверить»), а в CLI пользователь набирал
                # многосегментный путь с кириллическими slug'ами руками.
                graded = _maybe_grade_downloaded(
                    ctx,
                    downloaded,
                    record_stats=record_stats,
                    record_history=record_history,
                )
                if graded is not None:
                    nudged = _maybe_nudge_history(
                        ctx, graded, record_history=record_history, nudged=nudged
                    )
                    if graded is ExitCode.FAILURES:
                        success_streak = 0
                    elif graded is ExitCode.OK:
                        success_streak += 1
                    nudged_success = _maybe_nudge_success_streak(
                        ctx,
                        success_streak,
                        record_history=record_history,
                        nudged_success=nudged_success,
                    )

            elif choice == "9":
                # issue #753: обратная связь — prefilled-форма issue на GitHub.
                # Ctrl+C на любом из вопросов возвращает в меню, не роняя процесс
                # (симметрично пункту 8); EOF ловит внешний except EOFError.
                with contextlib.suppress(KeyboardInterrupt):
                    _feedback_flow(ctx)

            else:
                print(ctx.t("unknown_choice"))
        except EOFError:
            # issue #555: EOF/Ctrl+D на ЛЮБОМ prompt (выбор режима ИЛИ вложенный
            # ввод пути/числа/профиля в _prompt_path/_ask_*) — корректный выход,
            # не трейсбек. Пайповый ввод (`printf '1\n' | ... grader`) завершается
            # штатно, а не падает на вложенном input() после исчерпания потока.
            print(ctx.t("goodbye"))
            return
