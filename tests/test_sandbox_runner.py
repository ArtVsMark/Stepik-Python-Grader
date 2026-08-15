"""Tests for core/sandbox/ — SandboxRunner MVP (issue #266).

Structure:
- Platform-independent unit tests (bootstrap argv building, ephemeral run-dir
  cleanup retry, SandboxRunner backend dispatch/SandboxUnavailableError) —
  run everywhere, no real sandbox tool needed.
- Real backend scenario tests, gated with ``pytest.mark.skipif`` on actual
  availability of that backend's OS tool/API, so they execute for real only
  on their native CI runner (ubuntu/macos/windows-latest) — same convention
  as ``test_storage.py``'s POSIX-only permission-bits gating.
"""

from __future__ import annotations

import functools
import logging
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

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
    from stepik_grader.core.run_dir import ephemeral_run_dir

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
    from stepik_grader.core import run_dir as _run_dir

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
    from stepik_grader.core import run_dir as _run_dir

    def _always_fails(path: object) -> None:
        raise PermissionError("stuck forever")

    monkeypatch.setattr(_run_dir.shutil, "rmtree", _always_fails)
    monkeypatch.setattr(_run_dir.time, "sleep", lambda _seconds: None)

    with pytest.warns(UserWarning, match="could not remove ephemeral run dir"):
        with _run_dir.ephemeral_run_dir():
            pass


# ---------------------------------------------------------------------------
# issue #996 (OPS-1-07) — осиротевшие каталоги прошлых прогонов.
#
# У `finally` есть предел: SIGKILL, падение интерпретатора, выключение машины
# посреди прогона — и каталог остаётся в temp навсегда. Подметает их первый за
# процесс вызов `ephemeral_run_dir`. Границы узкие: чужой каталог удаляется
# только по возрасту, и только если он заведомо наш.
# ---------------------------------------------------------------------------


class TestSweepOrphanedRunDirs:
    def _aged(self, path: pathlib.Path, hours: float) -> None:
        stamp = time.time() - hours * 3600
        os.utime(path, (stamp, stamp), follow_symlinks=False)

    def test_stale_dir_of_ours_is_removed(self, tmp_path: pathlib.Path) -> None:
        from stepik_grader.core import run_dir as _run_dir

        orphan = tmp_path / f"{_run_dir.RUN_DIR_PREFIX}dead"
        orphan.mkdir()
        (orphan / "wrapper.py").write_text("x", encoding="utf-8")
        self._aged(orphan, hours=48)

        assert _run_dir.sweep_orphans(root=tmp_path) == 1
        assert not orphan.exists()

    def test_fresh_dir_is_left_alone(self, tmp_path: pathlib.Path) -> None:
        """Рядом может работать другой грейдер — его каталог трогать нельзя."""
        from stepik_grader.core import run_dir as _run_dir

        live = tmp_path / f"{_run_dir.RUN_DIR_PREFIX}live"
        live.mkdir()

        assert _run_dir.sweep_orphans(root=tmp_path) == 0
        assert live.is_dir()

    def test_foreign_names_are_never_touched(self, tmp_path: pathlib.Path) -> None:
        """Подметается только свой префикс: в temp живёт не только грейдер."""
        from stepik_grader.core import run_dir as _run_dir

        alien = tmp_path / "pytest-of-root"
        alien.mkdir()
        self._aged(alien, hours=48)

        assert _run_dir.sweep_orphans(root=tmp_path) == 0
        assert alien.is_dir()

    def test_symlink_is_not_followed(self, tmp_path: pathlib.Path) -> None:
        """`rmtree` по ссылке ушёл бы удалять чужое дерево целиком."""
        from stepik_grader.core import run_dir as _run_dir

        target = tmp_path / "precious"
        target.mkdir()
        (target / "data.txt").write_text("важное", encoding="utf-8")
        link = tmp_path / f"{_run_dir.RUN_DIR_PREFIX}link"
        link.symlink_to(target)
        self._aged(link, hours=48)

        assert _run_dir.sweep_orphans(root=tmp_path) == 0
        assert (target / "data.txt").exists()

    def test_unreadable_temp_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Уборка мусора не вправе ронять прогон, ради которого её позвали."""
        from stepik_grader.core import run_dir as _run_dir

        def _boom(self: object, pattern: str) -> object:
            raise PermissionError("temp закрыт")

        monkeypatch.setattr(pathlib.Path, "glob", _boom)

        assert _run_dir.sweep_orphans() == 0

    def test_sweep_runs_once_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Иначе при сотне кейсов temp сканировался бы сто раз."""
        from stepik_grader.core import run_dir as _run_dir

        calls = {"n": 0}
        monkeypatch.setattr(_run_dir, "_swept", False)
        monkeypatch.setattr(
            _run_dir, "sweep_orphans", lambda *a, **kw: calls.__setitem__("n", calls["n"] + 1)
        )

        for _ in range(3):
            with _run_dir.ephemeral_run_dir():
                pass

        assert calls["n"] == 1


