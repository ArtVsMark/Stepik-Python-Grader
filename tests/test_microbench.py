"""Unit-тесты для microbench_runner.py."""
from __future__ import annotations

import pytest

from microbench_runner import (
    MicrobenchResult,
    SIMILAR_THRESHOLD_PERCENT,
    apply_relative_micro,
    run_microbench,
)


# ---------------------------------------------------------------------------
# MicrobenchResult — свойства при пустых timings
# ---------------------------------------------------------------------------

def test_result_empty_timings_min() -> None:
    """min_time возвращает 0.0 при пустом списке timings."""
    r = MicrobenchResult(file="a.py", repeats=10)
    assert r.min_time == 0.0


def test_result_empty_timings_median() -> None:
    """median_time возвращает 0.0 при пустом списке timings."""
    r = MicrobenchResult(file="a.py", repeats=10)
    assert r.median_time == 0.0


def test_result_empty_timings_std_dev() -> None:
    """std_dev_time возвращает 0.0 при менее двух замеров."""
    r = MicrobenchResult(file="a.py", repeats=10, timings=[0.001])
    assert r.std_dev_time == 0.0


def test_result_with_timings_median() -> None:
    """median_time корректно считается для нечётного количества замеров."""
    r = MicrobenchResult(file="a.py", repeats=10, timings=[0.001, 0.003, 0.002])
    assert r.median_time == pytest.approx(0.002)


# ---------------------------------------------------------------------------
# SIMILAR_THRESHOLD_PERCENT — единый источник истины
# ---------------------------------------------------------------------------

def test_similar_threshold_value() -> None:
    """Константа порога должна быть равна 5.0 (задача 9 аудита)."""
    assert SIMILAR_THRESHOLD_PERCENT == 5.0


# ---------------------------------------------------------------------------
# apply_relative_micro
# ---------------------------------------------------------------------------

def test_apply_relative_micro_empty_list() -> None:
    """Пустой список — возвращается пустой список без ошибок."""
    assert apply_relative_micro([]) == []


def test_apply_relative_micro_single_result() -> None:
    """Единственный результат должен получить verdict SIMILAR и 100%."""
    r = MicrobenchResult(file="a.py", repeats=10, timings=[0.001])
    results = apply_relative_micro([r])
    assert results[0].verdict == "SIMILAR"
    assert results[0].relative_percent == pytest.approx(100.0)


def test_apply_relative_micro_faster_slower() -> None:
    """Результат в 2× медленнее быстрейшего должен получить verdict MUCH SLOWER."""
    fast = MicrobenchResult(file="fast.py", repeats=10, timings=[0.001])
    slow = MicrobenchResult(file="slow.py", repeats=10, timings=[0.002])
    apply_relative_micro([fast, slow])
    assert fast.verdict == "SIMILAR"
    assert slow.verdict == "MUCH SLOWER"
    assert slow.relative_percent == pytest.approx(200.0)


def test_apply_relative_micro_error_result() -> None:
    """Результат с ошибкой должен получить verdict ERROR."""
    err = MicrobenchResult(file="bad.py", repeats=10, error="SyntaxError: bad")
    apply_relative_micro([err])
    assert err.verdict == "ERROR"


# ---------------------------------------------------------------------------
# run_microbench — интеграционный дымовой тест
# ---------------------------------------------------------------------------

def test_run_microbench_simple_print() -> None:
    """Простой print-скрипт без stdin выполняется без ошибок."""
    code = "print('hello')"
    result = run_microbench(code, ["\n"], file_label="test", repeats=5)
    assert result.error == ""
    assert result.timings


def test_run_microbench_syntax_error() -> None:
    """SyntaxError в коде возвращает MicrobenchResult с непустым полем error."""
    code = "def broken("
    result = run_microbench(code, [""], file_label="bad.py", repeats=1)
    assert "SyntaxError" in result.error
    assert result.timings == []
