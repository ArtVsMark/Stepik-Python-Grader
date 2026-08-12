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

from stepik_grader.core.grader_core import *
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
from stepik_grader.core.reporter import *
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
    run_cli,
)

__all__ = [
    "ENCODING",
    "MEASURE_CHILD_MEMORY",
    "MICROBENCH_MAX_CASES",
    "MUCH_SLOWER_THRESHOLD",
    "SIMILAR_THRESHOLD",
    "TIMEOUT_SECONDS",
    "TestCase",
    "collect_grouped_files",
    "find_all_solution_files",
    "format_benchmark_row",
    "format_correctness_row",
    "is_function_only_solution",
    "is_solution_file",
    "load_test_cases",
    "load_text_lines",
    "print_benchmark_header",
    "print_benchmark_results",
    "print_correctness_header",
    "print_correctness_results",
    "resolve_test_dir",
    "run_benchmark",
    "run_microbench_mode",
    "run_single_test",
    "run_tests",
    "set_runner",
]

if __name__ == "__main__":
    # issue #936: код исхода прогона становится статусом процесса.
    run_cli()
