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

import json
import pathlib
from typing import Any

from stepik_grader.cli.context import CliContext
from stepik_grader.core import stats
from stepik_grader.core.cache import GraderCache, hash_solution, hash_tests
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    collect_grouped_files,
    find_all_solution_files,
    resolve_test_dir,
)
from stepik_grader.core.microbench_runner import apply_relative_ranking
from stepik_grader.core.reporter import (
    print_benchmark_results,
    print_case_verbose,
    print_correctness_results,
    rich_track,
)


def _rel(path: pathlib.Path, base: pathlib.Path) -> str:
    """Относительный путь для колонок таблиц (с ``..`` при выходе за ``base``).

    Прямая замена ``os.path.relpath`` на pathlib (issue #354): лексический
    расчёт без обращения к ФС, ``walk_up=True`` разрешает ``..`` (Python 3.12+).
    """
    return str(path.relative_to(base, walk_up=True))


def _verdict_counts_from_cases(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Тальи вердиктов кейсов для режимов 1/2 (issue #268 — статистика)."""
    counts: dict[str, int] = {}
    for c in cases:
        verdict = c.get("verdict") or ("AC" if c.get("passed") else "WA")
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def _verdict_counts_from_bench(results: dict[pathlib.Path, dict[str, Any]]) -> dict[str, int]:
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


__all__ = [
    "_run_mode_1",
    "_run_mode_2",
    "_run_mode_3",
    "_run_mode_4",
    "_run_tests_maybe_cached",
    "_verdict_counts_from_cases",
    "_verdict_counts_from_bench",
]


def _run_tests_maybe_cached(
    ctx: CliContext,
    solution: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    verbose: bool,
    output: str,
    cache: GraderCache | None,
) -> tuple[dict[str, Any], bool]:
    """Прогнать тесты, при активном кэше — переиспользуя актуальную запись.

    Возвращает пару (result, from_cache). Ключ кэша — sha256 содержимого
    решения и sha256 всех файлов тест-директории (issue #56). При промахе
    результат кладётся в кэш (в память; ``cache.save()`` — забота вызывающей
    стороны, чтобы для пачки решений писать файл один раз). На попадании
    per-case verbose-вывод не печатается — тесты не запускались.
    """
    callback = print_case_verbose if (verbose and output == "text") else None
    if cache is None:
        result = ctx.run_tests(solution, test_dir, verbose=verbose, verbose_callback=callback)
        return result, False

    solution_sha = hash_solution(solution)
    tests_sha = hash_tests(test_dir)
    cached = cache.get(solution, solution_sha, tests_sha)
    if cached is not None:
        return cached, True

    result = ctx.run_tests(solution, test_dir, verbose=verbose, verbose_callback=callback)
    cache.put(solution, solution_sha, tests_sha, result)
    return result, False


def _run_mode_1(
    ctx: CliContext,
    solution: pathlib.Path,
    *,
    verbose: bool = True,
    output: str = "text",
    use_cache: bool = False,
    record_stats: bool = False,
) -> None:
    """Режим 1: проверить одно решение (verbose). Общий код для меню и --mode 1."""
    if not solution.is_file():
        print(ctx.t("file_not_found", path=solution))
        return

    test_dir = resolve_test_dir(solution)
    if test_dir is None or not test_dir.is_dir():
        print(
            ctx.t(
                "test_dir_not_found",
                name=solution.name,
                expected=str(solution.resolve().parent / "tests"),
            )
        )
        return

    cache = GraderCache() if use_cache else None
    result, from_cache = _run_tests_maybe_cached(
        ctx, solution, test_dir, verbose=verbose, output=output, cache=cache
    )
    if cache is not None:
        cache.save()
    if from_cache and output == "text":
        print(ctx.t("cache_hit"))

    if record_stats:
        stats.record_run(1, _verdict_counts_from_cases(result["cases"]), result["total_time"])

    if output == "json":
        print(json.dumps({"file": str(solution), **result}, ensure_ascii=False))
        return
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
        return

    col_file = 28
    print()
    base = solution.resolve().parent
    print_correctness_results([(solution, result)], base, col_file=col_file)


def _run_mode_2(
    ctx: CliContext,
    directory: pathlib.Path,
    *,
    verbose: bool = False,
    output: str = "text",
    use_cache: bool = False,
    record_stats: bool = False,
) -> None:
    """Режим 2: проверить все решения в папке. Общий код для меню и --mode 2."""
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return

    scripts = find_all_solution_files(directory)
    if not scripts:
        print(ctx.t("no_solutions_found"))
        return

    col_file = max((len(_rel(p, directory)) for p in scripts), default=20) + 2

    rows: list[tuple[pathlib.Path, dict[str, Any]]] = []
    machine_output = output != "text"
    cache = GraderCache() if use_cache else None
    cache_hits = 0
    track = scripts if machine_output else rich_track(scripts, description="Проверка решений...")
    for path in track:
        individual_test_dir = resolve_test_dir(path)
        if individual_test_dir is None or not individual_test_dir.is_dir():
            individual_test_dir = ctx.resolve_test_dir_from_input(directory, is_dir=True)
        # ctx.resolve_test_dir_from_input(is_dir=True) always returns a Path (never
        # the None its is_dir=False passthrough branch can produce) -- narrows for mypy.
        assert individual_test_dir is not None
        result, from_cache = _run_tests_maybe_cached(
            ctx, path, individual_test_dir, verbose=verbose, output=output, cache=cache
        )
        cache_hits += int(from_cache)
        rows.append((path, result))

    if cache is not None:
        cache.save()

    if record_stats:
        all_cases = [c for _, result in rows for c in result["cases"]]
        total_time = sum(result["total_time"] for _, result in rows)
        stats.record_run(2, _verdict_counts_from_cases(all_cases), total_time)

    if output == "json":
        print(json.dumps({"results": {str(p): r for p, r in rows}}, ensure_ascii=False))
        return
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
        return

    print_correctness_results(rows, directory, col_file=col_file)
    if cache is not None:
        print(ctx.t("cache_summary", hits=cache_hits, total=len(rows)))


def _run_mode_3(
    ctx: CliContext,
    directory: pathlib.Path,
    repeats: int,
    *,
    output: str = "text",
    record_stats: bool = False,
) -> None:
    """Режим 3: subprocess-бенчмарк папки. Общий код для меню и --mode 3."""
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return

    scripts = find_all_solution_files(directory)
    if not scripts:
        print(ctx.t("no_solutions_found"))
        return

    results: dict[pathlib.Path, dict[str, Any]] = {}
    machine_output = output != "text"
    track = scripts if machine_output else rich_track(scripts, description="Бенчмарк решений...")
    for path in track:
        individual_test_dir = resolve_test_dir(path)
        if individual_test_dir is None or not individual_test_dir.is_dir():
            individual_test_dir = ctx.resolve_test_dir_from_input(directory, is_dir=True)
        # ctx.resolve_test_dir_from_input(is_dir=True) always returns a Path (never
        # the None its is_dir=False passthrough branch can produce) -- narrows for mypy.
        assert individual_test_dir is not None
        results[path] = ctx.run_benchmark(path, individual_test_dir, repeats=repeats)

    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    if record_stats:
        # Bench-данные не несут единого "total_time" на решение (только
        # min/median/mean/max/stdev за один прогон + число прогонов) --
        # mean × runs — приближённая оценка суммарного времени решения.
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in results.values())
        stats.record_run(3, _verdict_counts_from_bench(results), total_time)

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
) -> None:
    """Режим 4: timeit micro-bench папки. Общий код для меню и --mode 4."""
    if not directory.is_dir():
        print(ctx.t("dir_not_found", path=directory))
        return

    grouped = collect_grouped_files(directory)
    if not grouped:
        print(ctx.t("no_solutions_found"))
        return

    machine_output = output != "text"
    json_results: dict[str, dict[str, Any]] = {}
    table_rows: list[dict[str, Any]] = []
    printed_table = False
    all_bench_results: dict[pathlib.Path, dict[str, Any]] = {}

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

        bench = ctx.run_microbench_mode(sorted(paths), test_dir, number=number)
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

    if record_stats and all_bench_results:
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in all_bench_results.values())
        stats.record_run(4, _verdict_counts_from_bench(all_bench_results), total_time)

    if output == "json":
        print(json.dumps({"groups": json_results}, ensure_ascii=False))
    elif output in ("csv", "markdown"):
        ctx.print_tabular(output, table_rows, _MODE4_FIELDS)
    elif printed_table:
        # issue #66: сноска о методике "Py-heap" печатается один раз под всеми
        # группами, а не под каждой таблицей.
        print(ctx.t("micro_mem_note"))
