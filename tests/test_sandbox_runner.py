"""Tests for core/sandbox/ — SandboxRunner MVP (issue #266).

Structure:
- Platform-independent unit tests (bootstrap argv building, ephemeral run-dir
  cleanup retry, SandboxRunner backend dispatch/SandboxUnavailableError) —
  run everywhere, no real sandbox tool needed.
- Real backend scenario tests, gated with ``pytest.mark.skipif`` on actual
  availability of that backend's OS tool/API, so they execute for real only
  on their native CI runner (ubuntu/macos/windows-latest) — same convention
  as ``test_executor.py``'s ``SIGALRM`` gating and ``test_storage.py``'s
  POSIX-only permission-bits gating.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

from stepik_grader.core.runner import RunSpec

# ---------------------------------------------------------------------------
# _posix_bootstrap.build_bootstrap_argv — pure string building, no OS calls.
# ---------------------------------------------------------------------------


def test_build_bootstrap_argv_shape() -> None:
    from stepik_grader.core.sandbox._posix_bootstrap import build_bootstrap_argv

    argv = build_bootstrap_argv(
        "/usr/bin/python3",
        "/tmp/run/solution.py",
        cpu_seconds=5,
        max_processes=16,
        max_file_bytes=1024,
    )

    assert argv[0] == "/usr/bin/python3"
    assert argv[1] == "-c"
    assert argv[3] == "/usr/bin/python3"
    assert argv[4] == "/tmp/run/solution.py"
    src = argv[2]
    assert "RLIMIT_CPU, (5, 5)" in src
    assert "RLIMIT_NPROC, (16, 16)" in src
    assert "RLIMIT_FSIZE, (1024, 1024)" in src
    assert "os.execv(sys.argv[1], sys.argv[1:])" in src


def test_build_bootstrap_argv_omits_memory_rlimit_by_default() -> None:
    from stepik_grader.core.sandbox._posix_bootstrap import build_bootstrap_argv

    argv = build_bootstrap_argv(
        "/usr/bin/python3", "/tmp/s.py", cpu_seconds=1, max_processes=1, max_file_bytes=1
    )

    assert "RLIMIT_AS" not in argv[2]


def test_build_bootstrap_argv_includes_memory_rlimit_when_given() -> None:
    from stepik_grader.core.sandbox._posix_bootstrap import build_bootstrap_argv

    argv = build_bootstrap_argv(
        "/usr/bin/python3",
        "/tmp/s.py",
        cpu_seconds=1,
        max_processes=1,
        max_file_bytes=1,
        max_memory_bytes=134217728,
    )

    assert "RLIMIT_AS, (134217728, 134217728)" in argv[2]


# ---------------------------------------------------------------------------
# _run_dir.ephemeral_run_dir — creates + robustly removes a per-run tmp dir
# (issue #266 fix: a straggler child process holding the dir open briefly
# after Job Object/bwrap teardown must not crash the caller — see
# _run_dir.py docstring).
# ---------------------------------------------------------------------------


def test_ephemeral_run_dir_creates_and_removes() -> None:
    from stepik_grader.core.sandbox._run_dir import ephemeral_run_dir

    captured: pathlib.Path
    with ephemeral_run_dir() as run_dir:
        captured = run_dir
        assert run_dir.is_dir()
        (run_dir / "marker.txt").write_text("x", encoding="utf-8")

    assert not captured.exists()


def test_ephemeral_run_dir_survives_transient_rmtree_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A straggler process can hold the dir open for a beat after teardown --
    retry a couple of times before giving up, don't propagate OSError."""
    from stepik_grader.core.sandbox import _run_dir

    calls = {"n": 0}
    real_rmtree = shutil.rmtree

    def _flaky_rmtree(path: object) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("still open")
        real_rmtree(path)

    monkeypatch.setattr(_run_dir.shutil, "rmtree", _flaky_rmtree)
    monkeypatch.setattr(_run_dir.time, "sleep", lambda _seconds: None)

    with _run_dir.ephemeral_run_dir() as run_dir:
        pass

    assert calls["n"] == 3
    assert not run_dir.exists()


