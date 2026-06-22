"""microbench_runner.py — timeit-based microbenchmark for function-only solutions.

Only function-only solutions (no module-level input() calls) are supported,
because timeit runs code inside the same process.  For subprocess-based
solutions use mode 3 (benchmark) in test.py instead.

Typical usage from test.py (mode 4):
    result = run_microbench(source_code, test_args_list, file_label, repeats)
    results = apply_relative_micro(results)
    print_microbench_table(task_folder, results)
"""

from __future__ import annotations

import ast
import statistics
import timeit
import traceback
from dataclasses import dataclass, field

SIMILAR_THRESHOLD_PERCENT = 5.0


@dataclass
class MicrobenchResult:
    """Timing result for a single function-only solution file."""

    file: str
    func_name: str
    repeats: int
    timings: list[float] = field(default_factory=list)
    error: str = ""
    relative_percent: float = 100.0
    verdict: str = "OK"

    # ------------------------------------------------------------------
    # Derived statistics (computed from timings list)
    # ------------------------------------------------------------------

    @property
    def min_time(self) -> float:
        return min(self.timings) if self.timings else 0.0

    @property
    def median_time(self) -> float:
        return statistics.median(self.timings) if self.timings else 0.0

    @property
    def mean_time(self) -> float:
        return statistics.mean(self.timings) if self.timings else 0.0

    @property
    def max_time(self) -> float:
        return max(self.timings) if self.timings else 0.0

    @property
    def stdev_time(self) -> float:
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0


def _find_callable(namespace: dict, source_code: str) -> tuple[str, object] | tuple[None, None]:
    """Return (name, callable) for the first public function defined in source_code."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None, None

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if name in namespace and callable(namespace[name]):
                return name, namespace[name]

    return None, None


def run_microbench(
    source_code: str,
    test_args_list: list[tuple],
    file_label: str,
    repeats: int = 1_000,
) -> MicrobenchResult:
    """Execute timeit microbenchmark for a function-only solution.

    Parameters
    ----------
    source_code:
        Full Python source of the solution (must be function-only).
    test_args_list:
        List of argument tuples extracted from the test cases.
        Each tuple is unpacked and passed to the function.
        Example: [(1, 2), (3, 4)] for a function f(a, b).
    file_label:
        Short label shown in the results table (usually the relative path).
    repeats:
        Number of timeit calls *per argument tuple*.  The total number of
        timings stored in MicrobenchResult.timings = repeats x len(test_args_list).
    """
    namespace: dict = {}

    # Compile and exec the source once — amortises import overhead
    try:
        exec(compile(source_code, file_label, "exec"), namespace)  # noqa: S102
    except Exception as exc:
        return MicrobenchResult(
            file=file_label,
            func_name="<compile error>",
            repeats=repeats,
            error=f"{type(exc).__name__}: {exc}",
        )

    func_name, func = _find_callable(namespace, source_code)
    if func is None:
        return MicrobenchResult(
            file=file_label,
            func_name="<no function>",
            repeats=repeats,
            error="No callable function found in source.",
        )

    timings: list[float] = []

    for args in test_args_list:
        try:
            # timeit returns total time for `number` calls; divide to get per-call time
            total = timeit.timeit(lambda a=args: func(*a), number=repeats)
            per_call = total / repeats
            timings.append(per_call)
        except Exception as exc:
            tb = traceback.format_exc(limit=3)
            return MicrobenchResult(
                file=file_label,
                func_name=func_name,
                repeats=repeats,
                error=f"{type(exc).__name__}: {exc}\n{tb}",
            )

    return MicrobenchResult(
        file=file_label,
        func_name=func_name,
        repeats=repeats,
        timings=timings,
    )


def apply_relative_micro(results: list[MicrobenchResult]) -> list[MicrobenchResult]:
    """Set relative_percent and verdict on each result relative to the fastest."""
    valid = [r for r in results if r.timings and not r.error]
    if not valid:
        return results

    best = min(r.median_time for r in valid)

    for r in valid:
        r.relative_percent = (r.median_time / best) * 100 if best > 0 else 100.0
        delta = r.relative_percent - 100

        if delta <= SIMILAR_THRESHOLD_PERCENT:
            r.verdict = "SIMILAR"
        elif delta <= 15:
            r.verdict = "SLOWER"
        else:
            r.verdict = "MUCH SLOWER"

    for r in results:
        if r.error:
            r.verdict = "ERROR"

    return results
