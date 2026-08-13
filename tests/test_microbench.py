"""Unit tests for microbench_runner."""

from __future__ import annotations

from stepik_grader.core.microbench_runner import (
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
    assert results[1].verdict in {"SLOWER", "MUCH_SLOWER"}


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


def test_warmup_applied_in_bench_script() -> None:
    """WARMUP_RUNS реально применяется: прогрев вшит в bench-скрипт ДО замера (issue #412)."""
    from stepik_grader.core.microbench_runner import _build_bench_script

    assert isinstance(WARMUP_RUNS, int) and WARMUP_RUNS >= 1
    script = _build_bench_script("x = 1\n", stdin_data="", number=1000)
    assert f"_warmup = {WARMUP_RUNS}" in script
    # прогрев (timeit.timeit) предшествует замеру (timeit.repeat) и стартует до
    # tracemalloc — в измеряемые время/память не входит.
    assert script.index("_timeit.timeit(") < script.index("_timeit.repeat(")
    assert script.index("_timeit.timeit(") < script.index("_tm.start()")


def test_timing_run_is_outside_tracemalloc() -> None:
    """Замер времени НЕ идёт под профилировщиком памяти (issue #956).

    `tracemalloc` берёт плату пропорционально числу аллокаций, поэтому под ним
    решение, активнее работающее со структурами данных, выглядит медленнее
    независимо от реальной скорости — режим 4 ранжировал решения наоборот.
    Порядок починен в #991, но гейта на него не было: возврат `_tm.start()`
    выше замера тесты не заметили бы, а сам дефект виден только на сравнении
    двух решений, то есть у пользователя.
    """
    from stepik_grader.core.microbench_runner import _build_bench_script

    script = _build_bench_script("x = [i for i in range(1000)]\n", stdin_data="", number=10)

    repeat_at = script.index("_timeit.repeat(")
    start_at = script.index("_tm.start()")
    stop_at = script.index("_tm.stop()")
    assert start_at < stop_at, "профилировщик должен открываться и закрываться"
    assert not (start_at < repeat_at < stop_at), (
        "замер времени оказался между _tm.start() и _tm.stop(): накладные расходы "
        "tracemalloc снова штрафуют решения по числу аллокаций"
    )


def test_memory_pass_stays_under_tracemalloc() -> None:
    """Пик памяти по-прежнему меряется — отдельным прогоном под профилировщиком."""
    from stepik_grader.core.microbench_runner import _build_bench_script

    script = _build_bench_script("x = 1\n", stdin_data="", number=10)

    start_at = script.index("_tm.start()")
    stop_at = script.index("_tm.stop()")
    measure_at = script.index("_tm.get_traced_memory()")
    assert start_at < measure_at < stop_at, "пик памяти снимается внутри окна профилировщика"
    assert "print('MEM:' + str(_peak_bytes))" in script


def test_peak_memory_survives_the_split_run() -> None:
    """Строка `MEM:` доезжает до результата живым прогоном (issue #956).

    Разделение проходов трогает формат stdout bench-скрипта, а его разбирает
    `run_microbench`. Регресс здесь выглядел бы безобидно — «память просто 0» —
    и молча обнулял бы половину сравнения решений.
    """
    result = run_microbench("data = [i * i for i in range(20000)]\n", number=1)

    assert not result["error"]
    assert result["peak_memory_mb"] > 0, "пик памяти обнулился — строка MEM: не разобралась"


def test_run_microbench_with_input() -> None:
    """Решение использующее input() корректно работает с stdin."""
    result = run_microbench("n = int(input())\nprint(n * 2)\n", stdin_data="5\n", number=20)
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
    from stepik_grader import grader

    source = "a, b = int(input()), int(input())\nprint(a + b)\n"
    result = grader.run_microbench(source, stdin_data="10\n20\n", number=50)
    assert result["error"] == ""
    assert len(result["times"]) == 5
    # Напечатанное число 30 не должно оказаться среди таймингов (они ~микросекунды).
    assert all(t < 1.0 for t in result["times"])


def test_grader_microbench_non_numeric_stdout_no_error() -> None:
    """stdin-решение, печатающее не-число, больше не падает на float(line)."""
    from stepik_grader import grader

    source = 'name = input()\nprint("Hello, " + name)\n'
    result = grader.run_microbench(source, stdin_data="World\n", number=50)
    assert result["error"] == ""
    assert len(result["times"]) == 5
