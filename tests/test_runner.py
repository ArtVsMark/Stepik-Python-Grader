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


def test_local_runner_satisfies_runner_protocol() -> None:
    from stepik_grader.core.runner import Runner

    assert isinstance(LocalRunner(), Runner)


def test_sys_executable_used_for_interpreter(tmp_path: pathlib.Path) -> None:
    # Убедимся, что раннер использует ТОТ ЖЕ интерпретатор, что и текущий
    # процесс (важно для venv на Windows) -- не системный "python"/"python3".
    path = _write_script(tmp_path, "import sys; print(sys.executable)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode().strip() == sys.executable