def test_ephemeral_run_dir_gives_up_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the dir never frees up, warn and move on -- a leaked temp dir is a
    minor OS-cleanup annoyance, not a run-correctness failure."""
    from stepik_grader.core.sandbox import _run_dir

    def _always_fails(path: object) -> None:
        raise PermissionError("stuck forever")

    monkeypatch.setattr(_run_dir.shutil, "rmtree", _always_fails)
    monkeypatch.setattr(_run_dir.time, "sleep", lambda _seconds: None)

    with pytest.warns(UserWarning, match="could not remove ephemeral run dir"):
        with _run_dir.ephemeral_run_dir():
            pass


# ---------------------------------------------------------------------------
# SandboxRunner.__init__ — platform dispatch + SandboxUnavailableError
# propagation, without touching real OS sandbox tools.
# ---------------------------------------------------------------------------


def test_sandbox_runner_dispatches_by_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    import stepik_grader.core.sandbox as sandbox_pkg

    sentinel = object()

    class _FakeLinuxModule:
        @staticmethod
        def create_backend() -> object:
            return sentinel

    monkeypatch.setattr(sandbox_pkg.platform, "system", lambda: "Linux")
    monkeypatch.setitem(sys.modules, "stepik_grader.core.sandbox._linux", _FakeLinuxModule)

    runner = sandbox_pkg.SandboxRunner()

    assert runner._backend is sentinel


def test_sandbox_runner_unsupported_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import stepik_grader.core.sandbox as sandbox_pkg

    monkeypatch.setattr(sandbox_pkg.platform, "system", lambda: "PlanNine")

    with pytest.raises(sandbox_pkg.SandboxUnavailableError):
        sandbox_pkg.SandboxRunner()


def test_sandbox_runner_propagates_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import stepik_grader.core.sandbox as sandbox_pkg

    class _FakeLinuxModule:
        @staticmethod
        def create_backend() -> object:
            raise sandbox_pkg.SandboxUnavailableError("bwrap not found")

    monkeypatch.setattr(sandbox_pkg.platform, "system", lambda: "Linux")
    monkeypatch.setitem(sys.modules, "stepik_grader.core.sandbox._linux", _FakeLinuxModule)

    with pytest.raises(sandbox_pkg.SandboxUnavailableError, match="bwrap not found"):
        sandbox_pkg.SandboxRunner()


# ---------------------------------------------------------------------------
# Real backend scenarios -- skipif-gated on actual tool/API availability so
# they execute for real on their native CI runner (issue #266 verification
# plan: cannot exercise Linux/macOS backends on a Windows dev machine).
# ---------------------------------------------------------------------------


def _write_script(tmp_path: pathlib.Path, body: str, name: str = "sol.py") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only backend")
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap (bwrap) not installed")
class TestLinuxSandboxRunner:
    def _runner(self):
        from stepik_grader.core.sandbox._linux import create_backend

        return create_backend()

    def test_normal_run_ac(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "print('hello')\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.launch_error is None
        assert outcome.returncode == 0
        assert outcome.stdout.decode().strip() == "hello"

    def test_write_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "escape-target.txt"
        path = _write_script(
            tmp_path,
            f"open({str(target)!r}, 'w').write('pwned')\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert not target.exists()
        assert outcome.returncode != 0

    def test_network_blocked(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(3)\n"
            "s.connect(('93.184.216.34', 80))\n"
            "print('connected')\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=8.0))
        assert outcome.returncode != 0
        assert b"connected" not in outcome.stdout

    def test_fork_bomb_contained(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "import subprocess, sys\n"
            "procs = []\n"
            "for _ in range(200):\n"
            "    cmd = [sys.executable, '-c', 'import time; time.sleep(5)']\n"
            "    procs.append(subprocess.Popen(cmd))\n"
            "print('spawned', len(procs))\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=8.0))
        assert outcome.returncode != 0
        assert b"spawned 200" not in outcome.stdout

    def test_memory_overrun_violation(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "data = []\nwhile True:\n    data.append(bytearray(10 * 1024 * 1024))\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=10.0, max_memory_mb=64))
        assert outcome.timed_out is False
        # Kernel RLIMIT_AS rejection (RE-style MemoryError) or our own psutil
        # backstop (sandbox_violation="memory") are both acceptable -- see
        # RunOutcome.sandbox_violation docstring on why we don't force one.
        assert outcome.sandbox_violation == "memory" or outcome.returncode != 0

    def test_output_size_violation(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.sandbox_violation == "output_size"

    def test_infinite_loop_still_times_out(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "while True:\n    pass\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=2.0))
        assert outcome.timed_out is True


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only backend")
@pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="sandbox-exec not available")
class TestMacSandboxRunner:
    def _runner(self):
        from stepik_grader.core.sandbox._macos import create_backend

        return create_backend()

    def test_normal_run_ac(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "print('hello')\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.launch_error is None
        assert outcome.returncode == 0
        assert outcome.stdout.decode().strip() == "hello"

    def test_write_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "escape-target.txt"
        path = _write_script(tmp_path, f"open({str(target)!r}, 'w').write('pwned')\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert not target.exists()
        assert outcome.returncode != 0

    def test_network_blocked(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(3)\n"
            "s.connect(('93.184.216.34', 80))\n"
            "print('connected')\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=8.0))
        assert outcome.returncode != 0
        assert b"connected" not in outcome.stdout

    def test_memory_overrun_violation(self, tmp_path: pathlib.Path) -> None:
        """No RLIMIT_AS on Darwin -- psutil polling is the only enforcement,
        so allow a somewhat larger overshoot tolerance than Linux/Windows."""
        path = _write_script(
            tmp_path,
            "data = []\nwhile True:\n    data.append(bytearray(10 * 1024 * 1024))\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=10.0, max_memory_mb=64))
        assert outcome.timed_out is False
        assert outcome.sandbox_violation == "memory"

    def test_output_size_violation(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path, "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n"
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.sandbox_violation == "output_size"

    def test_infinite_loop_still_times_out(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "while True:\n    pass\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=2.0))
        assert outcome.timed_out is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only backend")
class TestWindowsSandboxRunner:
    def _runner(self):
        from stepik_grader.core.sandbox._windows import create_backend

        return create_backend()

    def test_normal_run_ac(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "print('hello')\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.launch_error is None
        assert outcome.returncode == 0
        assert outcome.stdout.decode().strip() == "hello"

    def test_stdin_passed_through(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "print(input())\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=b"world\n", timeout=5.0))
        assert outcome.stdout.decode().strip() == "world"

    def test_memory_overrun_surfaces_as_violation_or_re(self, tmp_path: pathlib.Path) -> None:
        """Job Object's JOB_OBJECT_LIMIT_JOB_MEMORY is commit-charge-based and
        typically rejects the allocation fast enough that it surfaces as an
        ordinary MemoryError/RE rather than our own psutil poll -- confirmed
        by manual testing on this machine (see _windows.py docstring). Either
        outcome is an acceptable containment of the overrun."""
        path = _write_script(
            tmp_path,
            "data = []\nwhile True:\n    data.append(bytearray(10 * 1024 * 1024))\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=10.0, max_memory_mb=64))
        assert outcome.timed_out is False
        assert outcome.sandbox_violation == "memory" or outcome.returncode != 0

    def test_output_size_violation(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path, "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n"
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.sandbox_violation == "output_size"

    def test_process_count_contained(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(
            tmp_path,
            "import subprocess, sys\n"
            "procs = []\n"
            "for _ in range(200):\n"
            "    cmd = [sys.executable, '-c', 'import time; time.sleep(5)']\n"
            "    procs.append(subprocess.Popen(cmd))\n"
            "print('spawned', len(procs))\n",
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=8.0))
        assert outcome.returncode != 0
        assert b"spawned 200" not in outcome.stdout

    def test_infinite_loop_still_times_out(self, tmp_path: pathlib.Path) -> None:
        path = _write_script(tmp_path, "while True:\n    pass\n")
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=2.0))
        assert outcome.timed_out is True


# ---------------------------------------------------------------------------
# Golden comparison: on whatever platform is actually running this suite,
# LocalRunner and SandboxRunner must agree on the broad AC/RE/TLE shape for
# the same small solution set -- sandboxing must not change grading verdicts
# for well-behaved code (issue #266 plan's "golden comparison").
# ---------------------------------------------------------------------------


def _current_platform_backend_available() -> bool:
    if sys.platform == "win32":
        return True
    if sys.platform == "linux":
        return shutil.which("bwrap") is not None
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    return False


@pytest.mark.skipif(
    not _current_platform_backend_available(),
    reason="no sandbox backend available for this platform/environment",
)
class TestGoldenComparisonAgainstLocalRunner:
    def _both_outcomes(self, tmp_path: pathlib.Path, body: str):
        from stepik_grader.core.runner import LocalRunner
        from stepik_grader.core.sandbox import SandboxRunner

        path = _write_script(tmp_path, body)
        local = LocalRunner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        sandboxed = SandboxRunner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        return local, sandboxed

    def test_ac_solution_agrees(self, tmp_path: pathlib.Path) -> None:
        local, sandboxed = self._both_outcomes(tmp_path, "print('ok')\n")
        assert local.returncode == sandboxed.returncode == 0
        assert local.stdout.decode().strip() == sandboxed.stdout.decode().strip() == "ok"

    def test_re_solution_agrees(self, tmp_path: pathlib.Path) -> None:
        local, sandboxed = self._both_outcomes(tmp_path, "raise ValueError('boom')\n")
        assert local.returncode != 0
        assert sandboxed.returncode != 0

    def test_tle_solution_agrees(self, tmp_path: pathlib.Path) -> None:
        local, sandboxed = self._both_outcomes(tmp_path, "while True:\n    pass\n")
        assert local.timed_out is True
        assert sandboxed.timed_out is True
