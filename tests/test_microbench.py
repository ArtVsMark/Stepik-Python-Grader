"""Unit tests for microbench_runner."""

from __future__ import annotations

from microbench_runner import (
    SIMILAR_THRESHOLD_PERCENT,
    MicrobenchResult,
    apply_relative_micro,
    run_microbench,
)


def test_similar_threshold_value() -> None:
    """Константа должна быть единственным источником истины."""
    assert SIMILAR_THRESHOLD_PERCENT == 5.0


def test_result_no_timings_defaults() -> None:
    r = MicrobenchResult(file="x.py", repeats=10)
    assert r.min_time == 0.0
    assert r.median_time == 0.0
    assert r.mean_time == 0.0
    assert r.max_time == 0.0
    assert r.std_dev_time == 0.0


def test_apply_relative_micro_empty() -> None:
    assert apply_relative_micro([]) == []


def test_apply_relative_micro_single() -> None:
    r = MicrobenchResult(file="a.py", repeats=10, timings=[0.001])
    results = apply_relative_micro([r])
    assert results[0].verdict == "SIMILAR"
    assert results[0].relative_percent == 100.0


def test_apply_relative_micro_two_solutions() -> None:
    fast = MicrobenchResult(file="fast.py", repeats=10, timings=[0.001])
    slow = MicrobenchResult(file="slow.py", repeats=10, timings=[0.002])
    results = apply_relative_micro([fast, slow])
    assert results[0].verdict == "SIMILAR"
    assert results[1].verdict in {"SLOWER", "MUCH SLOWER"}


def test_run_microbench_syntax_error() -> None:
    result = run_microbench(
        source_code="def broken(:",
        stdin_texts=[""],
        file_label="broken.py",
        repeats=1,
    )
    assert result.error
    assert "SyntaxError" in result.error


def test_run_microbench_simple_script() -> None:
    source = "x = 1 + 1\n"
    result = run_microbench(source_code=source, stdin_texts=[""], file_label="t.py", repeats=3)
    assert not result.error
    assert len(result.timings) == 1
    assert result.min_time > 0
