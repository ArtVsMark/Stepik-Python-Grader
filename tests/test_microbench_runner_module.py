"""Tests for the microbench_runner.py module functions directly.

microbench_runner.py is currently DEAD CODE — grader.py uses its own inline
run_microbench / run_microbench_mode instead. These tests document the module's
behavior so a future refactoring that merges it into grader.py can verify the
merged version keeps (or intentionally changes) each behavior.

KEY DIFFERENCE from grader.run_microbench:
  - microbench_runner runs the solution in-process via exec() inside timeit and
    redirects stdout to an io.StringIO (per-iteration), so printed output is
    captured but discarded.
  - grader.run_microbench runs the solution as a real subprocess (python -c) and
    redirects the solution's stdout to os.devnull during the timeit.repeat call.
  - The signatures differ entirely: microbench_runner.run_microbench takes
    (source_code, stdin_texts: list[str], file_label, repeats) and returns a
    MicrobenchResult dataclass; grader.run_microbench takes
    (source_code, *, stdin_data: str, number) and returns a dict.

The existing tests/test_microbench.py already covers MicrobenchResult defaults,
apply_relative_micro, syntax errors, simple scripts, builtins, and input(). This
file adds happy-path coverage of the remaining public surface and explicitly
documents the stdout-handling difference vs grader.
"""

from __future__ import annotations

import microbench_runner
from microbench_runner import (
    MicrobenchResult,
    apply_relative_micro,
    run_microbench,
)


def test_microbench_runner_basic_timing() -> None:
    """Basic timing returns one positive per-call float per stdin text."""
    result = run_microbench(
        source_code="x = sum(range(50))\n",
        stdin_texts=[""],
        file_label="t.py",
        repeats=5,
    )
    assert result.error == ""
    assert len(result.timings) == 1
    assert result.min_time > 0
    assert result.median_time > 0


def test_microbench_runner_multiple_stdin_texts() -> None:
    """One timing is appended per stdin text (total = len(stdin_texts))."""
    result = run_microbench(
        source_code="n = int(input())\nprint(n)\n",
        stdin_texts=["1", "2", "3"],
        file_label="t.py",
        repeats=3,
    )
    assert result.error == ""
    assert len(result.timings) == 3


def test_microbench_runner_empty_stdin_texts_defaults_to_one() -> None:
    """An empty stdin_texts list is treated as a single empty stdin ([''])."""
    result = run_microbench(
        source_code="y = 2 * 2\n",
        stdin_texts=[],
        file_label="t.py",
        repeats=2,
    )
    assert result.error == ""
    assert len(result.timings) == 1


def test_microbench_runner_runtime_error_captured() -> None:
    """A runtime exception inside the runner is captured as result.error."""
    result = run_microbench(
        source_code="raise ValueError('boom')\n",
        stdin_texts=[""],
        file_label="t.py",
        repeats=2,
    )
    assert result.error
    assert "ValueError" in result.error
    assert result.timings == []


def test_microbench_runner_vs_grader_behavior() -> None:
    """microbench_runner does NOT redirect stdout to devnull the way grader does.

    Instead it captures the solution's stdout into a per-iteration io.StringIO
    (in _make_stdin_runner) and discards it. The net effect for the caller is the
    same — printed output never leaks into the returned timings — but the MECHANISM
    differs from grader.run_microbench's os.devnull redirection (commit 15b9912).

    REFACTORING NOTE: after merging microbench_runner.py into grader.py, the
    surviving implementation should be grader's subprocess+devnull approach. This
    test documents that a loud (printing) solution still yields clean timings here;
    update it if the merge unifies the two mechanisms.
    """
    result = run_microbench(
        source_code="print('noise from solution')\nz = 1 + 1\n",
        stdin_texts=[""],
        file_label="loud.py",
        repeats=3,
    )
    assert result.error == ""
    # Exactly one timing per stdin text — the printed line did not corrupt it.
    assert len(result.timings) == 1
    assert result.timings[0] > 0


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


def test_microbench_runner_module_constants() -> None:
    """Module exposes the threshold and warmup constants used by the verdict logic."""
    assert microbench_runner.SIMILAR_THRESHOLD_PERCENT == 5.0
    assert isinstance(microbench_runner.WARMUP_RUNS, int)
    assert microbench_runner.WARMUP_RUNS >= 1
