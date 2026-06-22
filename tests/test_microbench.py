from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from microbench_runner import (
    MicrobenchResult,
    SIMILAR_THRESHOLD_PERCENT,
    apply_relative_micro,
    run_microbench,
)


def test_result_defaults() -> None:
    r = MicrobenchResult(file="test.py", repeats=10)
    assert r.min_time == 0.0
    assert r.median_time == 0.0
    assert r.mean_time == 0.0
    assert r.max_time == 0.0
    assert r.std_dev_time == 0.0
    assert r.verdict == "OK"


def test_result_no_func_name_field() -> None:
    """func_name мёртвое поле — должно быть удалено из датакласса."""
    assert not hasattr(MicrobenchResult(file="x", repeats=1), "func_name"), \
        "func_name удалён как мёртвое поле"


def test_apply_relative_micro_empty() -> None:
    assert apply_relative_micro([]) == []


def test_apply_relative_micro_single() -> None:
    r = MicrobenchResult(file="a.py", repeats=10, timings=[0.001])
    results = apply_relative_micro([r])
    assert results[0].verdict == "SIMILAR"
    assert results[0].relative_percent == 100.0


def test_apply_relative_micro_two() -> None:
    fast = MicrobenchResult(file="fast.py", repeats=10, timings=[0.001])
    slow = MicrobenchResult(file="slow.py", repeats=10, timings=[0.002])
    results = apply_relative_micro([fast, slow])
    verdicts = {r.file: r.verdict for r in results}
    assert verdicts["fast.py"] == "SIMILAR"
    assert verdicts["slow.py"] in ("SLOWER", "MUCH SLOWER")


def test_similar_threshold_value() -> None:
    assert SIMILAR_THRESHOLD_PERCENT == 5.0


def test_run_microbench_syntax_error() -> None:
    result = run_microbench("def broken(:", [""], "broken.py", repeats=1)
    assert result.error
    assert "SyntaxError" in result.error


def test_run_microbench_simple() -> None:
    code = "x = 1 + 1"
    result = run_microbench(code, [""], "simple.py", repeats=10)
    assert not result.error
    assert result.timings
    assert result.min_time > 0
