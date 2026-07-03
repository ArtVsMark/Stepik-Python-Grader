"""grader.py — фасад для обратной совместимости.

Архитектурный слой: Application.
Реэкспортирует публичные и приватные (тестируемые напрямую) символы из
core/grader_core.py (загрузка/исполнение), core/reporter.py (вывод) и cli.py (меню).

С версии, где выполнен Issue #20 finding #4 / CLAUDE.md Sprint 7, сам файл
не содержит логики — она перенесена в три модуля выше. Инвариант обратной
совместимости: все имена из __all__, а также приватные имена, на которые
опирается тестовый набор (`grader._foo`), остаются доступны как `grader.X`.

Прямой запуск: python -m stepik_grader.grader (или консольная команда
`stepik-grader` после `pip install -e .`)
"""

from __future__ import annotations

from stepik_grader.core.grader_core import *  # noqa: F401, F403
from stepik_grader.core.grader_core import (
    ENCODING,
    MEASURE_CHILD_MEMORY,
    MICROBENCH_MAX_CASES,
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    TIMEOUT_SECONDS,
    _ast_function_name,
    _apply_run_mode_override,
    _build_call_wrapper,
    _build_function_wrapper,
    _detect_run_mode,
    _ExecutorRunResult,
    _is_python_code_block,
    _is_safe_constant,
    _measure_peak_memory,
    _micro_stats,
    _normalize_output_line,
    _parse_testblock_file,
    _read_meta_function_name,
    _SOLUTION_FILE_RE,
    _verdict,
    apply_relative_ranking,
    run_microbench,
)
from stepik_grader.core.reporter import *  # noqa: F401, F403
from stepik_grader.core.reporter import (
    Console,
    Table,
    Text,
    _console,
    _correctness_status,
    _cprint,
    _RICH,
    _SEP,
    _STATUS_COLORS,
    _VERDICT_COLORS,
)
from stepik_grader.cli import (
    __version__,
    _ask_bench_profile,
    _ask_micro_profile,
    _ask_number,
    _BENCH_PROFILES,
    _interactive_menu,
    _MICRO_PROFILES,
    _print_menu,
    _resolve_test_dir_from_input,
    main,
)

__all__ = [
    "TestCase",
    "is_function_only_solution",
    "is_solution_file",
    "find_all_solution_files",
    "collect_grouped_files",
    "load_test_cases",
    "load_text_lines",
    "run_single_test",
    "run_tests",
    "run_benchmark",
    "run_microbench_mode",
    "format_correctness_row",
    "print_correctness_header",
    "print_correctness_results",
    "format_benchmark_row",
    "print_benchmark_header",
    "print_benchmark_results",
    "TIMEOUT_SECONDS",
    "ENCODING",
    "SIMILAR_THRESHOLD",
    "MUCH_SLOWER_THRESHOLD",
    "MEASURE_CHILD_MEMORY",
    "MICROBENCH_MAX_CASES",
]

if __name__ == "__main__":
    main()
