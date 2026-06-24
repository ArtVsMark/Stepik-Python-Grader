"""Unit tests for microbench_runner."""

from __future__ import annotations

from microbench_runner import (
    SIMILAR_THRESHOLD_PERCENT,
    WARMUP_RUNS,
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
    """Решение с синтаксической ошибкой → непустой error, пустые times."""
    result = run_microbench("def broken(:", stdin_data="", number=10)
    assert result["error"]
    assert result["times"] == []


def test_run_microbench_simple_script() -> None:
    result = run_microbench("x = 1 + 1\n", stdin_data="", number=50)
    assert not result["error"]
    assert len(result["times"]) == 5
    assert all(t > 0 for t in result["times"])


def test_run_microbench_uses_builtins() -> None:
    """print() работает: stdout подавляется в devnull, замер проходит чисто."""
    result = run_microbench("print('hello')\n", stdin_data="", number=50)
    assert not result["error"], f"Unexpected error: {result['error']}"
    assert result["times"]


def test_warmup_runs_constant_exists() -> None:
    """Константа WARMUP_RUNS экспортируется из модуля."""
    assert isinstance(WARMUP_RUNS, int)
    assert WARMUP_RUNS >= 1


def test_run_microbench_with_input() -> None:
    """Решение использующее input() корректно работает с stdin."""
    result = run_microbench(
        "n = int(input())\nprint(n * 2)\n", stdin_data="5\n", number=20
    )
    assert not result["error"]
    assert len(result["times"]) == 5


# ===========================================================================
# grader.run_microbench — фикс контаминации stdout таймингами (режим 4, stdin)
# ===========================================================================


def test_grader_microbench_suppresses_numeric_stdout() -> None:
    """print()-вывод stdin-решения НЕ должен попадать в список таймингов.

    Раньше каждая строка stdout (включая напечатанное число) парсилась как
    тайминг → мусорная статистика. Теперь ожидаем ровно 5 таймингов repeat=5.
    """
    import grader

    source = "a, b = int(input()), int(input())\nprint(a + b)\n"
    result = grader.run_microbench(source, stdin_data="10\n20\n", number=50)
    assert result["error"] == ""
    assert len(result["times"]) == 5
    # Напечатанное число 30 не должно оказаться среди таймингов (они ~микросекунды).
    assert all(t < 1.0 for t in result["times"])


def test_grader_microbench_non_numeric_stdout_no_error() -> None:
    """stdin-решение, печатающее не-число, больше не падает на float(line)."""
    import grader

    source = 'name = input()\nprint("Hello, " + name)\n'
    result = grader.run_microbench(source, stdin_data="World\n", number=50)
    assert result["error"] == ""
    assert len(result["times"]) == 5
