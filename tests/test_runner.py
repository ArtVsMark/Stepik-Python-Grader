"""Tests for core/runner.py — Runner Protocol + LocalRunner (issue #136/#139).

Two halves:
- _apply_memory_limit / _measure_peak_memory unit tests, migrated from
  test_grader_core.py after the subprocess-execution code moved into
  core/runner.py (issue #136/#137/#138) — monkeypatch targets updated to the
  runner module, since that's where `resource`/`psutil` now actually live.
- LocalRunner.run() scenario tests (issue #139): timeout, a script living in
  a temp directory, stdout/stderr capture, and launch failure (OSError).
"""

from __future__ import annotations

import pathlib
import sys
import threading
import time
import warnings

import pytest

from stepik_grader.core.runner import LocalRunner, RunSpec

# ---------------------------------------------------------------------------
# _apply_memory_limit — best-effort RLIMIT_AS cap via prlimit (issue #67, #43 S-01)
# ---------------------------------------------------------------------------


def test_apply_memory_limit_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_memory_mb=None → prlimit не вызывается."""
    from stepik_grader.core import runner

    calls: list[tuple] = []

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(*args):
            calls.append(args)

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(123, None)
    assert calls == []


def test_apply_memory_limit_noop_when_resource_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no `resource` module — must degrade to a no-op, not crash."""
    from stepik_grader.core import runner

    monkeypatch.setattr(runner, "resource", None)
    runner._apply_memory_limit(123, 1024)  # must not raise


