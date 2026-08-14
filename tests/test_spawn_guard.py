"""Tests for core/spawn.py — страховка от зависания на ЗАПУСКЕ (issue #1149).

Проверяется то, ради чего модуль и заведён: подвисший ``Popen.__init__`` даёт
ошибку, а не вечное ожидание. Зависание моделируется подменённым ``Popen`` —
воспроизводить настоящее (macOS/Windows + Python 3.14) в наборе нечем, а
поведение обёртки от причины зависания не зависит.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from stepik_grader.core import spawn


class _HangingPopen:
    """Подделка ``Popen``, зависающая в конструкторе — как на errpipe после fork.

    Минимальный интерфейс процесса нужен не сам по себе: без него снятие
    страховки роняло бы тест на ``AttributeError``, а не на том, что прогон
    занял тридцать секунд вместо двух. Причина падения должна совпадать с
    темой теста, иначе следующий читатель пойдёт чинить не то.
    """

    def __init__(self, *_args: object, delay: float = 30.0, **_kwargs: object) -> None:
        time.sleep(delay)
        self.pid = -1
        self.stdin = self.stdout = self.stderr = None
        self.returncode = 0

    def poll(self) -> int:
        return 0

    def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
        return b"", b""

    def wait(self, *_args: object, **_kwargs: object) -> int:
        return 0

    def kill(self) -> None:
        pass


class TestGuardedPopen:
    def test_real_process_is_returned_and_usable(self) -> None:
        """Штатный путь не меняется: процесс возвращается живым и рабочим."""
        proc = spawn.guarded_popen(
            [sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE, text=True
        )

        out, _ = proc.communicate(timeout=30)

        assert proc.returncode == 0
        assert out.strip() == "ok"

    def test_hanging_launch_raises_instead_of_blocking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Главный инвариант: вызывающий поток освобождается, а не висит навсегда.

        Без обёртки этот вызов не вернулся бы вовсе — ровно так pytest-timeout
        и снимал прогон на 120 секундах, объявляя виновным случайный тест.
        """
        monkeypatch.setattr(subprocess, "Popen", _HangingPopen)
        started = time.monotonic()

        with pytest.raises(spawn.SpawnTimeout):
            spawn.guarded_popen([sys.executable, "-c", "pass"], launch_timeout=0.3)

        assert time.monotonic() - started < 5.0

    def test_timeout_message_names_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """В сообщении видно, ЧТО не стартовало, — иначе диагноз опять некуда вести."""
        monkeypatch.setattr(subprocess, "Popen", _HangingPopen)

        with pytest.raises(spawn.SpawnTimeout, match="git"):
            spawn.guarded_popen(["git", "status"], launch_timeout=0.2)

    def test_launch_error_is_reraised_unchanged(self) -> None:
        """Обычная ошибка запуска остаётся собой: вызывающий ловит её как раньше."""
        with pytest.raises(FileNotFoundError):
            spawn.guarded_popen(["/nonexistent/binary-for-tests"])

    def test_late_process_is_killed_not_left_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Родившийся после сдачи процесс убивают, а не бросают без надзора.

        Иначе решение продолжало бы жечь CPU до конца прогона — на сервере это
        утечка, которую никто не заметит.
        """
        killed = threading.Event()

        class _SlowThenBorn:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                time.sleep(0.5)

            def kill(self) -> None:
                killed.set()

        monkeypatch.setattr(subprocess, "Popen", _SlowThenBorn)

        with pytest.raises(spawn.SpawnTimeout):
            spawn.guarded_popen([sys.executable, "-c", "pass"], launch_timeout=0.1)

        assert killed.wait(timeout=5.0), "процесс, родившийся после дедлайна, остался жив"


class TestGuardedRun:
    def test_completed_process_is_returned(self) -> None:
        result = spawn.guarded_run(
            [sys.executable, "-c", "print('hi')"], capture_output=True, text=True
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "hi"

    def test_hanging_launch_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: time.sleep(30))

        with pytest.raises(spawn.SpawnTimeout):
            spawn.guarded_run([sys.executable, "-c", "pass"], launch_timeout=0.3)


class TestRunnerUsesTheGuard:
    """issue #1149: ядро прогона перестало зависеть от того, ответит ли spawn.

    Стек падения на macOS 3.14 шёл `microbench_runner` → `runner.run_spec` →
    `Popen` → `os.read(errpipe_read)`, поэтому проверка стоит именно на
    продуктовом пути, а не только на самой обёртке.
    """

    def test_hanging_spawn_becomes_a_launch_error(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.core import runner

        solution = tmp_path / "task.py"
        solution.write_text("print(input())\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "Popen", _HangingPopen)
        monkeypatch.setattr(spawn, "DEFAULT_LAUNCH_TIMEOUT_S", 0.3)
        started = time.monotonic()

        outcome = runner.LocalRunner().run(
            runner.RunSpec(stdin=b"4\n", timeout=30.0, path=solution)
        )

        assert time.monotonic() - started < 10.0
        assert outcome.launch_error
        assert not outcome.timed_out
