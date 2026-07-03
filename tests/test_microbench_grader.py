"""Tests for grader.run_microbench and grader.run_microbench_mode.

These tests pin down the CURRENT live behavior of grader.py's inline timeit
microbenchmark, so that a future refactoring (consolidating the inline path
with microbench_runner.py) can be validated against them.

The most important behavior covered here is the stdout-isolation fix from
commit 15b9912: while timeit runs the solution, the solution's own stdout is
redirected to os.devnull. Without this, every print()ed line lands on stdout
mixed with the timing numbers and corrupts the parse (or, worse, a numeric
print like "10" gets parsed as a bogus timing). microbench_runner.py — now
imported by grader.py via run_microbench — does NOT have this fix.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader import grader


def _write(tmp_path: pathlib.Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# stdout isolation (the critical fix from commit 15b9912)
# ---------------------------------------------------------------------------


def test_run_microbench_stdin_stdout_not_contaminated() -> None:
    """A stdin solution's print() output must NOT appear among the timings.

    REFACTORING INVARIANT: after merging microbench_runner.py into grader.py,
    run_microbench must still redirect the solution's stdout to devnull during
    timeit. We assert the parsed timings are exactly the 5 repeat() values and
    that the printed number ("10") never shows up as a timing.
    """
    solution_code = "a, b = int(input()), int(input())\nprint(a + b)\n"
    result = grader.run_microbench(solution_code, stdin_data="3\n7\n", number=10)

    assert result["error"] == ""
    # timeit.repeat(repeat=5) → exactly 5 timings, never the solution's stdout.
    assert len(result["times"]) == 5
    assert all(isinstance(t, float) for t in result["times"])
    # The solution prints "10"; it must not have been parsed as a timing.
    assert 10.0 not in result["times"]
    # All timings are sub-second per-iteration measurements (microsecond range).
    assert all(0.0 < t < 1.0 for t in result["times"])


def test_run_microbench_loud_solution_many_prints_not_contaminated() -> None:
    """A solution that prints many numeric lines still yields exactly 5 timings.

    REFACTORING INVARIANT: stdout suppression must hold even when the solution
    is very chatty — otherwise hundreds of printed integers would be parsed as
    floats and inflate result["times"].
    """
    solution_code = "for i in range(20):\n    print(i)\n"
    result = grader.run_microbench(solution_code, stdin_data="", number=5)

    assert result["error"] == ""
    assert len(result["times"]) == 5
    # None of the printed integers 0..19 leaked into the timings.
    assert all(t < 1.0 for t in result["times"])


# ---------------------------------------------------------------------------
# basic timing correctness
# ---------------------------------------------------------------------------


def test_run_microbench_returns_sane_timings() -> None:
    """Timings should be small positive floats, not garbage."""
    solution_code = "x = sum(range(100))\n"
    result = grader.run_microbench(solution_code, stdin_data="", number=50)

    assert result["error"] == ""
    assert result["times"]
    assert all(t > 0 for t in result["times"])
    median = sorted(result["times"])[len(result["times"]) // 2]
    assert median < 1.0


def test_run_microbench_with_input_doubles() -> None:
    """A solution reading stdin and computing a value times cleanly."""
    solution_code = "n = int(input())\nprint(n * 2)\n"
    result = grader.run_microbench(solution_code, stdin_data="21\n", number=20)

    assert result["error"] == ""
    assert len(result["times"]) == 5
    assert all(t > 0 for t in result["times"])


# ---------------------------------------------------------------------------
# function-mode via run_microbench_mode
# ---------------------------------------------------------------------------


def test_run_microbench_mode_function_mode(tmp_path: pathlib.Path) -> None:
    """Function-call blocks route through run_single_test (subprocess), not timeit.

    Format-3 tests whose input block is valid Python code (a call to the
    solution's function) are detected as test_type='function'. run_microbench_mode
    must then bench them via run_single_test rather than feeding them to timeit as
    stdin. We assert a sane stats dict comes back with no error.

    REFACTORING INVARIANT: the function-mode routing in run_microbench_mode must
    survive the merge of microbench_runner.py.
    """
    sol = _write(tmp_path, "task1.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path, "input.txt", "# TEST_1:\nprint(add(2, 3))\n")
    _write(tmp_path, "output.txt", "# TEST_1:\n5\n")

    # Sanity: the loader classifies this block as function-mode.
    cases = grader.load_test_cases(str(tmp_path))
    assert cases and cases[0].test_type == "function"

    results = grader.run_microbench_mode([sol], str(tmp_path), number=100)
    assert sol in results
    data = results[sol]
    assert "error" not in data or not data["error"], data
    assert data["runs"] > 0
    assert data["median"] > 0
    assert data["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}


def test_run_microbench_mode_stdin_mode(tmp_path: pathlib.Path) -> None:
    """Plain numeric stdin blocks route through timeit (run_microbench)."""
    sol = _write(tmp_path, "task1.py", "a, b = int(input()), int(input())\nprint(a + b)\n")
    _write(tmp_path, "input.txt", "# TEST_1:\n2\n3\n")
    _write(tmp_path, "output.txt", "# TEST_1:\n5\n")

    cases = grader.load_test_cases(str(tmp_path))
    assert cases and cases[0].test_type == "stdin"

    results = grader.run_microbench_mode([sol], str(tmp_path), number=50)
    assert sol in results
    data = results[sol]
    assert not data.get("error"), data
    # stdin-mode uses timeit.repeat(repeat=5) → 5 timings per case.
    assert data["runs"] == 5
    assert data["peak_memory_mb"] >= 0.0


def test_run_microbench_mode_reports_nonzero_memory_for_allocation(
    tmp_path: pathlib.Path,
) -> None:
    """Issue #25: peak_memory_mb is no longer hardcoded to 0.0 for stdin blocks."""
    sol = _write(tmp_path, "task1.py", "input()\ndata = [0] * 1_000_000\nprint(len(data))\n")
    # A bare int literal has no ast.Name node, so _is_python_code_block treats
    # it as plain data -> routes through the stdin path (run_microbench), not
    # the function-call path (_build_call_wrapper, which doesn't pipe stdin).
    _write(tmp_path, "input.txt", "# TEST_1:\n42\n")
    _write(tmp_path, "output.txt", "# TEST_1:\n1000000\n")

    results = grader.run_microbench_mode([sol], str(tmp_path), number=1)
    data = results[sol]
    assert not data.get("error"), data
    assert data["peak_memory_mb"] > 0.0


def test_run_microbench_mode_empty_test_dir(tmp_path: pathlib.Path) -> None:
    """No test cases → empty dict, no crash."""
    sol = _write(tmp_path, "task1.py", "print('hi')\n")
    assert grader.run_microbench_mode([sol], str(tmp_path), number=10) == {}


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_run_microbench_invalid_solution() -> None:
    """A solution with a syntax error returns an error result, not a crash."""
    result = grader.run_microbench("def broken(:\n", stdin_data="", number=10)
    assert result["error"]
    assert result["times"] == []


def test_run_microbench_runtime_error() -> None:
    """A solution that raises at runtime surfaces as an error, empty times."""
    result = grader.run_microbench("raise ValueError('boom')\n", stdin_data="", number=10)
    assert result["error"]
    assert result["times"] == []


def test_run_microbench_mode_propagates_error(tmp_path: pathlib.Path) -> None:
    """A broken solution in stdin-mode reports an error keyed by path."""
    sol = _write(tmp_path, "task1.py", "raise RuntimeError('nope')\n")
    _write(tmp_path, "input.txt", "# TEST_1:\n1\n")
    _write(tmp_path, "output.txt", "# TEST_1:\n1\n")

    results = grader.run_microbench_mode([sol], str(tmp_path), number=10)
    assert sol in results
    assert results[sol].get("error")


# ---------------------------------------------------------------------------
# number parameter respected
# ---------------------------------------------------------------------------


def test_run_microbench_number_parameter() -> None:
    """The number= parameter is accepted and the result structure is stable.

    We can't directly observe the iteration count, but both a small and a large
    number must yield the same shape: 5 timings, no error.

    REFACTORING INVARIANT: number= must keep controlling timeit's per-repeat
    iteration count after the merge.
    """
    code = "x = 1 + 1\n"
    small = grader.run_microbench(code, stdin_data="", number=5)
    large = grader.run_microbench(code, stdin_data="", number=500)

    assert small["error"] == "" and large["error"] == ""
    assert len(small["times"]) == 5
    assert len(large["times"]) == 5


# ---------------------------------------------------------------------------
# max_memory_mb — best-effort RLIMIT_AS cap (Issue #43 S-01)
# ---------------------------------------------------------------------------


def test_run_microbench_accepts_generous_memory_limit() -> None:
    """A generous max_memory_mb must not interfere with a trivial computation."""
    result = grader.run_microbench("x = 1 + 1\n", stdin_data="", number=5, max_memory_mb=512)
    assert result["error"] == ""
    assert len(result["times"]) == 5


def test_make_memory_limiter_none_when_limit_disabled() -> None:
    from stepik_grader.core.microbench_runner import _make_memory_limiter

    assert _make_memory_limiter(None) is None


def test_make_memory_limiter_none_when_resource_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no `resource` module — limiter must degrade to a no-op."""
    from stepik_grader.core import microbench_runner

    monkeypatch.setattr(microbench_runner, "resource", None)
    assert microbench_runner._make_memory_limiter(1024) is None


def test_make_memory_limiter_calls_setrlimit_with_mb_converted_to_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stepik_grader.core import microbench_runner

    calls: list[tuple] = []

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def setrlimit(which, limits):
            calls.append((which, limits))

    monkeypatch.setattr(microbench_runner, "resource", _FakeResource)
    limiter = microbench_runner._make_memory_limiter(64)
    assert limiter is not None

    limiter()

    expected_bytes = 64 * 1024 * 1024
    assert calls == [("RLIMIT_AS", (expected_bytes, expected_bytes))]


@pytest.mark.parametrize("exc", [ValueError("invalid limit"), OSError("not permitted")])
def test_make_memory_limiter_swallows_setrlimit_failure(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """setrlimit() failing (observed on macOS CI for RLIMIT_AS) must not raise --
    see the matching test/comment in test_grader_core.py for why."""
    from stepik_grader.core import microbench_runner

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def setrlimit(which, limits):
            raise exc

    monkeypatch.setattr(microbench_runner, "resource", _FakeResource)
    limiter = microbench_runner._make_memory_limiter(64)
    assert limiter is not None

    limiter()  # must not raise
