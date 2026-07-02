"""cli.py — интерактивное меню грейдера (режимы 0-4).

Архитектурный слой: Application / CLI.
Оркестрирует grader_core (загрузка/исполнение) и reporter (вывод таблиц) —
не содержит собственной бизнес-логики запуска решений.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 3).
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

from core.microbench_runner import apply_relative_ranking
from grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    _resolve_test_dir,
    collect_grouped_files,
    find_all_solution_files,
    run_benchmark,
    run_microbench_mode,
    run_tests,
)
from reporter import _rich_track, print_benchmark_results, print_correctness_results

__all__ = ["main"]

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
    print("  Load profiles (repeats per solution):")
    print("    1  low       —   5 runs")
    print("    2  medium    —  15 runs")
    print("    3  high      —  50 runs")
    print("    4  custom    —  5–100 runs")
    choice = input("  Select profile [2]: ").strip() or "2"
    repeats = _BENCH_PROFILES.get(choice)
    if repeats is None:
        repeats = _BENCH_PROFILES["2"]
    if repeats == 0:
        repeats = _ask_number("  Enter repeats (5–100): ", default=15)
        repeats = max(5, min(100, repeats))
    return repeats


def _ask_micro_profile() -> int:
    """Запросить профиль нагрузки для timeit micro-bench (режим 4)."""
    print("  Load profiles (calls per run):")
    print("    1  fast      —     500")
    print("    2  normal    —   1 000")
    print("    3  thorough  —   5 000")
    print("    4  deep      —  50 000")
    print("    5  hard      — 100 000  (short deterministic functions only)")
    print("    6  custom    — 100–500 000")
    choice = input("  Select profile [2]: ").strip() or "2"
    number = _MICRO_PROFILES.get(choice)
    if number is None:
        number = _MICRO_PROFILES["2"]
    if number == 0:
        number = _ask_number("  Enter calls (100–500 000): ", default=1000)
        number = max(100, min(500_000, number))
    return number


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------


def _print_menu() -> None:
    print("\n" + "=" * 50)
    print("  Stepik Python Grader")
    print("=" * 50)
    print("  1. Check one solution")
    print("  2. Check all solutions in folder")
    print("  3. Benchmark solutions in folder")
    print("  4. Micro-benchmark (timeit) for folder")
    print("  0. Exit")
    print("=" * 50)


def _ask_number(prompt: str, *, default: int) -> int:
    raw = input(prompt).strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _resolve_test_dir_from_input(solution_or_dir: str, *, is_dir: bool = False) -> str:
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
    return _resolve_test_dir(solution_or_dir)


def _interactive_menu() -> None:
    """Показать меню один раз, выполнить выбранный режим и завершить работу."""
    _print_menu()
    choice = input("Select mode [0-4]: ").strip()

    if choice == "0":
        print("Goodbye!")
        return

    if choice == "1":
        solution = input("Enter path to solution file: ").strip()
        if not pathlib.Path(solution).is_file():
            print(f"File not found: {solution}")
            return

        test_dir = _resolve_test_dir(solution)
        if not pathlib.Path(test_dir).is_dir():
            print(f"Test directory not found: {test_dir}")
            return

        result = run_tests(solution, test_dir, verbose=True)

        col_file = 28
        print()
        base = pathlib.Path(solution).resolve().parent.as_posix()
        print_correctness_results([(solution, result)], base, col_file=col_file)

    elif choice == "2":
        directory = input("Enter path to folder: ").strip()
        if not pathlib.Path(directory).is_dir():
            print(f"Directory not found: {directory}")
            return

        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        col_file = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2

        rows: list[tuple[str, dict[str, Any]]] = []
        for path in _rich_track(scripts, description="Проверка решений..."):
            individual_test_dir = _resolve_test_dir(path)
            if not pathlib.Path(individual_test_dir).is_dir():
                individual_test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            result = run_tests(path, individual_test_dir, verbose=False)
            rows.append((path, result))
        print_correctness_results(rows, directory, col_file=col_file)

    elif choice == "3":
        directory = input("Enter path to folder: ").strip()
        if not pathlib.Path(directory).is_dir():
            print(f"Directory not found: {directory}")
            return

        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        repeats = _ask_bench_profile()

        results: dict[str, dict[str, Any]] = {}
        for path in _rich_track(scripts, description="Бенчмарк решений..."):
            individual_test_dir = _resolve_test_dir(path)
            if not pathlib.Path(individual_test_dir).is_dir():
                individual_test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            results[path] = run_benchmark(path, individual_test_dir, repeats=repeats)

        apply_relative_ranking(
            results,
            similar_threshold=SIMILAR_THRESHOLD,
            much_slower_threshold=MUCH_SLOWER_THRESHOLD,
        )
        ok = {k: v for k, v in results.items() if not v.get("error")}

        col = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2
        ranked = sorted(ok.items(), key=lambda x: x[1]["median"])
        print_benchmark_results(ranked, directory, col_file=col)

        for path, data in sorted(results.items()):
            if data.get("error"):
                rel = os.path.relpath(path, directory)
                print(f"  {rel}: {data['error']}")

    elif choice == "4":
        directory = input("Enter path to folder with solutions: ").strip()
        if not pathlib.Path(directory).is_dir():
            print(f"Directory not found: {directory}")
            return

        number = _ask_micro_profile()

        grouped = collect_grouped_files(directory)
        if not grouped:
            print("No solution files found.")
            return

        for folder, paths in sorted(grouped.items()):
            if folder != ".":
                folder_abs = pathlib.Path(directory) / folder
            else:
                folder_abs = pathlib.Path(directory)
            test_dir = _resolve_test_dir_from_input(str(folder_abs), is_dir=True)

            label = folder if folder != "." else pathlib.Path(directory).name
            print(f"\n⚡ Micro-bench (timeit): {label}")

            if not pathlib.Path(test_dir).is_dir():
                print(f"  ⚠ Tests not found: {test_dir}")
                print("  Expected: tests/ subfolder next to solution files.")
                continue

            bench = run_microbench_mode(sorted(paths), test_dir, number=number)

            if not bench:
                print("  ⚠ No test cases found in:", test_dir)
                continue

            ok_rows = {k: v for k, v in bench.items() if not v.get("error")}

            col = max((len(os.path.relpath(p, directory)) for p in paths), default=20) + 2

            if ok_rows:
                ranked = sorted(ok_rows.items(), key=lambda x: x[1]["median"])
                print_benchmark_results(ranked, directory, col_file=col)

            for path, data in sorted(bench.items()):
                if data.get("error"):
                    rel = os.path.relpath(path, directory)
                    print(f"  ✗ {rel}: {data['error']}")

            if not ok_rows and not any(v.get("error") for v in bench.values()):
                print("  No results.")

    else:
        print("Unknown choice. Please enter 0–4.")


def main() -> None:
    """Точка входа CLI: показать меню и выполнить выбранный режим."""
    _interactive_menu()
