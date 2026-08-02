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
import importlib.metadata
import os
import pathlib
from collections.abc import Callable

# issue #120: mode handlers — вынесены в leaf-модуль cli/commands.py; получают
# зависимости через CliContext (cli/context.py), а не читают module globals
# этого файла напрямую (см. docstring выше и _build_cli_context() ниже).
from stepik_grader.cli import commands, interactive
from stepik_grader.cli.context import CliContext

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
)

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
from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.core.reporter import (
    print_insights_summary,
    print_progress_summary,
    print_stats_summary,
)

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
) -> bool:
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
) -> bool:
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


def _dispatch_with_watch(target: pathlib.Path, run: Callable[[], object], *, watch: bool) -> None:
    """Запустить ``run`` один раз или, под ``--watch``, перезапускать при
    изменениях ``target``.

    issue #354 — общий раннер вместо двух почти одинаковых watch/no-watch
    веток в режимах 1 и 2.
    """
    if watch:
        _watch_and_rerun(target, run)
    else:
        run()


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

    # issue #146: opt-in диагностический лог. --diagnostic → debug; иначе уровень
    # берётся из STEPIK_GRADER_LOG (по умолчанию выключено, файл не создаётся).
    configure_diagnostics("debug" if args.diagnostic else None)

    global _LANG
    _LANG = args.lang

    if args.version:
        print(f"grader.py {_format_version_for_display(__version__)}")
        return

    if args.clear_cache:
        # issue #816 (DEV-11): чистим ОБА кэша. Раньше флаг трогал только
        # `.grader_cache` (результаты прогонов), а `.stepik_cache` (ответы API)
        # оставался расти — при том что именно он прибавляет файл на каждую
        # новую скачанную задачу и не чистится ничем, включая TTL.
        from stepik_grader.core.stepik_client import clear_cache as clear_stepik_cache

        removed = GraderCache().clear() + clear_stepik_cache()
        print(_t("cache_cleared", count=removed))
        return

    if args.purge_history is not None:
        # issue #813 (SECD-03): у локального журнала обучения должен быть
        # штатный способ удаления. Раньше `--no-history` лишь переставал писать,
        # а убрать уже накопленное можно было только `rm .grader_history.db`
        # (плюс -wal/-shm) — то есть зная о файлах, которых пользователь не
        # создавал. Без аргумента чистим и статистику: это те же личные данные.
        from stepik_grader.core import stats as stats_mod
        from stepik_grader.core.history import purge_history
        from stepik_grader.core.history_recording import default_history_db_path

        task_key = args.purge_history or None
        runs_removed = purge_history(default_history_db_path(), task_key=task_key)
        if task_key is None:
            stats_removed = stats_mod.purge_stats()
            print(_t("history_purged", runs=runs_removed, stats=stats_removed))
        else:
            print(_t("history_purged_task", task=task_key, runs=runs_removed))
        return

    if args.stats_summary:
        summary = stats.read_summary()
        if summary["total_runs"] == 0:
            print(_t("stats_no_data"))
        else:
            print_stats_summary(summary)
        return

    if args.export_progress:
        from stepik_grader.core import history, progress_export

        db_path = pathlib.Path.cwd() / history.HISTORY_DB_NAME
        report = progress_export.build_progress_report(db_path)
        if report["total_runs"] == 0:
            print(_t("insights_no_data"))  # дружелюбно, не ошибка (issue #432)
            return
        fmt = args.export_progress
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
        return

    if args.insights:
        from stepik_grader import rules
        from stepik_grader.core import history, insights

        db_path = pathlib.Path.cwd() / history.HISTORY_DB_NAME
        cards = insights.learning_cards(
            db_path,
            n=CONFIG.insights_window_n,
            t=CONFIG.insights_active_threshold_t,
            k=CONFIG.insights_clean_streak_k,
        )
        progress = insights.time_to_first_green(db_path)  # issue #431: TTFG
        if not cards and not progress:
            print(_t("insights_no_data"))
        else:
            if progress:
                print_progress_summary(progress)
            if cards:
                print_insights_summary(cards, rules_provider=rules.bundled_rules())
        return

    if args.init_vscode:
        from stepik_grader import ide

        written, path = ide.write_vscode_tasks()
        print(_t("vscode_written" if written else "vscode_exists", path=path))
        return

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
        return

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
                record_history=args.history is not False,
            )
        except SandboxUnavailableError as exc:
            parser.error(_t("sandbox_unavailable", reason=str(exc)))
        return

    if args.mode is None:
        _interactive_menu()
        return

    record_stats = _resolve_record_stats(args)
    record_history = _resolve_record_history(args)
    record_lint = args.lint  # разовый флаг режимов 1/2 (issue #349), без config-дефолта
    ai_hints = args.ai_hints  # разовый флаг AI-подсказок режимов 1–4 (issue #435/#542)

    if args.sandbox:
        # issue #266: жёсткий отказ, если backend недоступен на этой машине --
        # никогда не тихий откат на обычный LocalRunner (см. cli/options.py
        # --sandbox help и SECURITY.md). Ленивый импорт: core/sandbox тянет
        # ОС-специфичные модули (ctypes на Windows, resource на POSIX) только
        # когда флаг реально запрошен.
        from stepik_grader.core.sandbox import SandboxRunner, SandboxUnavailableError

        try:
            set_runner(SandboxRunner())
        except SandboxUnavailableError as exc:
            parser.error(_t("sandbox_unavailable", reason=str(exc)))

    if args.mode == 1:
        if not args.file:
            args.file = _resolve_cli_path_or_error(parser, args, want_dir=False, flag="--file")
        verbose = _resolve_verbosity(args, default=True)
        # Режим 1 — один файл; инкрементальность (issue #71) неприменима,
        # поэтому кэш под --watch автоматически не включаем.
        use_cache = _resolve_use_cache(args, incremental=False)
        _dispatch_with_watch(
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
        _dispatch_with_watch(
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
