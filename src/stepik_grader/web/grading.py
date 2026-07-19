"""grading.py — фасад доступа web-слоя к исполнительному ядру грейдера (ADR-0010).

Единственная точка, где web-слой касается core-поверхности грейдинга: агрегаторы
режимов (``run_tests``/``run_benchmark``/``run_microbench_mode``), запуск одиночного
``RunSpec`` для песочницы (``run_spec``), ранжирование, резолюция тестов, трейс и
форматирование времени. ``viewmodels``/``runs``/``playground`` берут эти примитивы
ОТСЮДА, а не из ``core.*`` напрямую — граница web↔core сведена в один модуль и
охраняется ``test_import_dag`` (allowlist публичной поверхности core, issue #549).

Адаптеры (``web/*_adapter.py``) — отдельный сервисный слой и обращаются к своим
core-модулям сами (ADR-0010); этот фасад про grade/bench/microbench-вход, не про них.

Модуль тонкий по замыслу — только реэкспорт публичной поверхности core, без
собственной логики: он фиксирует ГРАНИЦУ, а не добавляет уровень абстракции.
"""

from __future__ import annotations

from stepik_grader.core.cache import hash_solution
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    run_benchmark,
    run_microbench_mode,
    run_spec,
    run_tests,
    set_runner,
)
from stepik_grader.core.microbench_runner import (
    apply_reference_ranking,
    apply_relative_ranking,
)
from stepik_grader.core.reporter import fmt_time
from stepik_grader.core.runner import RunSpec
from stepik_grader.core.test_loader import (
    collect_grouped_files,
    find_all_solution_files,
    is_solution_file,
    load_test_cases,
    resolve_test_dir,
)
from stepik_grader.core.tracer import trace_code

__all__ = [
    "MUCH_SLOWER_THRESHOLD",
    "SIMILAR_THRESHOLD",
    "RunSpec",
    "apply_reference_ranking",
    "apply_relative_ranking",
    "collect_grouped_files",
    "find_all_solution_files",
    "fmt_time",
    "hash_solution",
    "is_solution_file",
    "load_test_cases",
    "resolve_test_dir",
    "run_benchmark",
    "run_microbench_mode",
    "run_spec",
    "run_tests",
    "set_runner",
    "trace_code",
]
