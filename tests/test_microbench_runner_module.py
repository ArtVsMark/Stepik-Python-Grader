"""Tests for the microbench_runner.py module functions directly.

microbench_runner.py is now LIVE — grader.py imports run_microbench from it and
calls it from run_microbench_mode (stdin path). These tests exercise the module's
public surface directly.

run_microbench runs the bench script through the active runner._RUNNER
(issue #417) and redirects the solution's stdout to os.devnull during the
timeit.repeat call (repeat=5), so printed output never leaks into the parsed
timings. Its signature is
(source_code, *, stdin_data: str, number) and it returns a dict with keys
'times' (list[float]) and 'error' (str).

The module also exposes the MicrobenchResult dataclass and apply_relative_micro
helper for aggregating/ranking per-file timings; those are covered below and in
tests/test_microbench.py.
"""

from __future__ import annotations

from stepik_grader.core import microbench_runner
from stepik_grader.core.microbench_runner import (
    MicrobenchResult,
    apply_reference_ranking,
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


def test_microbench_runner_reports_nonzero_peak_memory() -> None:
    """peak_memory_mb (Issue #25) is tracemalloc-based, not the hardcoded 0.0."""
    result = run_microbench("data = [0] * 1_000_000\n", stdin_data="", number=1)
    assert result["error"] == ""
    assert result["peak_memory_mb"] > 0.0


def test_timing_is_measured_outside_tracemalloc() -> None:
    """issue #991: замер времени идёт ДО старта профилировщика памяти.

    Накладные расходы ``tracemalloc`` пропорциональны числу аллокаций, поэтому
    под ним решение, активнее работающее с памятью, выглядит медленнее
    независимо от реальной скорости — режим 4 ранжировал решения наоборот
    (расхождение 2175% на паре, где «медленное» решение было быстрее).

    Проверяется порядок в сгенерированном bench-скрипте: это структурный
    инвариант, а не измеримая величина — сравнение двух решений по времени на
    CI-раннере было бы флаки-тестом, а не проверкой.
    """
    script = microbench_runner._build_bench_script("x = [0] * 100\n", "", 5)

    timing_at = script.index("_timeit.repeat(")
    tracemalloc_at = script.index("_tm.start()")

    assert timing_at < tracemalloc_at, "замер времени идёт под включённым tracemalloc"


def test_memory_pass_runs_the_solution_once_under_tracemalloc() -> None:
    """Память по-прежнему меряется — отдельным однократным прогоном.

    Пик — максимум по времени жизни, а не сумма, поэтому одного прогона
    достаточно; цена — один запуск против 5×number в замере времени.
    """
    script = microbench_runner._build_bench_script("x = [0] * 100\n", "", 5)
    after_start = script.split("_tm.start()", 1)[1]

    assert "_timeit.timeit(stmt=_stmt, setup='pass', number=1)" in after_start
    assert "_tm.get_traced_memory()" in after_start


def test_microbench_runner_peak_memory_present_on_error() -> None:
    """peak_memory_mb key is always present, even on a runtime error (0.0)."""
    result = run_microbench("raise ValueError('boom')\n", stdin_data="", number=2)
    assert result["peak_memory_mb"] == 0.0


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


def test_microbench_runner_timeout_returns_error(monkeypatch) -> None:
    """issue #417: bench исполняется через активный ``runner._RUNNER``; таймаут
    приходит как RunOutcome(timed_out=True).

    Issue #47 R-01: error message reports the iteration count (`number`) that
    was running when the timeout fired -- the most useful diagnostic available
    without a genuine per-call timeout inside the child process.
    """
    from stepik_grader.core import runner as runner_mod
    from stepik_grader.core.runner import RunOutcome

    class _TimeoutRunner:
        def run(self, spec):
            return RunOutcome(timed_out=True, elapsed=60.0)

    monkeypatch.setattr(runner_mod, "_RUNNER", _TimeoutRunner())
    result = run_microbench("x = 1\n", stdin_data="", number=5000)
    assert result["times"] == []
    assert "number=5000" in result["error"]
    assert "60s" in result["error"]
    assert result["peak_memory_mb"] == 0.0


def test_microbench_runner_unexpected_exception_returns_error(monkeypatch) -> None:
    """issue #417: сбой запуска (Runner не смог спавнить дочерний процесс)
    приходит как RunOutcome(launch_error=...) → error-результат."""
    from stepik_grader.core import runner as runner_mod
    from stepik_grader.core.runner import RunOutcome

    class _LaunchErrorRunner:
        def run(self, spec):
            return RunOutcome(launch_error="no such file")

    monkeypatch.setattr(runner_mod, "_RUNNER", _LaunchErrorRunner())
    result = run_microbench("x = 1\n", stdin_data="", number=5)
    assert result["times"] == []
    assert "no such file" in result["error"]
    assert result["peak_memory_mb"] == 0.0


def test_microbench_runner_uses_public_run_spec(monkeypatch) -> None:
    """issue #640: bench исполняется через публичный ``run_spec()``,
    а не через приватный ``_RUNNER`` напрямую — единственная точка выбора
    backend'а. Guard против возврата к ``_RUNNER.run``.

    Патчится имя в самом ``microbench_runner``: ``run_spec`` он импортирует
    напрямую, поэтому подмена в ``core.runner`` до него уже не доходит."""
    from stepik_grader.core import microbench_runner
    from stepik_grader.core.runner import RunOutcome

    calls: list = []

    def fake_run_spec(spec):
        calls.append(spec)
        return RunOutcome()  # пустой stdout → error-результат, но run_spec вызван

    monkeypatch.setattr(microbench_runner, "run_spec", fake_run_spec)
    run_microbench("x = 1\n", stdin_data="", number=5)
    assert len(calls) == 1  # ровно один прогон, через публичный run_spec


def test_microbench_runner_apply_relative_orders_by_median() -> None:
    """apply_relative_micro labels the fastest SIMILAR and slower ones SLOWER/MUCH_SLOWER.

    REFACTORING INVARIANT: any merged verdict logic must keep the fastest at
    relative_percent == 100.0 and verdict SIMILAR.
    issue #397: единый вердикт "MUCH_SLOWER" (подчёркивание) во всех путях.
    """
    fast = MicrobenchResult(file="fast.py", repeats=10, timings=[0.001])
    slow = MicrobenchResult(file="slow.py", repeats=10, timings=[0.010])
    out = apply_relative_micro([fast, slow])
    assert out[0].verdict == "SIMILAR"
    assert out[0].relative_percent == 100.0
    assert out[1].verdict == "MUCH_SLOWER"
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
    """Module exposes the threshold constant and applies WARMUP_RUNS in the bench script."""
    assert microbench_runner.SIMILAR_THRESHOLD_PERCENT == 5.0
    # issue #412: WARMUP_RUNS — не мёртвая константа, а число прогревочных
    # прогонов, реально вшитых в bench-скрипт перед замером.
    script = microbench_runner._build_bench_script("x = 1\n", stdin_data="", number=1000)
    assert f"_warmup = {microbench_runner.WARMUP_RUNS}" in script
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


# ---------------------------------------------------------------------------
# apply_reference_ranking — ранжирование относительно эталона (issue #397:
# перенесено из web/viewmodels в core, чтобы не дублировать формулу).
# ---------------------------------------------------------------------------


def test_apply_reference_ranking_labels_relative_to_reference() -> None:
    """Эталон помечается REFERENCE; быстрее — FASTER, медленнее — SLOWER/MUCH_SLOWER."""
    results = {
        "ref.py": {"median": 1.0},
        "faster.py": {"median": 0.5},
        "similar.py": {"median": 1.1},
        "slower.py": {"median": 1.3},
        "much.py": {"median": 2.0},
        "broken.py": {"error": "SyntaxError"},
    }
    apply_reference_ranking(results, "ref.py", similar_threshold=1.15, much_slower_threshold=1.5)

    assert results["ref.py"]["verdict"] == "REFERENCE"
    assert results["ref.py"]["relative"] == 1.0
    assert results["faster.py"]["verdict"] == "FASTER"
    assert results["similar.py"]["verdict"] == "SIMILAR"
    assert results["slower.py"]["verdict"] == "SLOWER"
    assert results["much.py"]["verdict"] == "MUCH_SLOWER"
    # Ошибочные записи не трогаются.
    assert "verdict" not in results["broken.py"]


def test_apply_reference_ranking_zero_reference_median_defaults_to_one() -> None:
    """base_median == 0 избегает деления на ноль (relative == 1.0)."""
    results = {"ref.py": {"median": 0.0}, "b.py": {"median": 0.0}}
    apply_reference_ranking(results, "ref.py", similar_threshold=1.15, much_slower_threshold=1.5)
    assert results["ref.py"]["verdict"] == "REFERENCE"
    assert all(v["relative"] == 1.0 for v in results.values())


# ---------------------------------------------------------------------------
# strip_harness_frames — traceback без кадров timeit-обёртки (issue #726)
# ---------------------------------------------------------------------------


_TIMEIT_TRACEBACK = """Traceback (most recent call last):
  File "/tmp/tmp7l_62ha6.py", line 16, in <module>
    _timeit.timeit(stmt=_stmt, setup='pass', number=_warmup)
    ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.14/timeit.py", line 234, in timeit
    return Timer(stmt, setup, timer, globals).timeit(number)
  File "<timeit-src>", line 9, in inner
NameError: name 'data' is not defined"""


def test_strip_harness_frames_keeps_only_the_exception_line() -> None:
    """Все кадры принадлежат обёртке — остаётся только сама ошибка."""
    stripped = microbench_runner.strip_harness_frames(
        _TIMEIT_TRACEBACK, harness_path="/tmp/tmp7l_62ha6.py"
    )

    assert stripped == "NameError: name 'data' is not defined"


def test_strip_harness_frames_keeps_user_frames() -> None:
    """Кадры пользовательского кода сохраняются вместе с заголовком traceback."""
    text = """Traceback (most recent call last):
  File "<timeit-src>", line 9, in inner
  File "/work/solution.py", line 3, in solve
    return 1 / 0
ZeroDivisionError: division by zero"""

    stripped = microbench_runner.strip_harness_frames(text)

    assert "timeit-src" not in stripped
    assert stripped.startswith("Traceback (most recent call last):")
    assert '  File "/work/solution.py", line 3, in solve' in stripped
    assert "    return 1 / 0" in stripped
    assert stripped.endswith("ZeroDivisionError: division by zero")


def test_strip_harness_frames_passes_through_plain_text() -> None:
    """Не-traceback (например, сообщение о таймауте) не трогаем."""
    text = "microbench timeout: exceeded 60s running number=1000"

    assert microbench_runner.strip_harness_frames(text) == text


def test_strip_harness_frames_handles_empty_input() -> None:
    assert microbench_runner.strip_harness_frames("") == ""
