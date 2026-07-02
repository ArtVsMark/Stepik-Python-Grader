"""Tests for the microbench_runner.py module functions directly.

microbench_runner.py is now LIVE — grader.py imports run_microbench from it and
calls it from run_microbench_mode (stdin path). These tests exercise the module's
public surface directly.

run_microbench runs the solution as a real subprocess (python -c) and redirects
the solution's stdout to os.devnull during the timeit.repeat call (repeat=5), so
printed output never leaks into the parsed timings. Its signature is
(source_code, *, stdin_data: str, number) and it returns a dict with keys
'times' (list[float]) and 'error' (str).

The module also exposes the MicrobenchResult dataclass and apply_relative_micro
helper for aggregating/ranking per-file timings; those are covered below and in
tests/test_microbench.py.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from core import microbench_runner
from core.microbench_runner import (
    MicrobenchResult,
    apply_relative_micro,
    apply_relative_ranking,
    run_microbench,
)


def test_microbench_runner_basic_timing() -> None:
    """Basic timing returns exactly 5 positive per-call floats (timeit repeat=5)."""
    result = run_microbench("x = sum(range(50))\n", stdin_data="", number=5)
    assert result["error"] == ""
    assert len(result["times"]) == 5
    assert all(t > 0 for t in result["times"])


def test_microbench_runner_with_stdin() -> None:
    """A solution reading stdin times cleanly with stdin_data provided."""
    result = run_microbench("n = int(input())\nprint(n)\n", stdin_data="42\n", number=3)
    assert result["error"] == ""
    assert len(result["times"]) == 5


def test_microbench_runner_number_parameter() -> None:
    """The number= parameter is accepted; shape stays 5 timings regardless of size."""
    small = run_microbench("y = 2 * 2\n", stdin_data="", number=5)
    large = run_microbench("y = 2 * 2\n", stdin_data="", number=500)
    assert small["error"] == "" and large["error"] == ""
    assert len(small["times"]) == 5
    assert len(large["times"]) == 5


def test_microbench_runner_runtime_error_captured() -> None:
    """A runtime exception in the solution is captured as result['error']."""
    result = run_microbench("raise ValueError('boom')\n", stdin_data="", number=2)
    assert result["error"]
    assert "ValueError" in result["error"]
    assert result["times"] == []


def test_microbench_runner_stdout_suppressed() -> None:
    """run_microbench redirects the solution's stdout to devnull during timing.

    A loud (printing) solution still yields exactly 5 clean timings — the printed
    line never lands among the parsed timing numbers.
    """
    result = run_microbench("print('noise from solution')\nz = 1 + 1\n", stdin_data="", number=3)
    assert result["error"] == ""
    assert len(result["times"]) == 5
    assert all(0.0 < t < 1.0 for t in result["times"])


def test_microbench_runner_timeout_returns_error() -> None:
    """subprocess.TimeoutExpired покрывает строки 158–159."""
    with patch("core.microbench_runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=60)
        result = run_microbench("x = 1\n", stdin_data="", number=5)
    assert result["times"] == []
    assert result["error"] == "microbench timeout"


def test_microbench_runner_unexpected_exception_returns_error() -> None:
    """OSError покрывает строки 160–161."""
    with patch("core.microbench_runner.subprocess.run") as mock_run:
        mock_run.side_effect = OSError("no such file")
        result = run_microbench("x = 1\n", stdin_data="", number=5)
    assert result["times"] == []
    assert "no such file" in result["error"]


def test_microbench_runner_apply_relative_orders_by_median() -> None:
    """apply_relative_micro labels the fastest SIMILAR and slower ones SLOWER/MUCH SLOWER.

    REFACTORING INVARIANT: any merged verdict logic must keep the fastest at
    relative_percent == 100.0 and verdict SIMILAR.
    """
    fast = MicrobenchResult(file="fast.py", repeats=10, timings=[0.001])
    slow = MicrobenchResult(file="slow.py", repeats=10, timings=[0.010])
    out = apply_relative_micro([fast, slow])
    assert out[0].verdict == "SIMILAR"
    assert out[0].relative_percent == 100.0
    assert out[1].verdict == "MUCH SLOWER"
    assert out[1].relative_percent > 100.0


def test_microbench_runner_apply_relative_marks_errors() -> None:
    """Results carrying an error are labeled ERROR by apply_relative_micro."""
    good = MicrobenchResult(file="good.py", repeats=10, timings=[0.001])
    bad = MicrobenchResult(file="bad.py", repeats=10, error="SyntaxError: x")
    out = apply_relative_micro([good, bad])
    verdicts = {r.file: r.verdict for r in out}
    assert verdicts["bad.py"] == "ERROR"
    assert verdicts["good.py"] == "SIMILAR"


def test_microbench_runner_apply_relative_best_is_zero() -> None:
    """best == 0 покрывает строку 181: relative_percent = 100.0 вместо деления."""
    r1 = MicrobenchResult(file="a.py", repeats=5, timings=[0.0])
    r2 = MicrobenchResult(file="b.py", repeats=5, timings=[0.0])
    out = apply_relative_micro([r1, r2])
    assert all(r.relative_percent == 100.0 for r in out)


def test_microbench_runner_module_constants() -> None:
    """Module exposes the threshold and warmup constants used by the verdict logic."""
    assert microbench_runner.SIMILAR_THRESHOLD_PERCENT == 5.0
    assert isinstance(microbench_runner.WARMUP_RUNS, int)
    assert microbench_runner.WARMUP_RUNS >= 1


# ---------------------------------------------------------------------------
# apply_relative_ranking — shared by grader.py mode 3 and mode 4 (Issue #20 #6)
# ---------------------------------------------------------------------------


def test_apply_relative_ranking_empty_results_is_noop() -> None:
    """An empty or all-error results dict is left untouched."""
    results: dict[str, dict] = {}
    apply_relative_ranking(results, similar_threshold=1.15, much_slower_threshold=1.5)
    assert results == {}

    only_errors = {"a.py": {"error": "boom"}}
    apply_relative_ranking(only_errors, similar_threshold=1.15, much_slower_threshold=1.5)
    assert only_errors == {"a.py": {"error": "boom"}}


def test_apply_relative_ranking_labels_all_three_verdicts() -> None:
    """Fastest is SIMILAR, moderately slower is SLOWER, much slower is MUCH_SLOWER."""
    results = {
        "fast.py": {"median": 1.0},
        "slower.py": {"median": 1.3},
        "much_slower.py": {"median": 2.0},
        "broken.py": {"error": "SyntaxError"},
    }
    apply_relative_ranking(results, similar_threshold=1.15, much_slower_threshold=1.5)

    assert results["fast.py"]["verdict"] == "SIMILAR"
    assert results["fast.py"]["relative"] == 1.0
    assert results["slower.py"]["verdict"] == "SLOWER"
    assert results["much_slower.py"]["verdict"] == "MUCH_SLOWER"
    assert "verdict" not in results["broken.py"]


def test_apply_relative_ranking_zero_median_defaults_to_similar() -> None:
    """min_median == 0 avoids division by zero and treats results as equally fast."""
    results = {"a.py": {"median": 0.0}, "b.py": {"median": 0.0}}
    apply_relative_ranking(results, similar_threshold=1.15, much_slower_threshold=1.5)
    assert all(v["relative"] == 1.0 and v["verdict"] == "SIMILAR" for v in results.values())
