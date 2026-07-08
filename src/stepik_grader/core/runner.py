"""runner.py — Runner Protocol + LocalRunner (issue #136/#137/#138).

Архитектурный слой: Infrastructure.

Явная абстракция запуска кода (`docs/server-mode.md § Runner-слой`, issue
#140): ``grader_core.run_single_test()`` делегирует фактический
subprocess-запуск сюда через ``Runner.run(RunSpec) -> RunOutcome``, не меняя
поведение (issue #138). ``RunOutcome`` несёт сырой итог запуска
(stdout/stderr/returncode/wall time/peak memory/timed_out) — вычисление
verdict/diff остаётся выше по стеку (``grader_core.py``); ``Runner``
вердиктов не выносит (`server-mode.md` § Runner-слой, инвариант 3).

``LocalRunner`` — рефактор текущего subprocess-пути: subprocess.Popen с
принудительным UTF-8 в дочернем окружении, best-effort лимит адресного
пространства (``RLIMIT_AS`` через ``resource.prlimit``, POSIX-only, issue
#67), фоновый psutil-поток мониторинга пикового RSS (issue #48 R-05).
Будущий ``SandboxRunner`` (issue #157 — дизайн, не реализация здесь) будет
тем же протоколом ``Runner`` с иной изоляцией (контейнер/VM), не требуя
изменений в ``grader_core.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import psutil

# resource — POSIX-only (RLIMIT_AS для best-effort memory cap, issue #43 S-01).
# На Windows модуль отсутствует; лимит памяти там не применяется (как и
# SIGALRM-таймаут в executor.py — тот же паттерн graceful degradation).
try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

__all__ = ["RunSpec", "RunOutcome", "Runner", "LocalRunner"]


@dataclass(frozen=True)
class RunSpec:
    """Что запустить (`server-mode.md` § Runner-слой): путь к исполняемому
    файлу, stdin, лимиты. Не зависит от механизма изоляции — одинаков для
    ``LocalRunner`` и будущего ``SandboxRunner``.
    """

    path: str
    stdin: bytes | None
    timeout: float
    measure_memory: bool = True
    max_memory_mb: int | None = None


@dataclass
class RunOutcome:
    """Сырой итог запуска — без вердикта (маппинг в case result выше по стеку,
    см. [`docs/result-contract.md`](../../../docs/result-contract.md)).

    ``launch_error`` заполняется, если процесс не удалось даже запустить
    (``OSError`` при spawn) — тогда ``stdout``/``stderr``/``returncode``
    неопределены (остаются дефолтами).
    """

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0
    elapsed: float = 0.0
    peak_memory_mb: float = 0.0
    timed_out: bool = False
    launch_error: str | None = None


@runtime_checkable
class Runner(Protocol):
    """Протокол исполнения (`server-mode.md` § Runner-слой, issue #137).

    Контракт не зависит от subprocess — реализация вольна выбрать любой
    механизм (``LocalRunner`` — subprocess на этой машине, будущий
    ``SandboxRunner`` — контейнер/VM с сетевой изоляцией).
    """

    def run(self, spec: RunSpec) -> RunOutcome:
        """Запустить ``spec`` и вернуть сырой итог (без вычисления вердикта)."""
        ...


def _apply_memory_limit(pid: int, max_memory_mb: int | None) -> None:
    """Best-effort лимит адресного пространства (RLIMIT_AS) на дочерний pid
    ПОСЛЕ spawn — потокобезопасная замена preexec_fn (issue #67).

    ``preexec_fn`` форкает в многопоточном родителе (грейдер держит
    psutil-поток мониторинга памяти) — документированно небезопасно.
    ``resource.prlimit`` ставит лимит на уже запущенный pid извне, без fork в
    родителе.

    Linux-only: ``resource.prlimit`` отсутствует на macOS (``AttributeError``)
    и на Windows (нет самого модуля ``resource``) — там no-op, решение
    выполняется без лимита памяти, как раньше на Windows. Окно «ребёнок
    стартовал без лимита» ~мс до exec пользовательского кода — приемлемо для
    задач курса (issue #43 S-01 — best-effort, не OS-sandbox; нет изоляции
    ФС/сети).
    """
    if resource is None or max_memory_mb is None:
        return
    limit_bytes = max_memory_mb * 1024 * 1024
    try:
        # typeshed помечает prlimit/RLIMIT_AS Linux-only; на macOS prlimit
        # отсутствует (AttributeError), OSError — нет процесса/прав, ValueError —
        # некорректный лимит. Любую из них глотаем: лишь пропускаем cap.
        resource.prlimit(pid, resource.RLIMIT_AS, (limit_bytes, limit_bytes))  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass


def _measure_peak_memory(
    proc: subprocess.Popen[bytes], result: list[float], stop: threading.Event
) -> None:
    """Поток: замерять RSS дочернего процесса до его завершения.

    Делает первый замер немедленно (до первого sleep), чтобы уловить
    даже очень короткие процессы (< 20 мс). Затем продолжает опрос
    каждые 20 мс до сигнала stop.

    Записывает пик памяти (МБ) в result[0].
    """

    # issue #48 R-05: proc.pid is read after Popen but before communicate() --
    # on a very short-lived child (especially on Windows) the process can exit
    # before psutil.Process(pid)/memory_info() ever samples it. The except
    # branches below already handle that, but previously did so silently,
    # returning peak=0.0 indistinguishable from "the process genuinely used
    # ~0 memory" -- warn so a caller doesn't mistake an unreliable reading for
    # a real measurement.
    def _warn_unreliable() -> None:
        warnings.warn(
            f"peak memory measurement unreliable for pid={proc.pid}: process "
            "exited before it could be sampled (reported peak may be 0.0 or "
            "an undercount)",
            stacklevel=2,
        )

    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        try:
            rss = ps_proc.memory_info().rss / 1024 / 1024
            if rss > peak:
                peak = rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _warn_unreliable()
            result[0] = peak
            return
        while not stop.is_set():
            try:
                rss = ps_proc.memory_info().rss / 1024 / 1024
                if rss > peak:
                    peak = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                _warn_unreliable()
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _warn_unreliable()
    result[0] = peak


class LocalRunner:
    """Subprocess-реализация ``Runner`` (текущее поведение, issue #138).

    Запускает ``sys.executable spec.path``, подаёт ``spec.stdin``, ждёт до
    ``spec.timeout`` секунд; при включённом ``spec.measure_memory`` — фоновый
    поток опроса RSS; при заданном ``spec.max_memory_mb`` — best-effort
    ``RLIMIT_AS`` (POSIX). Дочернему процессу принудительно ставится
    UTF-8 окружение (``PYTHONIOENCODING``/``PYTHONUTF8``), иначе на Windows по
    умолчанию используется cp1251, что ломает кириллицу в выводе.
    """

    def run(self, spec: RunSpec) -> RunOutcome:
        peak_mb_result: list[float] = [0.0]
        stop_event = threading.Event()
        mem_thread: threading.Thread | None = None

        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

        start = time.perf_counter()
        try:
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                [sys.executable, spec.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
            )
            # issue #67: лимит памяти ставим на pid ПОСЛЕ spawn (prlimit), а не
            # через preexec_fn — тот небезопасен при активном psutil-потоке.
            _apply_memory_limit(proc.pid, spec.max_memory_mb)

            if spec.measure_memory:
                mem_thread = threading.Thread(
                    target=_measure_peak_memory,
                    args=(proc, peak_mb_result, stop_event),
                    daemon=True,
                )
                mem_thread.start()

            try:
                stdout_bytes, stderr_bytes = proc.communicate(
                    input=spec.stdin, timeout=spec.timeout
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                stop_event.set()
                return RunOutcome(timed_out=True, elapsed=spec.timeout)
            finally:
                stop_event.set()

            elapsed = time.perf_counter() - start
            if mem_thread is not None:
                mem_thread.join(timeout=0.5)

            return RunOutcome(
                stdout=stdout_bytes,
                stderr=stderr_bytes,
                returncode=proc.returncode,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
                timed_out=False,
            )
        except OSError as exc:
            stop_event.set()
            return RunOutcome(launch_error=str(exc), timed_out=False)