def test_failed_cleanup_reaches_the_diagnostic_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """issue #996 (OPS-1-07): единственным следом отказа было `warnings.warn`.

    В CLI оно по умолчанию не показывается, поэтому симптом «temp пухнет»
    приходил без причины — а диагностический лог для того и заведён.
    """
    from stepik_grader.core import run_dir as _run_dir

    monkeypatch.setattr(_run_dir.shutil, "rmtree", _raise_permission_error)
    monkeypatch.setattr(_run_dir.time, "sleep", lambda _seconds: None)
    # `configure_diagnostics` гасит propagate у корневого логгера пакета, и в
    # полном прогоне (test_diag_log.py) это состояние доживает сюда: без явного
    # восстановления тест зелен в одиночку и красен в наборе.
    monkeypatch.setattr(logging.getLogger("stepik_grader"), "propagate", True)

    with caplog.at_level(logging.WARNING, logger=_run_dir._log.name):
        with pytest.warns(UserWarning, match="could not remove ephemeral run dir"):
            with _run_dir.ephemeral_run_dir():
                pass

    assert any(
        "не удалось удалить временный каталог" in record.getMessage() for record in caplog.records
    )


def _raise_permission_error(path: object) -> None:
    raise PermissionError("stuck forever")


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
    # issue #992: `from ... import _linux` берёт АТРИБУТ пакета, если модуль уже
    # импортирован по-настоящему, и подмена только в sys.modules перестаёт
    # действовать. Тест зеленел лишь пока никто до него не создавал настоящий
    # SandboxRunner — то есть зависел от порядка файлов в прогоне.
    monkeypatch.setattr(sandbox_pkg, "_linux", _FakeLinuxModule, raising=False)

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
    monkeypatch.setattr(sandbox_pkg, "_linux", _FakeLinuxModule, raising=False)  # issue #992

    with pytest.raises(sandbox_pkg.SandboxUnavailableError, match="bwrap not found"):
        sandbox_pkg.SandboxRunner()


# ---------------------------------------------------------------------------
# _posix_common.run_argv_with_limits — partial output on violations + tree-RSS
# memory detector in isolation from RLIMIT_AS (issue #556). These run argv
# directly (no bwrap needed) on POSIX; the real bwrap e2e stays in
# TestLinuxSandboxRunner. On Windows the sandbox uses _windows.py instead.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="_posix_common is the POSIX sandbox path")
def test_output_size_violation_carries_partial_stdout() -> None:
    """issue #556: вывод, напечатанный до обрыва по лимиту размера, не теряется."""
    from stepik_grader.core.sandbox._posix_common import run_argv_with_limits

    code = (
        "import sys\n"
        "sys.stdout.write('BEGIN\\n')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    sys.stdout.write('y' * 65536)\n"
    )
    outcome = run_argv_with_limits(
        [sys.executable, "-c", code], stdin=None, timeout=5.0, max_output_bytes=4096
    )
    assert outcome.sandbox_violation == "output_size"
    assert b"BEGIN" in outcome.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="_posix_common is the POSIX sandbox path")
def test_memory_violation_carries_partial_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """issue #556: memory-violation-ветка прикладывает частичный stdout (как TLE).

    Детектор памяти правится по времени (под лимитом ~0.6с — ребёнок успевает
    напечатать, затем над лимитом), без реальной аллокации/тайминговой флаки.
    """
    from stepik_grader.core.sandbox import _posix_common

    start = time.perf_counter()

    def _fake_tree_rss(_proc: object) -> float:
        return 9999.0 if time.perf_counter() - start > 0.6 else 1.0

    monkeypatch.setattr(_posix_common, "sample_tree_rss", _fake_tree_rss)

    code = "import sys, time\nsys.stdout.write('HELLO\\n')\nsys.stdout.flush()\ntime.sleep(30)\n"
    outcome = _posix_common.run_argv_with_limits(
        [sys.executable, "-c", code],
        stdin=None,
        timeout=10.0,
        max_output_bytes=1_000_000,
        max_memory_mb=64,
    )
    assert outcome.sandbox_violation == "memory"
    assert outcome.timed_out is False
    assert b"HELLO" in outcome.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="_posix_common is the POSIX sandbox path")
