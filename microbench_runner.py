"""microbench_runner.py — timeit-based microbenchmark for function-only solutions.

Used by test.py mode 4.  Only solutions that define at least one callable
function (detected by is_function_only_solution) are eligible.

The module is intentionally kept small: all display logic lives in test.py.
"""
from __future__ import annotations

import statistics
import timeit
from dataclasses import dataclass, field
from typing import Any


DEFAULT_REPEATS = 1_000


@dataclass
class MicrobenchResult:
    file: str
    func_name: str
    repeats: int
    timings: list[float] = field(default_factory=list)  # per-call seconds
    error: str = ""

    # Filled by apply_relative_micro() after all files are measured
    relative_percent: float = 100.0
    verdict: str = "OK"

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


def _find_entry_function(namespace: dict[str, Any]) -> tuple[str, Any] | None:
    """Return (name, callable) for the first non-dunder callable in *namespace*."""
    for name, obj in namespace.items():
        if callable(obj) and not name.startswith("_"):
            return name, obj
    return None


def _safe_call(func: Any, args: tuple[Any, ...]) -> None:
    """Call func with args, silently catching TypeError (wrong signature).

    If the function raises TypeError on the first attempt, retry with no
    arguments so microbench can still measure *something* instead of crashing.
    """
    try:
        func(*args)
    except TypeError:
        func()


def run_microbench(
    source_code: str,
    test_args_list: list[tuple[Any, ...]],
    file_label: str,
    repeats: int = DEFAULT_REPEATS,
) -> MicrobenchResult:
    """Execute *source_code* once to define functions, then timeit the entry function.

    Parameters
    ----------
    source_code:
        Full source of the solution (imports + function definitions).
    test_args_list:
        List of positional-argument tuples to pass on each call, e.g.
        ``[("2020-01-01", "2020-03-01"), ...]``.  Each tuple is used in
        round-robin order.
    file_label:
        Short label for the result (relative path of the solution file).
    repeats:
        Total number of timeit calls to distribute across test_args_list.
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source_code, file_label, "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        return MicrobenchResult(file=file_label, func_name="?", repeats=repeats, error=repr(exc))

    found = _find_entry_function(namespace)
    if found is None:
        return MicrobenchResult(
            file=file_label,
            func_name="?",
            repeats=repeats,
            error="No public callable found in source.",
        )

    func_name, func = found

    if not test_args_list:
        test_args_list = [()]

    n = len(test_args_list)
    timings: list[float] = []

    for i in range(repeats):
        # Fix: capture args by value via default argument, not by closure reference
        captured_args = test_args_list[i % n]
        elapsed = timeit.timeit(
            lambda f=func, a=captured_args: _safe_call(f, a),
            number=1,
        )
        timings.append(elapsed)

    return MicrobenchResult(
        file=file_label,
        func_name=func_name,
        repeats=repeats,
        timings=timings,
    )


SIMILAR_THRESHOLD_PERCENT = 5.0


def apply_relative_micro(
    results: list[MicrobenchResult],
    threshold: float = SIMILAR_THRESHOLD_PERCENT,
) -> list[MicrobenchResult]:
    """Set .relative_percent and .verdict relative to the fastest median."""
    valid = [r for r in results if r.timings]
    if not valid:
        return results

    best = min(r.median_time for r in valid)

    for r in results:
        if not r.timings:
            r.verdict = "ERROR"
            continue
        r.relative_percent = (r.median_time / best * 100) if best > 0 else 100.0
        delta = r.relative_percent - 100
        if delta <= threshold:
            r.verdict = "SIMILAR"
        elif delta <= 15:
            r.verdict = "SLOWER"
        else:
            r.verdict = "MUCH SLOWER"

    return results