def test_apply_memory_limit_calls_prlimit_with_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """prlimit получает (pid, RLIMIT_AS, (bytes, bytes)), bytes = mb * 1024**2."""
    from stepik_grader.core import runner

    calls: list[tuple] = []

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(pid, which, limits):
            calls.append((pid, which, limits))

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(4321, 64)

    expected_bytes = 64 * 1024 * 1024
    assert calls == [(4321, "RLIMIT_AS", (expected_bytes, expected_bytes))]


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("no prlimit on macOS"),
        ValueError("invalid limit"),
        OSError("no such process"),
    ],
)
def test_apply_memory_limit_swallows_prlimit_failure(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """prlimit отсутствует на macOS (AttributeError) / нет процесса-прав (OSError)
    — не должно падать, просто пропускаем cap (issue #67)."""
    from stepik_grader.core import runner

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(*args):
            raise exc

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(1, 64)  # must not raise


# ---------------------------------------------------------------------------
# _measure_peak_memory — warn on unreliable reading (Issue #48 R-05)
# ---------------------------------------------------------------------------


class _FakeProc:
    pid = 999999


def test_measure_peak_memory_warns_on_process_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil.Process(pid) itself raising NoSuchProcess -- outer except branch."""
    import psutil

    from stepik_grader.core import runner

    def _raise(_pid: int) -> None:
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(runner.psutil, "Process", _raise)

    result: list[float] = [0.0]
    stop = threading.Event()
    stop.set()

    with pytest.warns(UserWarning, match="unreliable"):
        runner._measure_peak_memory(_FakeProc(), result, stop)

    assert result[0] == 0.0


def test_measure_peak_memory_warns_on_first_sample_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil.Process() succeeds but the immediate first memory_info() sample
    fails -- inner except branch around the pre-loop read."""
    import psutil

    from stepik_grader.core import runner

    class _FakePsutilProcess:
        def memory_info(self) -> None:
            raise psutil.NoSuchProcess(999999)

    monkeypatch.setattr(runner.psutil, "Process", lambda pid: _FakePsutilProcess())

    result: list[float] = [0.0]
    stop = threading.Event()
    stop.set()

    with pytest.warns(UserWarning, match="unreliable"):
        runner._measure_peak_memory(_FakeProc(), result, stop)

    assert result[0] == 0.0


def test_measure_peak_memory_unreliable_warning_deduped_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls (batch grading many trivially-fast solutions) warn once,
    not once per call -- the message text is constant so Python's own
    "default" warning filter suppresses repeats within the same interpreter
    session (issue: pid-specific text used to defeat this dedup)."""
    import psutil

    from stepik_grader.core import runner

    def _raise(_pid: int) -> None:
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(runner.psutil, "Process", _raise)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for _ in range(5):
            result: list[float] = [0.0]
            stop = threading.Event()
            stop.set()
            runner._measure_peak_memory(_FakeProc(), result, stop)

    unreliable = [w for w in caught if "unreliable" in str(w.message)]
    assert len(unreliable) == 1, unreliable


# ---------------------------------------------------------------------------
# LocalRunner.run() — end-to-end scenarios (issue #139)
# ---------------------------------------------------------------------------


def _write_script(tmp_path: pathlib.Path, body: str, name: str = "sol.py") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_local_runner_captures_stdout_and_returncode(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print('hello')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is None
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "hello"


def test_local_runner_captures_stderr_on_nonzero_exit(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is None
    assert outcome.returncode == 1
    assert outcome.stderr.decode().strip() == "boom"


def test_local_runner_passes_stdin_through(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(path=path, stdin=b"4\n", timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode().strip() == "5"


def test_local_runner_script_in_nested_tempdir(tmp_path: pathlib.Path) -> None:
    # issue #139 "tempdir" scenario: script path lives in a fresh temp
    # directory tree (same situation as function-mode's NamedTemporaryFile
    # wrapper in grader_core.run_single_test), not just a bare filename.
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    path = _write_script(nested, "print('deep')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "deep"


def test_local_runner_times_out(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "import time; time.sleep(100)\n")
    spec = RunSpec(path=path, stdin=None, timeout=0.1, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert outcome.elapsed == 0.1
    assert outcome.launch_error is None


def test_local_runner_launch_error_is_captured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Точечно ломаем сам spawn (не поведение решения) -- monkeypatch на
    # несуществующий интерпретатор, чтобы Popen поднял OSError/FileNotFoundError.
    from stepik_grader.core import runner

    monkeypatch.setattr(runner.sys, "executable", str(tmp_path / "no-such-python-binary"))
    path = _write_script(tmp_path, "print(1)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is not None
    assert outcome.timed_out is False


def test_local_runner_measures_peak_memory_when_enabled(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "x = [0] * 1000\nprint(len(x))\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=True)

    outcome = LocalRunner().run(spec)

    assert outcome.returncode == 0
    # peak_memory_mb может быть 0.0 на очень короткоживущих процессах (issue
    # #48 R-05, best-effort) -- проверяем только, что замер не сломал запуск.
    assert outcome.peak_memory_mb >= 0.0


def test_local_runner_skips_memory_measurement_when_disabled(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print('ok')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.peak_memory_mb == 0.0


def test_run_spec_defaults() -> None:
    spec = RunSpec(path="x.py", stdin=None, timeout=5.0)
    assert spec.measure_memory is True
    assert spec.max_memory_mb is None
    assert spec.cancel_event is None


def test_run_outcome_defaults() -> None:
    from stepik_grader.core.runner import RunOutcome

    outcome = RunOutcome()
    assert outcome.stdout == b""
    assert outcome.stderr == b""
    assert outcome.returncode == 0
    assert outcome.elapsed == 0.0
    assert outcome.peak_memory_mb == 0.0
    assert outcome.timed_out is False
    assert outcome.launch_error is None
    assert outcome.cancelled is False
    assert outcome.sandbox_violation is None


# ---------------------------------------------------------------------------
# LocalRunner.run() — cancel_event poll-loop (issue #262)
# ---------------------------------------------------------------------------


def test_local_runner_cancel_event_none_matches_prior_behavior(tmp_path: pathlib.Path) -> None:
    """cancel_event=None (default) — identical to the pre-#262 blocking path."""
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(path=path, stdin=b"4\n", timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is False
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "5"


def test_local_runner_cancel_event_not_triggered_runs_to_completion(
    tmp_path: pathlib.Path,
) -> None:
    """cancel_event supplied but never set -- the poll path must still
    produce a correct result (proves the concurrent stdout/stderr drain
    threads work, not just the early-exit branch)."""
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(
        path=path, stdin=b"41\n", timeout=5.0, measure_memory=False, cancel_event=threading.Event()
    )

    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is False
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "42"


def test_local_runner_cancel_event_stops_process_early(tmp_path: pathlib.Path) -> None:
    """Cancelling mid-run kills the child well before its full sleep duration
    (proves the 100ms poll loop actually observes the event, not just the
    timeout budget)."""
    path = _write_script(tmp_path, "import time; time.sleep(30)\n")
    cancel_event = threading.Event()
    spec = RunSpec(
        path=path, stdin=None, timeout=60.0, measure_memory=False, cancel_event=cancel_event
    )

    def _cancel_soon() -> None:
        cancel_event.wait(0.3)
        cancel_event.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is True
    assert outcome.timed_out is False
    assert outcome.elapsed < 5.0  # well under the 30s sleep and 60s timeout


def test_local_runner_cancel_event_supplied_but_timeout_wins(tmp_path: pathlib.Path) -> None:
    """cancel_event supplied but never set and the child overruns timeout --
    the poll path's own timeout branch must still fire (not just cancel)."""
    path = _write_script(tmp_path, "import time; time.sleep(30)\n")
    spec = RunSpec(
        path=path, stdin=None, timeout=0.2, measure_memory=False, cancel_event=threading.Event()
    )

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert outcome.cancelled is False
    assert outcome.elapsed == 0.2


def test_local_runner_satisfies_runner_protocol() -> None:
    from stepik_grader.core.runner import Runner

    assert isinstance(LocalRunner(), Runner)


# ---------------------------------------------------------------------------
# LocalRunner.run() — TLE/cancel не должны зависать и терять вывод
# (issue #418 группа процессов, #419 stdin-deadlock, #421 частичный вывод)
# ---------------------------------------------------------------------------


def test_local_runner_returns_partial_output_on_timeout(tmp_path: pathlib.Path) -> None:
    """issue #421: вывод, напечатанный до TLE, доступен в outcome.stdout,
    а не выбрасывается."""
    path = _write_script(
        tmp_path,
        "import sys, time\nsys.stdout.write('partial\\n')\nsys.stdout.flush()\ntime.sleep(30)\n",
    )
    spec = RunSpec(path=path, stdin=None, timeout=0.3, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert b"partial" in outcome.stdout


def test_local_runner_timeout_does_not_hang_on_orphan_holding_pipe(
    tmp_path: pathlib.Path,
) -> None:
    """issue #418: решение спавнит внука, наследующего stdout, и оба висят.
    Без убийства группы процессов communicate() зависла бы на открытом pipe
    внука — run() должен вернуться за ~timeout, а не за время сна внука."""
    path = _write_script(
        tmp_path,
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n",
    )
    spec = RunSpec(path=path, stdin=None, timeout=0.3, measure_memory=False)

    t0 = time.perf_counter()
    outcome = LocalRunner().run(spec)
    wall = time.perf_counter() - t0

    assert outcome.timed_out is True
    assert wall < 8.0, f"зависание на pipe внука: {wall:.1f}s"


def test_local_runner_polling_large_stdin_non_reading_child_times_out(
    tmp_path: pathlib.Path,
) -> None:
    """issue #419: ребёнок не читает stdin и висит; большой stdin переполнил бы
    pipe-буфер и заблокировал бы синхронную запись до входа в poll-цикл. С
    записью stdin в отдельном потоке timeout/cancel всё равно срабатывают."""
    path = _write_script(tmp_path, "import time\ntime.sleep(30)\n")
    big_stdin = b"x" * (1024 * 1024)  # 1 MiB — заведомо больше pipe-буфера (~64 KiB)
    spec = RunSpec(
        path=path,
        stdin=big_stdin,
        timeout=0.3,
        measure_memory=False,
        cancel_event=threading.Event(),
    )

    t0 = time.perf_counter()
    outcome = LocalRunner().run(spec)
    wall = time.perf_counter() - t0

    assert outcome.timed_out is True
    assert wall < 8.0, f"deadlock записи stdin в главном потоке: {wall:.1f}s"


def test_sys_executable_used_for_interpreter(tmp_path: pathlib.Path) -> None:
    # Убедимся, что раннер использует ТОТ ЖЕ интерпретатор, что и текущий
    # процесс (важно для venv на Windows) -- не системный "python"/"python3".
    path = _write_script(tmp_path, "import sys; print(sys.executable)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode().strip() == sys.executable