def test_memory_detector_fires_from_tree_sample_without_rlimit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #556: psutil tree-RSS детектор в ИЗОЛЯЦИИ от RLIMIT_AS (обычный argv,
    без bwrap) флагает превышение. До фикса замерялся только pid-обёртки, и
    память внука могла не попасть под лимит — теперь считается всё поддерево."""
    from stepik_grader.core.sandbox import _posix_common

    monkeypatch.setattr(_posix_common, "sample_tree_rss", lambda _p: 9999.0)

    code = "import time\ntime.sleep(30)\n"
    outcome = _posix_common.run_argv_with_limits(
        [sys.executable, "-c", code],
        stdin=None,
        timeout=10.0,
        max_output_bytes=1_000_000,
        max_memory_mb=64,
    )
    assert outcome.sandbox_violation == "memory"
    assert outcome.timed_out is False


@pytest.mark.skipif(sys.platform == "win32", reason="_posix_common is the POSIX sandbox path")
def test_re_outcome_carries_partial_stdout() -> None:
    """issue #556: RE-исход (ненулевой код) несёт stdout, напечатанный до падения."""
    from stepik_grader.core.sandbox._posix_common import run_argv_with_limits

    code = "import sys\nprint('printed-before-crash')\nsys.exit(3)\n"
    outcome = run_argv_with_limits(
        [sys.executable, "-c", code], stdin=None, timeout=5.0, max_output_bytes=1_000_000
    )
    assert outcome.returncode == 3
    assert outcome.sandbox_violation is None
    assert b"printed-before-crash" in outcome.stdout


# ---------------------------------------------------------------------------
# Real backend scenarios -- skipif-gated on actual tool/API availability so
# they execute for real on their native CI runner (issue #266 verification
# plan: cannot exercise Linux/macOS backends on a Windows dev machine).
# ---------------------------------------------------------------------------


def _write_script(tmp_path: pathlib.Path, body: str, name: str = "sol.py") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _probe_usrmerge_symlink_args() -> list[str]:
    """usrmerge-симлинки для пробы netns (issue #420).

    Локальная копия логики ``_linux._usrmerge_symlink_args`` — сознательно НЕ
    импортируем боевой модуль здесь, потому что проба вызывается в ``skipif`` на
    этапе сбора тестов, а ранний импорт ``_linux`` проставил бы атрибут
    ``_linux`` на пакете и сломал бы юнит-тесты диспетчеризации, которые
    подменяют модуль через ``sys.modules``. Пробе не нужна точность боевого
    хелпера — достаточно, чтобы ``/usr/bin/true`` успешно запустился, когда
    netns работает.
    """
    args: list[str] = []
    for link in ("/lib", "/lib64", "/lib32", "/libx32", "/bin", "/sbin"):
        p = pathlib.Path(link)
        try:
            if p.is_symlink() and p.resolve().is_relative_to("/usr"):
                args += ["--symlink", str(p.readlink()), link]
        except OSError:
            pass
    return args


@functools.lru_cache(maxsize=1)
def _bwrap_netns_works() -> bool:
    """Проверить, может ли ``bwrap --unshare-net`` поднять loopback здесь (issue #420).

    Раннеры GitHub Actions это запрещают (``RTM_NEWADDR: Operation not
    permitted``), а обычный десктоп/privileged-контейнер — разрешают. Гоняет
    максимум один throwaway ``bwrap`` (кэшируется). Успех = netns доступен и
    ``/usr/bin/true`` реально исполнился; любой ненулевой код/ошибка = netns
    недоступен здесь → сетевые sandbox-тесты корректно скипаются.
    """
    if sys.platform != "linux":
        return False
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    argv = [bwrap, "--ro-bind", "/usr", "/usr"]
    argv += _probe_usrmerge_symlink_args()
    argv += ["--tmpfs", "/tmp", "--dev", "/dev", "--proc", "/proc"]
    argv += ["--unshare-net", "--unshare-user", "--", "/usr/bin/true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# --- Shared scenario assertions (issue #420): one source of truth for what each
# isolation guarantee means. Each takes an already-constructed Runner, so a single
# scenario body serves the Linux class here and stays reusable for other backends. ---


def _assert_normal_run(runner, tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print('hello')\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=5.0))
    assert outcome.launch_error is None
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "hello"


