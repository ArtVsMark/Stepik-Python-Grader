"""cli/commands.py — обработчики режимов CLI (issue #120, Stage 2 эпика #117).

Архитектурный слой: Application / CLI (leaf-модуль).

Реализация `_run_mode_1..4` (и приватного `_run_tests_maybe_cached`),
вынесенная из `cli/__init__.py`. Не импортирует `stepik_grader.cli` —
зависимости, которые тесты патчат через facade (`run_tests`,
`run_benchmark`, `run_microbench_mode`, `_resolve_test_dir_from_input`,
`_print_tabular`, `_t`), приходят явно через `CliContext` (см.
`cli/context.py`), а не читаются как module-global имена этого файла.
`cli/__init__.py` держит тонкие обёртки с тем же публичным сигнатурами,
что и раньше, строит `CliContext` заново на каждый вызов
(`_build_cli_context()`) и делегирует сюда — так monkeypatch на
`cli.run_tests`/`cli._print_tabular`/т.д. по-прежнему долетает до реального
исполнения без миграции существующих тестов.

Всё остальное, что используют handlers (`GraderCache`, `resolve_test_dir`,
`find_all_solution_files`, `collect_grouped_files`, `apply_relative_ranking`,
`print_correctness_results`, `print_benchmark_results`, `rich_track`,
`print_case_verbose`, пороги ранжирования) никогда не патчится через
`cli.X` в тестах — импортируется напрямую из `core.*`, без контекста.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import urlparse

from stepik_grader import config, rules
from stepik_grader.cli.context import CliContext
from stepik_grader.cli.prompts import EXPLICIT_YES
from stepik_grader.config import get_config
from stepik_grader.core import (
    ai_hints,
    history,
    history_recording,
    lint,
    stats,
    user_settings,
)
from stepik_grader.core.cache import GraderCache, hash_solution, hash_tests
from stepik_grader.core.failure_context import build_failure_context
from stepik_grader.core.grader_core import (
    collect_grouped_files,
    find_all_solution_files,
    resolve_test_dir,
)
from stepik_grader.core.microbench_runner import apply_relative_ranking
from stepik_grader.core.reporter import (
    print_benchmark_results,
    print_case_verbose,
    print_correctness_results,
    print_lint_block,
    rich_track,
    safe_rel,
)
from stepik_grader.core.result import BenchResult, CaseResult, SolutionResult
from stepik_grader.core.runprofile import current_profile


def _t(key: str, /, **kwargs: object) -> str:
    """Локализованное сообщение CLI (issue #897).

    Ленивый импорт: ``cli/__init__`` импортирует ЭТОТ модуль, поэтому импорт
    ``_t`` на уровне модуля замкнул бы цикл. Обёртка, а не импорт в каждой
    функции, — чтобы вызовы читались как обычный ``_t(...)``.
    """
    from stepik_grader.cli import _t as translate

    return translate(key, **kwargs)


_rel = safe_rel  # issue #831 (DEV-10): единственная реализация — в core/reporter


def _verdict_counts_from_cases(cases: Sequence[CaseResult]) -> dict[str, int]:
    """Тальи вердиктов кейсов для режимов 1/2 (issue #268 — статистика)."""
    counts: dict[str, int] = {}
    for c in cases:
        verdict = c.get("verdict") or ("AC" if c.get("passed") else "WA")
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def _has_failures(cases: Sequence[CaseResult]) -> bool:
    """Есть ли среди кейсов непройденные (для nudge «Подучить», issue #430)."""
    return any(not c.get("passed") for c in cases)


def _preflight_skip(
    ctx: CliContext, solution: pathlib.Path, test_dir: pathlib.Path
) -> BenchResult | None:
    """Отсеять решение, не прошедшее тесты, до замера скорости (issue #729).

    ``None`` — решение годится. Иначе — запись с ``verdict="SKIPPED"``: сравнение
    по времени осмысленно только между решениями, которые дают верный ответ, а
    ``run_benchmark`` результат не сверяет — WA получал медиану и место в
    рейтинге наравне с корректными.
    """
    report = ctx.preflight_solution(solution, test_dir)
    if report["ok"] or report["cancelled"]:
        return None
    return {
        "error": ctx.t(
            "bench_skipped_not_ac",
            passed=report["passed"],
            total=report["total"],
            verdict=report["verdict"] or "—",
        ),
        "runs": 0,
        "verdict": "SKIPPED",
    }


def _verdict_counts_from_bench(results: Mapping[pathlib.Path, BenchResult]) -> dict[str, int]:
    """Тальи вердиктов решений для режимов 3/4 (issue #268 — статистика).

    Ошибочные решения (``error`` вместо ``verdict``) считаются как ``ERR`` —
    та же метка, что уже использует UI веб-слоя для строк с ошибкой.
    """
    counts: dict[str, int] = {}
    for data in results.values():
        verdict = "ERR" if data.get("error") else data.get("verdict")
        if verdict:
            counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def _collect_lint(
    solutions: list[pathlib.Path],
) -> dict[pathlib.Path, list[lint.Violation]] | None:
    """Прогнать ruff по каждому решению ОДИН раз (issue #403).

    Единый источник lint'а и для печати блока «Стиль» (#349), и для записи в
    историю (#346/#403) — чтобы ruff не запускался дважды при ``--lint --history``.
    ``None`` — ruff недоступен (extra ``[lint]`` не установлен); ``{}`` — решений
    нет. Best-effort: ``run_lint`` сам проглатывает сбои в пустой список.
    """
    if not solutions:
        return {}
    if not lint.ruff_available():
        return None
    # issue #728: набор правил — ровно карточки базы (rules/data/pep8_ru.json),
    # с --preview: иначе половина карточек недостижима, а часть найденных кодов
    # нечем объяснить. Строится в rules/ (Domain карточек), а не в core/lint.
    select = rules.lint_select()
    return {sol: lint.run_lint(sol, select=select, preview=True) for sol in solutions}


def _print_lint_blocks(
    solutions: list[pathlib.Path],
    base: pathlib.Path | None,
    output: str,
    collected: dict[pathlib.Path, list[lint.Violation]] | None,
) -> None:
    """Блоки «Стиль» по решениям режимов 1/2 (``--lint``, issue #349).

    Печатает уже собранные ``_collect_lint`` нарушения (не запускает ruff
    повторно, issue #403). Только текстовый вывод (машинные форматы не трогаем).
    ``collected is None`` — ruff недоступен: печатаем подсказку об установке один
    раз. Линт НЕ влияет на вердикт — информационный блок.
    """
    # Локальный импорт: cli/__init__ импортирует этот модуль, поэтому импорт _t на
    # уровне модуля замкнул бы цикл (тот же приём, что в cli/interactive.py).
    from stepik_grader.cli import _lint_labels

    if output != "text" or not solutions:
        return
    if collected is None:
        print(_t("lint_skipped_no_ruff"))
        return
    provider = rules.bundled_rules()
    labels = _lint_labels()
    multi = len(solutions) > 1
    for sol in solutions:
        violations = collected.get(sol, [])
        if violations and multi and base is not None:
            print(f"\n{_rel(sol, base)}")
        print_lint_block(violations, rules_provider=provider, labels=labels)


def consent_endpoint(base_url: str | None) -> str:
    """Получатель согласия — ``scheme://host[:port]`` без пути (issue #812).

    Путь (``/v1``) отбрасывается: согласие даётся серверу, а не конкретному
    маршруту на нём, иначе смена ``/v1`` на ``/v1beta`` спрашивала бы заново без
    всякой пользы. Порт же значим — на другом порту другой сервис.
    """
    parsed = urlparse((base_url or "").strip())
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def _ensure_ai_consent(base_url: str | None = None) -> bool:
    """Явное согласие на отправку кода ЭТОМУ AI-провайдеру (issue #630/#812).

    AI-подсказка отправляет код решения и его ввод-вывод внешнему провайдеру,
    поэтому web-путь с issue #543 требует явного согласия и без него отвечает
    ``403 consent_required``, ничего не отправляя. CLI этот гейт не проверял и
    слал код молча — приватность (в том числе несовершеннолетних студентов)
    соблюдалась лишь в одном из двух путей.

    issue #812: согласие привязано к получателю. Прежде оно было глобальным —
    сказав «да» локальному ollama, пользователь тем же «да» разрешал отправку
    на любой адрес, который позже окажется в конфиге (а конфиг приезжает вместе
    с чужой папкой задач). Сменился ``scheme://host:port`` — спрашиваем заново,
    показывая, КОМУ уйдут данные.

    Хранится в ``.grader_settings.json`` — тот же файл, что у web, поэтому
    данное однажды согласие действует для обоих путей. Отказ намеренно НЕ
    фиксируется: пользователь может передумать, а «залипший» отказ пришлось бы
    править руками в JSON.

    В неинтерактивной сессии (нет TTY: CI, пайп) согласие не запрашивается —
    подсказки просто пропускаются с явным сообщением.
    """
    settings_path = user_settings.default_settings_path(config.workspace_root())
    settings = user_settings.load_settings(settings_path)
    endpoint = consent_endpoint(base_url)
    if settings.ai_hint_consent is True and settings.ai_hint_consent_endpoint == endpoint:
        return True

    if not sys.stdin.isatty():
        print(f"\n{_t('ai_skipped_consent_required')}")
        return False

    print("\n" + _t("ai_consent_notice", settings_file=user_settings.SETTINGS_FILE_NAME))
    if endpoint:
        print(_t("ai_consent_recipient", endpoint=endpoint))
    try:
        answer = input(_t("ai_consent_prompt")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer not in EXPLICIT_YES:
        print(_t("ai_consent_declined"))
        return False

    settings.ai_hint_consent = True
    settings.ai_hint_consent_endpoint = endpoint
    with contextlib.suppress(OSError):
        user_settings.save_settings(settings, settings_path)
    return True


def revoke_ai_consent() -> bool:
    """Отозвать согласие на AI-подсказки; ``True`` — оно было (issue #812).

    ``SECD-06``: отозвать согласие было нечем — только правкой JSON руками.
    Согласие на передачу данных, которое нельзя отозвать, согласием не является.
    """
    settings_path = user_settings.default_settings_path(config.workspace_root())
    settings = user_settings.load_settings(settings_path)
    had = settings.ai_hint_consent is True
    settings.ai_hint_consent = None
    settings.ai_hint_consent_endpoint = None
    with contextlib.suppress(OSError):
        user_settings.save_settings(settings, settings_path)
    return had


def _read_solution_code(solution: pathlib.Path) -> str:
    """Содержимое решения для заземления AI-промпта (best-effort, «» при сбое)."""
    try:
        return solution.read_text(encoding="utf-8")
    except OSError:
        return ""


def _resolve_ai_config() -> object | None:
    """Конфиг AI-канала, если провайдер настроен; иначе печать подсказки + ``None``.

    Общий гейт режимов 1–4 (issue #542): провайдер не настроен → одна подсказка,
    как включить, и ``None`` (вызывающий выходит). Звать ТОЛЬКО когда есть что
    объяснять (есть упавшие кейсы/решения) — иначе подсказка печатается впустую.
    """
    config = get_config()
    if not ai_hints.is_configured(config):
        print(f"\n{_t('ai_skipped_no_provider')}")
        return None
    # issue #630: consent-гейт ДО любого обращения к провайдеру — как в web
    # issue #812 (SECD-02): недопустимый адрес отсекается ЗДЕСЬ, а не молча в
    # `_post_chat` — иначе пользователь видел бы просто отсутствие подсказок и
    # не знал, что дело в схеме. Спрашивать согласие на адрес, куда всё равно не
    # пойдём, тоже незачем — проверка стоит перед consent-гейтом.
    base_url = getattr(config, "ai_base_url", None)
    if not ai_hints.base_url_is_allowed(str(base_url or "")):
        print("\n" + _t("ai_skipped_insecure_url", url=repr(base_url)))
        return None
    # issue #630: consent-гейт ДО любого обращения к провайдеру — как в web
    # (403 consent_required). Общая точка для режимов 1-4, поэтому оба
    # вызывающих (_print_ai_hints / _print_ai_hints_bench) закрыты разом.
    if not _ensure_ai_consent(base_url):
        return None
    return config


def _ai_hint_limit(config: object) -> int:
    """Сколько AI-подсказок максимум за прогон (issue #812, ``TREND-02``).

    Потолка не было вовсе: N упавших кейсов = N последовательных POST, каждый
    до ``ai_timeout_seconds`` (20 с по умолчанию). Папка на 40 решений — это
    13 минут ожидания и 40 оплаченных запросов, о которых никто не предупреждал.
    Первые несколько подсказок несут почти всю пользу: ошибки внутри одного
    решения обычно однотипны.
    """
    return max(1, int(getattr(config, "ai_max_hints", 5)))


def _print_ai_limit_notice(limit: int) -> None:
    """Сказать, что подсказки оборваны потолком, а не закончились сами."""
    print(_t("ai_hints_capped", limit=limit))


def _print_ai_hints(
    rows: Sequence[tuple[pathlib.Path, SolutionResult]], *, lang: str = "ru"
) -> None:
    """AI-объяснения упавших кейсов режимов 1/2 (``--ai-hints``, issue #435/#542, ADR-0003).

    Только текстовый вывод; opt-in. Грейдинг НИКОГДА не падает из-за AI —
    ``explain_failure`` глушит любые ошибки канала в ``None``. Контекст строится
    общим core-хелпером ``build_failure_context`` (issue #542). Провайдер не
    настроен → одна подсказка, как включить (только если есть что объяснять)."""
    has_fail = any(not c.get("passed") for _, result in rows for c in result["cases"])
    if not has_fail:
        return
    config = _resolve_ai_config()
    if config is None:
        return
    limit = _ai_hint_limit(config)
    shown = 0
    for solution, result in rows:
        code = _read_solution_code(solution)
        for index, case in enumerate(result["cases"], start=1):
            if case.get("passed"):
                continue
            if shown >= limit:
                _print_ai_limit_notice(limit)
                return
            fc = build_failure_context(case, code=code, lang=lang)
            hint = ai_hints.explain_failure(fc, config)
            shown += 1
            if hint:
                header = _t("ai_hint_case_header", solution=solution.name, index=index)
                print(f"{header}\n{hint}")


def _print_ai_hints_bench(
    results: Mapping[pathlib.Path, BenchResult], base: pathlib.Path, *, lang: str = "ru"
) -> None:
    """AI-объяснения упавших решений режимов 3/4 (``--ai-hints``, issue #542).

    Бенчмарк не даёт per-case ``output``/``expected``/``diff`` — объясняем
    решения с ошибкой исполнения (crash/RE), строя контекст ТЕМ ЖЕ core-хелпером
    ``build_failure_context`` из ``verdict``+``error``. Только текстовый вывод;
    грейдинг НИКОГДА не падает. Провайдер не настроен → одна подсказка (если есть
    что объяснять)."""
    failing = [(path, data) for path, data in sorted(results.items()) if data.get("error")]
    if not failing:
        return
    config = _resolve_ai_config()
    if config is None:
        return
    limit = _ai_hint_limit(config)
    for shown, (path, data) in enumerate(failing):
        if shown >= limit:
            _print_ai_limit_notice(limit)
            return
        code = _read_solution_code(path)
        case = {"verdict": str(data.get("verdict") or "RE"), "error": str(data.get("error", ""))}
        fc = build_failure_context(case, code=code, lang=lang)
        hint = ai_hints.explain_failure(fc, config)
        if hint:
            print(f"\n· {_rel(path, base)}:\n{hint}")


__all__ = [
    "_run_mode_1",
    "_run_mode_2",
    "_run_mode_3",
    "_run_mode_4",
    "_run_tests_maybe_cached",
    "_verdict_counts_from_bench",
    "_verdict_counts_from_cases",
]


def _run_tests_maybe_cached(
    ctx: CliContext,
    solution: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    verbose: bool,
    output: str,
    cache: GraderCache | None,
) -> tuple[SolutionResult, bool]:
    """Прогнать тесты, при активном кэше — переиспользуя актуальную запись.

    Возвращает пару (result, from_cache). Ключ кэша — sha256 содержимого
    решения, sha256 всех файлов тест-директории (issue #56) и отпечаток условий
    прогона (issue #984): таймаут, изоляция и лимиты меняют вердикт наравне с
    кодом, поэтому запись, снятая при других условиях, считается промахом. При
    промахе результат кладётся в кэш (в память; ``cache.save()`` — забота
    вызывающей стороны, чтобы для пачки решений писать файл один раз). На
    попадании per-case verbose-вывод не печатается — тесты не запускались.
    """
    callback = print_case_verbose if (verbose and output == "text") else None
    if cache is None:
        result = ctx.run_tests(solution, test_dir, verbose=verbose, verbose_callback=callback)
        return result, False

    solution_sha = hash_solution(solution)
    tests_sha = hash_tests(test_dir)
    env = current_profile().fingerprint
    cached = cache.get(solution, solution_sha, tests_sha, env=env)
    if cached is not None:
        # Форма читается из results.json — статически её никто не гарантирует,
        # поэтому cast, а не «типизированный» кэш с ложной уверенностью.
        return cast("SolutionResult", cached), True

    result = ctx.run_tests(solution, test_dir, verbose=verbose, verbose_callback=callback)
    cache.put(solution, solution_sha, tests_sha, result, env=env)
    return result, False


def _missing_tests_hint(ctx: CliContext, solution: pathlib.Path) -> str:
    """Подсказка «тестов нет» — разная для скачанной задачи и для чужой папки.

    issue #1018: совет «запустите загрузчик» был безусловным, и после скачивания
    шага без публичных тестов получался круг — загрузчик уже сказал «тесты не
    найдены», а грейдер отправлял к нему обратно. Повторное скачивание в этом
    случае ничего не меняет: тестов нет на стороне Stepik.

    Признак скачанной задачи — ``meta.json`` рядом с решением: его кладёт
    загрузчик, и вручную такой файл не появляется.
    """
    folder = solution.resolve().parent
    key = "test_dir_not_found_task" if (folder / "meta.json").is_file() else "test_dir_not_found"
    return ctx.t(key, name=solution.name, expected=str(folder / "tests"))


def _print_run_profile(output: str) -> None:
    """Шапка прогона: чем именно проверяли (issue #984).

    Условия, определяющие вердикт, были не видны нигде — пользователь не знал
    ни какой ``pyproject.toml`` применён, ни идёт ли исполнение в песочнице,
    поэтому расхождение вердиктов между двумя запусками объяснить было нечем.
    Печатается только в текстовом выводе: машинные форматы (json/csv/markdown)
    — это данные для пайплайна, лишняя строка их ломает.
    """
    if output != "text":
        return
    profile = current_profile()
    config_label = (
        str(profile.config_path)
        if profile.config_path is not None
        else _t("run_profile_config_default")
    )
    print(
        _t(
            "run_profile",
            runner=profile.describe(),
            timeout=profile.timeout_seconds,
            python=profile.python,
            config=config_label,
        )
    )


def _resolve_individual_test_dir(
    ctx: CliContext, path: pathlib.Path, directory: pathlib.Path
) -> pathlib.Path:
    """tests/ для одного решения в директории-режиме (2/3): по решению, иначе общий.

    Сначала ищет ``tests/`` рядом с самим решением (``resolve_test_dir``); при
    отсутствии откатывается на общий ``tests/`` директории.
    ``resolve_test_dir_from_input(is_dir=True)`` всегда возвращает ``Path``
    (никогда None), поэтому результат не-опционален. issue #354 — дедуп двух
    идентичных копий в режимах 2 и 3.
    """
    individual_test_dir = resolve_test_dir(path)
    if individual_test_dir is None or not individual_test_dir.is_dir():
        individual_test_dir = ctx.resolve_test_dir_from_input(directory, is_dir=True)
    assert individual_test_dir is not None
    return individual_test_dir


def _run_mode_1(
    ctx: CliContext,
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
    """Режим 1: проверить одно решение (verbose). Общий код для меню и --mode 1.

    Возвращает ``had_failures`` — были ли непройденные кейсы. Интерактивное меню
    (issue #430) решает по этому флагу, печатать ли однократный за сессию nudge
    «Подучить»; CLI ``--mode 1`` возврат игнорирует. ``False`` также на ранних
    выходах (файл/тесты не найдены — прогона не было).
    """
    if not solution.is_file():
        print(ctx.t("file_not_found", path=solution))
        return False

    test_dir = resolve_test_dir(solution)
    if test_dir is None or not test_dir.is_dir():
        print(_missing_tests_hint(ctx, solution))
        return False

    _print_run_profile(output)
    cache = GraderCache() if use_cache else None
    result, from_cache = _run_tests_maybe_cached(
        ctx, solution, test_dir, verbose=verbose, output=output, cache=cache
    )
    if cache is not None:
        cache.save()
    if from_cache and output == "text":
        print(ctx.t("cache_hit"))

    # issue #403: собрать lint один раз — и для истории, и для печати ниже.
    lint_by_sol = _collect_lint([solution]) if record_lint else None
    if record_stats:
        stats.record_run(1, _verdict_counts_from_cases(result["cases"]), result["total_time"])
    if record_history:
        history.record_run(
            1,
            history_recording.cases_from_test_results(result["cases"]),
            db_path=history_recording.default_history_db_path(),
            task_key=history.task_key_for(solution.parent, pathlib.Path.cwd()),
            solution_name=solution.name,
            solution_hash=hash_solution(solution),
            duration_s=result["total_time"],
            lint=history_recording.lint_records_from_violations(
                (lint_by_sol or {}).get(solution, [])
            )
            or None,
        )

    had_failures = _has_failures(result["cases"])

    if output == "json":
        print(json.dumps({"file": str(solution), **result}, ensure_ascii=False))
        return had_failures
    if output in ("csv", "markdown"):
        rows = [
            {
                "index": i,
                "passed": c["passed"],
                "verdict": c.get("verdict", ""),
                "time": c["time"],
                "memory": c["memory"],
                "error": c["error"],
            }
            for i, c in enumerate(result["cases"], start=1)
        ]
        ctx.print_tabular(output, rows, ["index", "passed", "verdict", "time", "memory", "error"])
        return had_failures

    col_file = 28
    print()
    base = solution.resolve().parent
    print_correctness_results([(solution, result)], base, col_file=col_file)
    if record_lint:
        _print_lint_blocks([solution], None, output, lint_by_sol)
    if ai_hints:
        _print_ai_hints([(solution, result)])
    return had_failures


def _run_mode_2(
    ctx: CliContext,
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
    """Режим 2: проверить все решения в папке. Общий код для меню и --mode 2.

    Возвращает ``had_failures`` (см. ``_run_mode_1``) — были ли непройденные
    кейсы среди всех решений; меню решает по нему про однократный nudge. ``False``
    на ранних выходах (папка/решения не найдены).
    """
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return False

    scripts = find_all_solution_files(directory)
    if not scripts:
        print(ctx.t("no_solutions_found"))
        return False

    col_file = max((len(_rel(p, directory)) for p in scripts), default=20) + 2

    _print_run_profile(output)
    rows: list[tuple[pathlib.Path, SolutionResult]] = []
    machine_output = output != "text"
    cache = GraderCache() if use_cache else None
    cache_hits = 0
    track = (
        scripts
        if machine_output
        else rich_track(scripts, description=_t("progress_checking_solutions"))
    )
    for path in track:
        individual_test_dir = _resolve_individual_test_dir(ctx, path, directory)
        result, from_cache = _run_tests_maybe_cached(
            ctx, path, individual_test_dir, verbose=verbose, output=output, cache=cache
        )
        cache_hits += int(from_cache)
        rows.append((path, result))

    if cache is not None:
        cache.save()

    # issue #403: собрать lint по всем решениям один раз — для истории и печати.
    lint_by_sol = _collect_lint([p for p, _ in rows]) if record_lint else None
    if record_stats or record_history:
        all_cases = [c for _, result in rows for c in result["cases"]]
        total_time = sum(result["total_time"] for _, result in rows)
        if record_stats:
            stats.record_run(2, _verdict_counts_from_cases(all_cases), total_time)
        if record_history:
            # Агрегатный прогон режима 2 → объединённые нарушения всех решений
            # (карточки «Подучить» агрегируют по rule_code, атрибуция по файлу тут
            # не нужна).
            all_violations = [v for sol in lint_by_sol.values() for v in sol] if lint_by_sol else []
            history.record_run(
                2,
                history_recording.cases_from_test_results(all_cases),
                db_path=history_recording.default_history_db_path(),
                task_key=history.task_key_for(directory, pathlib.Path.cwd()),
                duration_s=total_time,
                lint=history_recording.lint_records_from_violations(all_violations) or None,
            )

    had_failures = _has_failures([c for _, result in rows for c in result["cases"]])

    if output == "json":
        print(json.dumps({"results": {str(p): r for p, r in rows}}, ensure_ascii=False))
        return had_failures
    if output in ("csv", "markdown"):
        table_rows = [{"file": path, **result} for path, result in rows]
        fields = [
            "file",
            "total",
            "passed",
            "failed",
            "errors",
            "total_time",
            "avg_time",
            "peak_memory_mb",
            "first_fail",
        ]
        ctx.print_tabular(output, table_rows, fields)
        return had_failures

    print_correctness_results(rows, directory, col_file=col_file)
    if cache is not None:
        print(ctx.t("cache_summary", hits=cache_hits, total=len(rows)))
    if record_lint:
        _print_lint_blocks([p for p, _ in rows], directory, output, lint_by_sol)
    if ai_hints:
        _print_ai_hints(rows)
    return had_failures


def _run_mode_3(
    ctx: CliContext,
    directory: pathlib.Path,
    repeats: int,
    *,
    output: str = "text",
    record_stats: bool = False,
    record_history: bool = False,
    ai_hints: bool = False,
) -> None:
    """Режим 3: subprocess-бенчмарк папки. Общий код для меню и --mode 3."""
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return

    scripts = find_all_solution_files(directory)
    if not scripts:
        print(ctx.t("no_solutions_found"))
        return

    _print_run_profile(output)
    results: dict[pathlib.Path, BenchResult] = {}
    machine_output = output != "text"
    track = (
        scripts
        if machine_output
        else rich_track(scripts, description=_t("progress_benchmarking_solutions"))
    )
    for path in track:
        individual_test_dir = _resolve_individual_test_dir(ctx, path, directory)
        skip = _preflight_skip(ctx, path, individual_test_dir)
        if skip is not None:
            results[path] = skip
            continue
        results[path] = ctx.run_benchmark(path, individual_test_dir, repeats=repeats)

    bench_cfg = get_config()
    apply_relative_ranking(
        results,
        similar_threshold=bench_cfg.similar_threshold,
        much_slower_threshold=bench_cfg.much_slower_threshold,
    )

    if record_stats or record_history:
        # Bench-данные не несут единого "total_time" на решение (только
        # min/median/mean/max/stdev за один прогон + число прогонов) --
        # mean × runs — приближённая оценка суммарного времени решения.
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in results.values())
        if record_stats:
            stats.record_run(3, _verdict_counts_from_bench(results), total_time)
        if record_history:
            history.record_run(
                3,
                history_recording.cases_from_bench_results(results),
                db_path=history_recording.default_history_db_path(),
                task_key=history.task_key_for(directory, pathlib.Path.cwd()),
                duration_s=total_time,
            )

    if output == "json":
        print(json.dumps({"results": {str(p): d for p, d in results.items()}}, ensure_ascii=False))
        return
    if output in ("csv", "markdown"):
        table_rows = [{"file": path, **data} for path, data in sorted(results.items())]
        fields = [
            "file",
            "runs",
            "min",
            "median",
            "mean",
            "max",
            "stdev",
            "peak_memory_mb",
            "relative",
            "verdict",
            "error",
        ]
        ctx.print_tabular(output, table_rows, fields)
        return

    ok = {k: v for k, v in results.items() if not v.get("error")}

    col = max((len(_rel(p, directory)) for p in scripts), default=20) + 2
    ranked = sorted(ok.items(), key=lambda x: x[1]["median"])
    print_benchmark_results(ranked, directory, col_file=col)

    for path, data in sorted(results.items()):
        if data.get("error"):
            rel = _rel(path, directory)
            print(f"  {rel}: {data['error']}")

    if ai_hints:
        _print_ai_hints_bench(results, directory)


_MODE4_FIELDS = [
    "group",
    "file",
    "runs",
    "min",
    "median",
    "mean",
    "max",
    "stdev",
    "peak_memory_mb",
    "relative",
    "verdict",
    "error",
]


def _run_mode_4(
    ctx: CliContext,
    directory: pathlib.Path,
    number: int,
    *,
    output: str = "text",
    record_stats: bool = False,
    record_history: bool = False,
    ai_hints: bool = False,
) -> None:
    """Режим 4: timeit micro-bench папки. Общий код для меню и --mode 4."""
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return

    grouped = collect_grouped_files(directory)
    if not grouped:
        print(ctx.t("no_solutions_found"))
        return

    _print_run_profile(output)
    machine_output = output != "text"
    json_results: dict[str, dict[str, Any]] = {}
    table_rows: list[dict[str, Any]] = []
    printed_table = False
    all_bench_results: dict[pathlib.Path, BenchResult] = {}

    for folder, paths in sorted(grouped.items()):
        if folder != ".":
            folder_abs = directory / folder
        else:
            folder_abs = directory
        test_dir = ctx.resolve_test_dir_from_input(folder_abs, is_dir=True)

        label = folder if folder != "." else directory.name
        if not machine_output:
            print(ctx.t("micro_bench_header", label=label))

        # is_dir=True never actually returns None (see resolve_test_dir_from_input),
        # but its return type is Path | None -- check explicitly rather than assert,
        # since this path doesn't fall back to anything and must "continue" cleanly.
        if test_dir is None or not test_dir.is_dir():
            if output == "json":
                json_results[folder] = {"error": f"tests not found: {test_dir}"}
            elif output in ("csv", "markdown"):
                table_rows.append({"group": folder, "error": f"tests not found: {test_dir}"})
            else:
                print(ctx.t("tests_not_found", test_dir=test_dir))
                print(ctx.t("expected_tests_subfolder"))
            continue

        # issue #729: то же предусловие, что в режиме 3 — в замер идут только
        # решения, прошедшие тесты; остальные попадают в результат как SKIPPED.
        eligible: list[pathlib.Path] = []
        skipped: dict[pathlib.Path, BenchResult] = {}
        for sol in sorted(paths):
            skip = _preflight_skip(ctx, sol, test_dir)
            if skip is None:
                eligible.append(sol)
            else:
                skipped[sol] = skip
        bench = ctx.run_microbench_mode(eligible, test_dir, number=number)
        bench.update(skipped)
        all_bench_results.update(bench)

        if not bench:
            if output == "json":
                json_results[folder] = {"error": "no test cases found"}
            elif output in ("csv", "markdown"):
                table_rows.append({"group": folder, "error": "no test cases found"})
            else:
                print(ctx.t("no_test_cases_found", test_dir=test_dir))
            continue

        if output == "json":
            json_results[folder] = {"results": {str(p): d for p, d in bench.items()}}
            continue
        if output in ("csv", "markdown"):
            table_rows.extend(
                {"group": folder, "file": path, **data} for path, data in sorted(bench.items())
            )
            continue

        ok_rows = {k: v for k, v in bench.items() if not v.get("error")}

        col = max((len(_rel(p, directory)) for p in paths), default=20) + 2

        if ok_rows:
            ranked = sorted(ok_rows.items(), key=lambda x: x[1]["median"])
            # issue #66: режим 4 меряет Python-heap (tracemalloc), не RSS —
            # подпись колонки обязана это отражать.
            print_benchmark_results(ranked, directory, col_file=col, memory_header="Py-heap")
            printed_table = True

        for path, data in sorted(bench.items()):
            if data.get("error"):
                rel = _rel(path, directory)
                print(f"  ✗ {rel}: {data['error']}")

        if not ok_rows and not any(v.get("error") for v in bench.values()):
            print(ctx.t("no_results"))

    if (record_stats or record_history) and all_bench_results:
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in all_bench_results.values())
        if record_stats:
            stats.record_run(4, _verdict_counts_from_bench(all_bench_results), total_time)
        if record_history:
            history.record_run(
                4,
                history_recording.cases_from_bench_results(all_bench_results),
                db_path=history_recording.default_history_db_path(),
                task_key=history.task_key_for(directory, pathlib.Path.cwd()),
                duration_s=total_time,
            )

    if output == "json":
        print(json.dumps({"groups": json_results}, ensure_ascii=False))
    elif output in ("csv", "markdown"):
        ctx.print_tabular(output, table_rows, _MODE4_FIELDS)
    elif printed_table:
        # issue #66: сноска о методике "Py-heap" печатается один раз под всеми
        # группами, а не под каждой таблицей.
        print(ctx.t("micro_mem_note"))

    if ai_hints and not machine_output:
        _print_ai_hints_bench(all_bench_results, directory)