def _assert_write_outside_run_dir_blocked(runner, tmp_path: pathlib.Path) -> None:
    target = tmp_path / "escape-target.txt"
    path = _write_script(tmp_path, f"open({str(target)!r}, 'w').write('pwned')\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=5.0))
    assert not target.exists()
    assert outcome.returncode != 0


def _assert_network_blocked(runner, tmp_path: pathlib.Path) -> None:
    """Изоляция сети доказывается локальным listener'ом, а не внешним адресом.

    issue #800 (QA-02): прежняя версия коннектилась к 93.184.216.34:80 и
    проходила вхолостую без интернета — `connect` бросал `OSError` и когда сеть
    закрыта песочницей, и когда её просто нет. То есть тест, заведённый ради
    ключевой гарантии SEC-CORE-04, не мог её опровергнуть: сними кто-нибудь
    `--unshare-net`, он остался бы зелёным.

    Здесь тест сам поднимает TCP-listener на `127.0.0.1` и требует, чтобы
    соединение НЕ дошло. Детерминированно, работает оффлайн и строже: netns не
    пускает даже к loopback хоста. Приём взят из
    `tests/test_w6_windows_sandbox_gaps.py`, где им подтверждали обратное —
    отсутствие изоляции у `LocalRunner`.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    accepted = threading.Event()

    def _accept() -> None:
        try:
            conn, _ = srv.accept()
            accepted.set()
            conn.close()
        except OSError:
            pass

    accepter = threading.Thread(target=_accept, name="sandbox-net-accept", daemon=True)
    accepter.start()
    try:
        path = _write_script(
            tmp_path,
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(3)\n"
            f"s.connect(('127.0.0.1', {port}))\n"
            "s.sendall(b'exfil')\n"
            "print('connected')\n",
        )
        outcome = runner.run(RunSpec(path=path, stdin=None, timeout=8.0))
    finally:
        srv.close()
        accepter.join(timeout=2)

    # Главное утверждение — соединение не принято. Оно и отличает «сеть
    # закрыта» от «сети нет»: listener гарантированно доступен, если изоляции
    # не осталось.
    assert not accepted.is_set(), "песочница пропустила соединение к локальному listener'у"
    assert outcome.returncode != 0
    assert b"connected" not in outcome.stdout


def _assert_fork_bomb_contained(runner, tmp_path: pathlib.Path) -> None:
    path = _write_script(
        tmp_path,
        "import subprocess, sys\n"
        "procs = []\n"
        "for _ in range(200):\n"
        "    cmd = [sys.executable, '-c', 'import time; time.sleep(5)']\n"
        "    procs.append(subprocess.Popen(cmd))\n"
        "print('spawned', len(procs))\n",
    )
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=8.0))
    assert outcome.returncode != 0
    assert b"spawned 200" not in outcome.stdout


def _assert_memory_overrun_violation(runner, tmp_path: pathlib.Path) -> None:
    path = _write_script(
        tmp_path,
        "data = []\nwhile True:\n    data.append(bytearray(10 * 1024 * 1024))\n",
    )
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=10.0, max_memory_mb=64))
    assert outcome.timed_out is False
    # Kernel RLIMIT_AS rejection (RE-style MemoryError) or our own psutil
    # backstop (sandbox_violation="memory") are both acceptable -- see
    # RunOutcome.sandbox_violation docstring on why we don't force one.
    assert outcome.sandbox_violation == "memory" or outcome.returncode != 0


def _assert_output_size_violation(runner, tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=5.0))
    assert outcome.sandbox_violation == "output_size"


def _assert_infinite_loop_times_out(runner, tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "while True:\n    pass\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=2.0))
    assert outcome.timed_out is True


def _assert_large_stdin_to_non_reader_times_out(runner, tmp_path: pathlib.Path) -> None:
    """Большой stdin к нечитающему решению даёт TLE, а не вечное зависание.

    issue #796: backend'ы писали stdin синхронно в главном потоке, до входа в
    цикл ожидания. Решение, не читающее ввод, при stdin больше буфера pipe
    (~64 KiB на Linux) блокировало `write` — и проверка таймаута не выполнялась
    НИ РАЗУ: прогон висел до самостоятельного завершения ребёнка. В CLI это
    зависание процесса, под `--serve --sandbox` — навсегда занятый воркер (при
    дефолтных двух двух таких прогонов хватало, чтобы убить async-подсистему).

    Тот же deadlock закрыли для `LocalRunner` ещё в #419; здесь он воспроизводил
    себя в другом файле. Проверка идёт по факту завершения: если фикс откатят,
    тест не «упадёт по ассерту», а повиснет — поэтому у прогона есть общий
    дедлайн pytest-timeout, и висящий тест уронит прогон, а не заморозит его.
    """
    path = _write_script(tmp_path, "import time\ntime.sleep(30)\n")
    payload = b"x" * (1024 * 1024)  # 1 MiB — заведомо больше любого pipe-буфера
    start = time.perf_counter()
    outcome = runner.run(RunSpec(path=path, stdin=payload, timeout=2.0))
    elapsed = time.perf_counter() - start

    assert outcome.timed_out is True, "нечитающее решение должно получить TLE"
    # Запас на kill дерева и дренаж, но заметно меньше sleep(30) в решении:
    # без фикса выход произошёл бы только через 30 с.
    assert elapsed < 15.0, f"прогон занял {elapsed:.1f} с — похоже на блокировку записи stdin"


def _assert_tle_keeps_partial_output(runner, tmp_path: pathlib.Path) -> None:
    """При TLE возвращается то, что решение успело напечатать (issue #798).

    Reader-потоки уже слили вывод в память; выбрасывать его — значит оставлять
    студента без диагноза: «превышено время» без единой строки не подсказывает,
    где цикл ушёл в разнос. POSIX-путь так делает с #421/#556, Windows-backend
    отставал и терял вывод у всех аварийных исходов.
    """
    path = _write_script(tmp_path, "print('BEGIN', flush=True)\nwhile True:\n    pass\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=2.0))

    assert outcome.timed_out is True
    assert b"BEGIN" in outcome.stdout, "частичный вывод потерян при TLE"


def _assert_grandchildren_killed_on_timeout(runner, tmp_path: pathlib.Path) -> None:
    """После TLE не остаётся живых внуков (issue #798).

    `proc.kill()` убивает процесс изоляции; на Linux этого хватает (bwrap
    уносит PID-namespace целиком), а на macOS форкнутые внуки продолжали жить:
    ветка добивания дерева срабатывала, только если reap упирался в таймаут, —
    а ребёнок, умерший сразу, оставлял внуков навсегда.

    Внук пишет файл-маркер уже ПОСЛЕ смерти родителя: маркер на диске означает,
    что он пережил уборку.
    """
    marker = tmp_path / "grandchild-alive.txt"
    grandchild = f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('alive')"
    path = _write_script(
        tmp_path,
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        "time.sleep(30)\n",
    )
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=2.0))
    assert outcome.timed_out is True

    time.sleep(4)  # выживший внук успел бы дожить и создать маркер
    assert not marker.exists(), "внук пережил уборку после TLE"


def _assert_cancel_event_stops_run(runner, tmp_path: pathlib.Path) -> None:
    """Отмена под изоляцией останавливает прогон и даёт `cancelled`, а не TLE.

    issue #797: `RunSpec.cancel_event` не читался НИ ОДНИМ backend'ом — поле
    доезжало до `run()` и молча игнорировалось. Под `--serve --sandbox` кнопка
    «Отмена» ничего не делала, вердикт CANCELLED был недостижим, а воркер
    держался до конца прогона (при дефолтных двух воркерах — до половины
    async-подсистемы за один клик).

    Решение спит заведомо дольше и таймаута, и времени отмены: если отмена не
    сработает, тест увидит TLE вместо `cancelled` — то есть отличит починку от
    случайного совпадения.
    """
    path = _write_script(tmp_path, "import time\ntime.sleep(20)\n")
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()

    start = time.perf_counter()
    outcome = runner.run(
        RunSpec(path=path, stdin=None, timeout=15.0, cancel_event=cancel),
    )
    elapsed = time.perf_counter() - start

    assert outcome.cancelled is True, "отмена под --sandbox должна давать cancelled"
    assert outcome.timed_out is False, "отмена — не таймаут: UI различает эти исходы"
    assert elapsed < 10.0, f"прогон занял {elapsed:.1f} с — отмена не прервала ожидание"


def _assert_read_outside_run_dir_blocked(runner, tmp_path: pathlib.Path) -> None:
    """Чтение секрета ВНЕ run_dir заблокировано (SEC-CORE-03, escape-PoC #648).

    Решение читает файл по абсолютному пути вне run_dir и печатает его —
    эксфильтрация секрета через stdout. Backend с изоляцией ЧТЕНИЯ (Linux bwrap:
    файл не в bind'ах, host ``/tmp`` скрыт свежим ``--tmpfs``) чтения не даёт —
    секрет не попадает в stdout, процесс падает (``FileNotFoundError``). Контраст
    с macOS (``(allow file-read*)``) и Windows, где чтение НЕ изолировано —
    характеризующие тесты в соответствующих классах.
    """
    secret = "S3CRET-sandbox-exfil-648"
    secret_file = tmp_path / "outside_secret.txt"
    secret_file.write_text(secret, encoding="utf-8")
    path = _write_script(tmp_path, f"print(open({str(secret_file)!r}, encoding='utf-8').read())\n")
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=5.0))
    assert secret.encode() not in outcome.stdout
    assert outcome.returncode != 0


def _assert_symlink_write_outside_blocked(runner, tmp_path: pathlib.Path) -> None:
    """Запись через symlink из run_dir наружу заблокирована (escape-PoC #648).

    Классический symlink-побег: решение создаёт в run_dir символическую ссылку на
    абсолютный путь ВНЕ run_dir и пишет через неё — это обошло бы наивную
    prefix-проверку пути (ссылка «выглядит» внутри run_dir). Ни один POSIX-backend
    так не проверяет: bwrap изолирует mount-namespace (цель вне bind'ов там
    отсутствует), Seatbelt применяет правило записи к canonical/резолвленному пути
    (target вне run_dir → deny). Ожидание — побег НЕ проходит: host-target не
    создан, процесс падает. Красный тест = запись через symlink обходит
    ограничение записи (реальный побег, в отличие от прямой записи вовне) → это
    уже находка, тогда тест переписывается на характеризующий и снимается оговорка.

    run_dir находится через ``dirname(__file__)`` (сам скрипт лежит в run_dir),
    а не через cwd — cwd на macOS-backend'е не гарантированно равен run_dir.
    """
    target = tmp_path / "symlink-escape-target.txt"
    path = _write_script(
        tmp_path,
        "import os\n"
        "run_dir = os.path.dirname(os.path.abspath(__file__))\n"
        "link = os.path.join(run_dir, 'escape_link')\n"
        f"os.symlink({str(target)!r}, link)\n"
        "open(link, 'w', encoding='utf-8').write('pwned-via-symlink')\n",
    )
    outcome = runner.run(RunSpec(path=path, stdin=None, timeout=5.0))
    assert not target.exists()
    assert outcome.returncode != 0


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only backend")
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap (bwrap) not installed")
@pytest.mark.skipif(
    not _bwrap_netns_works(),
    reason="bwrap --unshare-net unavailable here (e.g. GitHub Actions loopback restriction, #420)",
)
class TestLinuxSandboxRunner:
    """Полная изоляция bwrap, включая сетевую (netns). Гейт ``_bwrap_netns_works``
    скипает класс там, где непривилегированный netns недоступен (обычные
    GHA-раннеры, issue #420); реально гоняется локально и в CI-job'е
    ``sandbox-linux`` (privileged-контейнер, где netns/userns доступны)."""

    def _runner(self):
        from stepik_grader.core.sandbox._linux import create_backend

        return create_backend()

    def test_normal_run_ac(self, tmp_path: pathlib.Path) -> None:
        _assert_normal_run(self._runner(), tmp_path)

    def test_write_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        _assert_write_outside_run_dir_blocked(self._runner(), tmp_path)

    def test_network_blocked(self, tmp_path: pathlib.Path) -> None:
        _assert_network_blocked(self._runner(), tmp_path)

    def test_fork_bomb_contained(self, tmp_path: pathlib.Path) -> None:
        _assert_fork_bomb_contained(self._runner(), tmp_path)

    def test_memory_overrun_violation(self, tmp_path: pathlib.Path) -> None:
        _assert_memory_overrun_violation(self._runner(), tmp_path)

    def test_output_size_violation(self, tmp_path: pathlib.Path) -> None:
        _assert_output_size_violation(self._runner(), tmp_path)

    def test_infinite_loop_still_times_out(self, tmp_path: pathlib.Path) -> None:
        _assert_infinite_loop_times_out(self._runner(), tmp_path)

    def test_large_stdin_to_non_reader_times_out(self, tmp_path: pathlib.Path) -> None:
        _assert_large_stdin_to_non_reader_times_out(self._runner(), tmp_path)

    def test_cancel_event_stops_run(self, tmp_path: pathlib.Path) -> None:
        _assert_cancel_event_stops_run(self._runner(), tmp_path)

    def test_tle_keeps_partial_output(self, tmp_path: pathlib.Path) -> None:
        _assert_tle_keeps_partial_output(self._runner(), tmp_path)

    def test_grandchildren_killed_on_timeout(self, tmp_path: pathlib.Path) -> None:
        _assert_grandchildren_killed_on_timeout(self._runner(), tmp_path)

    def test_read_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        # issue #648: bwrap изолирует ЧТЕНИЕ (файл вне bind'ов недоступен) —
        # в отличие от macOS/Windows; секрет не эксфильтруется.
        _assert_read_outside_run_dir_blocked(self._runner(), tmp_path)

    def test_symlink_write_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        # issue #648: symlink-побег — bwrap резолвит цель В mount-namespace, вне
        # bind'ов её нет; запись через ссылку наружу не проходит.
        _assert_symlink_write_outside_blocked(self._runner(), tmp_path)


def test_linux_sandbox_not_silently_skipped() -> None:
    """issue #420 guard (крит. 3): в CI-job'е ``sandbox-linux`` (privileged-
    контейнер) установлен ``STEPIK_REQUIRE_SANDBOX_TESTS=1`` — тогда молчаливый
    skip Linux-песочницы обязан стать ЖЁСТКИМ падением, а не тихим no-op.
    Проверяем всё, от чего зависит запуск полного ``TestLinuxSandboxRunner``:
    Linux + установленный bwrap + рабочий netns (``--unshare-user``/
    ``--unshare-net`` под privileged). Если что-то отвалилось — job краснеет,
    а не проходит с тихо скипнутым классом.

    Само-гейт по платформе И переменной (issue #558): матричные mac/win-job'ы
    тоже ставят ``STEPIK_REQUIRE_SANDBOX_TESTS=1`` (свои guard'ы ниже) — без
    проверки платформы этот тест ложно падал бы там на «assert linux».
    """
    if sys.platform != "linux" or not os.environ.get("STEPIK_REQUIRE_SANDBOX_TESTS"):
        pytest.skip("guard enforced only on Linux with STEPIK_REQUIRE_SANDBOX_TESTS")
    assert shutil.which("bwrap") is not None, (
        "bwrap must be installed in the sandbox-linux job — sandbox tests would "
        "otherwise silently skip (#420 guard)"
    )
    assert _bwrap_netns_works(), (
        "bwrap --unshare-net/--unshare-user must work in the privileged sandbox-linux "
        "job — the full TestLinuxSandboxRunner class would otherwise silently skip (#420 guard)"
    )


def test_macos_sandbox_not_silently_skipped() -> None:
    """issue #558 guard (QA-3): обобщение #420-паттерна на macOS. В матричном
    macOS-job'е установлен ``STEPIK_REQUIRE_SANDBOX_TESTS=1`` — тогда молчаливый
    skip ``TestMacSandboxRunner`` (нет ``sandbox-exec``) обязан стать ЖЁСТКИМ
    падением. Само-гейт по платформе И переменной (см. Linux-guard выше).
    """
    if sys.platform != "darwin" or not os.environ.get("STEPIK_REQUIRE_SANDBOX_TESTS"):
        pytest.skip("guard enforced only on macOS with STEPIK_REQUIRE_SANDBOX_TESTS")
    assert shutil.which("sandbox-exec") is not None, (
        "sandbox-exec must be available on the macOS runner — TestMacSandboxRunner "
        "would otherwise silently skip (#558 guard)"
    )


def test_windows_sandbox_not_silently_skipped() -> None:
    """issue #558 guard (QA-3): обобщение #420-паттерна на Windows. В матричном
    Windows-job'е установлен ``STEPIK_REQUIRE_SANDBOX_TESTS=1`` — Job Objects
    backend (ctypes) встроен, поэтому проверяем, что он реально КОНСТРУИРУЕТСЯ
    (сломанный backend иначе не «скипнулся» бы, а тихо ронял бы каждый тест
    класса). Само-гейт по платформе И переменной.
    """
    if sys.platform != "win32" or not os.environ.get("STEPIK_REQUIRE_SANDBOX_TESTS"):
        pytest.skip("guard enforced only on Windows with STEPIK_REQUIRE_SANDBOX_TESTS")
    from stepik_grader.core.sandbox._windows import create_backend

    assert create_backend() is not None, (
        "Windows Job Objects backend must be constructible — TestWindowsSandboxRunner "
        "would otherwise fail rather than genuinely exercise the sandbox (#558 guard)"
    )


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
        """issue #800: та же проверка локальным listener'ом, что и на Linux.

        Раньше здесь лежала копия внешнего адреса — с тем же изъяном: без
        интернета тест был зелёным независимо от того, работает ли
        `(deny network*)` в профиле sandbox-exec.
        """
        _assert_network_blocked(self._runner(), tmp_path)

    def test_large_stdin_to_non_reader_times_out(self, tmp_path: pathlib.Path) -> None:
        _assert_large_stdin_to_non_reader_times_out(self._runner(), tmp_path)

    def test_cancel_event_stops_run(self, tmp_path: pathlib.Path) -> None:
        _assert_cancel_event_stops_run(self._runner(), tmp_path)

    def test_tle_keeps_partial_output(self, tmp_path: pathlib.Path) -> None:
        _assert_tle_keeps_partial_output(self._runner(), tmp_path)

    def test_grandchildren_killed_on_timeout(self, tmp_path: pathlib.Path) -> None:
        _assert_grandchildren_killed_on_timeout(self._runner(), tmp_path)

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

    def test_file_read_outside_run_dir_not_isolated(self, tmp_path: pathlib.Path) -> None:
        """ПРОБЕЛ (SEC-CORE-03, escape-PoC #648): чтение файлов НЕ изолировано.

        macOS-профиль сознательно несёт ``(allow file-read*)`` без ограничения по
        пути (``_macos.py``: перечисление путей даёт SIGABRT в dyld/CPython ещё до
        кода решения; SECURITY.md для macOS изоляцию ЧТЕНИЯ никогда не обещал —
        только запись/сеть/ресурсы). Характеризующий тест: sandboxed-код читает
        секрет вне run_dir по абсолютному пути и печатает его → эксфильтрация
        через stdout проходит. Красный тест = на macOS появилась изоляция чтения
        → снять оговорку в ``_macos.py``/SECURITY.md и переписать на
        ``_assert_read_outside_run_dir_blocked`` (как Linux). Симметрично
        Windows-части #648 (``test_absolute_path_read_exfiltrates_secret``).
        """
        secret = "S3CRET-macos-exfil-648"
        secret_file = tmp_path / "outside_secret.txt"
        secret_file.write_text(secret, encoding="utf-8")
        path = _write_script(
            tmp_path, f"print(open({str(secret_file)!r}, encoding='utf-8').read())\n"
        )
        outcome = self._runner().run(RunSpec(path=path, stdin=None, timeout=5.0))
        assert outcome.returncode == 0, outcome.stderr.decode("utf-8", errors="replace")
        assert secret in outcome.stdout.decode("utf-8", errors="replace")

    def test_symlink_write_outside_run_dir_blocked(self, tmp_path: pathlib.Path) -> None:
        # issue #648: единственная эмпирически-неподтверждённая точка "symlink
        # избыточен" — проверяем, что Seatbelt применяет write-правило к canonical
        # (резолвленному) пути ссылки, а не к литеральному «внутри run_dir». Иначе
        # symlink обошёл бы (subpath run_dir)-ограничение записи. Ожидание: побег
        # заблокирован (host-target не создан). Красный тест здесь = реальный обход.
        _assert_symlink_write_outside_blocked(self._runner(), tmp_path)


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

    def test_large_stdin_to_non_reader_times_out(self, tmp_path: pathlib.Path) -> None:
        _assert_large_stdin_to_non_reader_times_out(self._runner(), tmp_path)

    def test_cancel_event_stops_run(self, tmp_path: pathlib.Path) -> None:
        _assert_cancel_event_stops_run(self._runner(), tmp_path)

    def test_tle_keeps_partial_output(self, tmp_path: pathlib.Path) -> None:
        _assert_tle_keeps_partial_output(self._runner(), tmp_path)

    def test_grandchildren_killed_on_timeout(self, tmp_path: pathlib.Path) -> None:
        _assert_grandchildren_killed_on_timeout(self._runner(), tmp_path)

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
        # issue #420: golden-сравнение гоняет боевой SandboxRunner() (netns on) —
        # требует не только bwrap, но и рабочего netns (на GHA его нет).
        return shutil.which("bwrap") is not None and _bwrap_netns_works()
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
