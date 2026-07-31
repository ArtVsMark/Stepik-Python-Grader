"""_windows.py — SandboxRunner backend для Windows: Job Objects (issue #266).

Механизм: ``subprocess.Popen(..., creationflags=CREATE_SUSPENDED)`` —
Popen поддерживает произвольные ``creationflags``, поэтому не нужен
собственный ``CreateProcessW`` — затем ``AssignProcessToJobObject`` и
возобновление процесса через ``NtResumeProcess`` (ntdll, недокументированное,
но широко используемое API — резюмирует ВСЕ потоки процесса по одному
handle процесса; официального документированного способа возобновить
``CREATE_SUSPENDED``-процесс, создать который приходится через
``subprocess.Popen``, не зная thread handle, не существует — ``Popen`` не
отдаёт ``hThread``, только ``hProcess``). Ограничения ставятся ДО того, как
резюмируется поток — окна гонки нет, ни одна инструкция пользовательского
кода не выполняется до применения Job Object лимитов.

Job Object лимиты (``JOBOBJECT_EXTENDED_LIMIT_INFORMATION``) сочетают
kernel-backstop и (для памяти) собственно основной механизм — эмпирически
подтверждено ручным прогоном на этой машине: ``JOB_OBJECT_LIMIT_JOB_MEMORY``
срабатывает настолько быстро (по commit-charge, до роста RSS), что типичный
"жадный" перебор памяти падает обычным ``MemoryError`` внутри самого
Python-процесса (ненулевой exit code с traceback) РАНЬШЕ, чем наш psutil-поллинг
(``_poll_resources`` ниже) успевает его заметить — т.е. в частом случае это
``RE``, а не ``sandbox_violation="memory"`` (тот же паттерн, что сетевые/ФС/
process-count нарушения, см. докстринг ``RunOutcome.sandbox_violation`` в
``core/runner.py``). psutil-поллинг остаётся backstop'ом для медленно
растущей памяти вне видимости Python-аллокатора (напр. через раздутие RSS
без явного malloc-провала) — тогда ``sandbox_violation="memory"`` всё же
проставляется. Для CPU-времени такого быстрого kernel-сигнала нет — там
поллинг остаётся основным детектором (Job Object'ный
``PerProcessUserTimeLimit`` — лишь backstop).

Два сознательных пробела MVP (см. SECURITY.md — план предполагал больше,
но во время реализации выяснилось, что полная версия непропорционально
сложна для этого MVP):

- **Нет сетевой изоляции.** Правильный примитив — AppContainer
  (``CreateAppContainerProfile`` + ``UpdateProcThreadAttribute`` без
  ``internetClient``), но требует ACL на всё дерево интерпретатора для SID
  контейнера при каждом запуске — негодно для per-run эфемерного профиля.
  Трекается отдельным issue, не блокирует этот MVP.
- **Нет строгой ФС-изоляции.** План предполагал ``CreateRestrictedToken``
  (Low integrity), но применение ДРУГОГО токена к дочернему процессу требует
  ``CreateProcessAsUser`` — что означает отказ от ``subprocess.Popen`` и
  написание своего ``CreateProcessW``+``STARTUPINFO``+pipe-плюмбинга с нуля
  (Popen не поддерживает "запустить с другим токеном"). Риск тихо
  ошибиться в security-critical низкоуровневом коде, который нельзя
  протестировать иначе, чем вручную на этой же машине, признан
  непропорциональным для MVP. Вместо этого — только ``cwd`` контейнмент:
  относительные пути пишутся в per-run tmp dir; абсолютные пути НЕ
  блокируются. Задокументировано как явный пробел, не пропущено молча.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import (
    RunOutcome,
    RunSpec,
    _kill_process_tree,
    _write_stdin,
    sample_tree_rss,
    spec_source_bytes,
)
from stepik_grader.core.sandbox._run_dir import ephemeral_run_dir

__all__ = ["WindowsSandboxRunner", "create_backend"]

_CREATE_SUSPENDED = 0x00000004

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> Any:
    # ctypes.WinDLL как имя типа недоступно в typeshed при проверке mypy не
    # под Windows (CI гоняет mypy на каждой ОС матрицы отдельно) -- Any вместо
    # WinDLL, реальная типизация всё равно динамическая (__getattr__ на DLL).
    return ctypes.windll.kernel32  # type: ignore[attr-defined]


def _ntdll() -> Any:
    return ctypes.windll.ntdll  # type: ignore[attr-defined]


def _create_job_object(max_memory_mb: float, cpu_seconds: int, max_processes: int) -> int:
    """Создать Job Object с лимитами память/CPU/процессы (kernel backstop).

    ``PerProcessUserTimeLimit`` — 100-нс интервалы (``cpu_seconds * 10**7``).
    Поднимает ``OSError`` при сбое любого шага Win32 API.
    """
    kernel32 = _kernel32()
    job: int = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed: " + ctypes.FormatError())  # type: ignore[attr-defined]

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_seconds * 10_000_000)
    info.BasicLimitInformation.ActiveProcessLimit = max_processes
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_TIME
        | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_JOB_MEMORY
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.JobMemoryLimit = int(max_memory_mb * 1024 * 1024)

    ok = kernel32.SetInformationJobObject(
        job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        err = ctypes.FormatError()  # type: ignore[attr-defined]
        kernel32.CloseHandle(job)
        raise OSError(f"SetInformationJobObject failed: {err}")
    return job


def _assign_and_resume(job: int, proc: subprocess.Popen[bytes]) -> None:
    kernel32 = _kernel32()
    handle = proc._handle  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(job, handle):
        raise OSError(
            "AssignProcessToJobObject failed: " + ctypes.FormatError()  # type: ignore[attr-defined]
        )
    # NtResumeProcess — недокументированный, но стабильный/широко используемый
    # способ резюмировать CREATE_SUSPENDED-процесс без thread handle (см.
    # докстринг модуля — Popen не отдаёт hThread, только hProcess).
    status = _ntdll().NtResumeProcess(handle)
    if status != 0:
        raise OSError(f"NtResumeProcess failed: NTSTATUS=0x{status:08X}")


def _poll_resources(
    proc: subprocess.Popen[bytes],
    max_memory_mb: float,
    cpu_seconds: float,
    mem_exceeded: threading.Event,
    cpu_exceeded: threading.Event,
    stop: threading.Event,
    peak_result: list[float],
) -> None:
    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        while not stop.is_set():
            try:
                # issue #556: суммируем RSS всего дерева (решение может породить
                # процессы под тем же Job Object), а не только прямого ребёнка.
                rss = sample_tree_rss(ps_proc)
                if rss > peak:
                    peak = rss
                if rss > max_memory_mb:
                    mem_exceeded.set()
                    proc.kill()
                    break
                cpu_times = ps_proc.cpu_times()
                if (cpu_times.user + cpu_times.system) > cpu_seconds:
                    cpu_exceeded.set()
                    proc.kill()
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    peak_result[0] = peak


class WindowsSandboxRunner:
    """``Runner`` изолирующий выполнение через Windows Job Objects.

    См. докстринг модуля для двух явных пробелов MVP (сеть, строгая ФС-
    изоляция) — память/CPU/число процессов реально изолированы ядром.
    """

    supports_project_imports = False  # issue #550: site-packages проекта не в песочнице

    def run(self, spec: RunSpec) -> RunOutcome:
        with ephemeral_run_dir() as run_dir:
            script_path = run_dir / "solution.py"
            try:
                script_path.write_bytes(spec_source_bytes(spec))
            except OSError as exc:
                return RunOutcome(launch_error=str(exc))

            interpreter_dir = str(Path(sys.executable).resolve().parent)
            child_env = {
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "PATH": interpreter_dir,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                # issue #726: без ANSI-раскраски traceback'а (Python 3.13+),
                # как в LocalRunner и POSIX-backend'ах.
                "PYTHON_COLORS": "0",
            }
            max_memory_mb = float(spec.max_memory_mb or CONFIG.max_memory_mb or 1024)
            cpu_seconds = max(1, math.ceil(CONFIG.sandbox_max_cpu_seconds))

            start = time.perf_counter()
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(script_path)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(run_dir),
                    env=child_env,
                    creationflags=_CREATE_SUSPENDED,
                )
            except OSError as exc:
                return RunOutcome(launch_error=str(exc))

            job = None
            try:
                job = _create_job_object(max_memory_mb, cpu_seconds, CONFIG.sandbox_max_processes)
                _assign_and_resume(job, proc)
            except OSError as exc:
                proc.kill()
                proc.wait()
                return RunOutcome(launch_error=str(exc))

            try:
                return self._drain_and_wait(proc, spec, start, max_memory_mb, cpu_seconds, job=job)
            finally:
                _kernel32().CloseHandle(job)

    def _drain_and_wait(
        self,
        proc: subprocess.Popen[bytes],
        spec: RunSpec,
        start: float,
        max_memory_mb: float,
        cpu_seconds: float,
        *,
        job: int | None = None,
    ) -> RunOutcome:
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        total_bytes = 0
        size_lock = threading.Lock()
        output_exceeded = threading.Event()
        max_output_bytes = CONFIG.sandbox_max_output_bytes

        def _on_chunk(n: int) -> None:
            nonlocal total_bytes
            with size_lock:
                total_bytes += n
                if total_bytes > max_output_bytes:
                    output_exceeded.set()
                    proc.kill()

        def _drain(pipe: Any, sink: list[bytes]) -> None:
            try:
                for chunk in iter(lambda: pipe.read(65536), b""):
                    sink.append(chunk)
                    _on_chunk(len(chunk))
            except (OSError, ValueError):
                pass

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True),
        ]
        for r in readers:
            r.start()

        mem_stop = threading.Event()
        mem_exceeded = threading.Event()
        cpu_exceeded = threading.Event()
        peak_mb_result: list[float] = [0.0]
        poll_thread = threading.Thread(
            target=_poll_resources,
            args=(
                proc,
                max_memory_mb,
                cpu_seconds,
                mem_exceeded,
                cpu_exceeded,
                mem_stop,
                peak_mb_result,
            ),
            daemon=True,
        )
        poll_thread.start()

        # issue #796: та же правка, что в POSIX-backend'е и в LocalRunner
        # (issue #419) — запись stdin в daemon-потоке. Синхронный write
        # блокировал главный поток до входа в цикл ожидания, если решение не
        # читает ввод, а stdin больше буфера pipe: ни таймаут, ни проверки
        # нарушений не выполнялись, прогон висел до конца ребёнка.
        stdin_writer: threading.Thread | None = None
        if proc.stdin is not None:
            stdin_writer = threading.Thread(
                target=_write_stdin,
                args=(proc.stdin, spec.stdin),
                name="sandbox-stdin",
                daemon=True,
            )
            stdin_writer.start()

        timed_out = False
        cancelled = False
        while True:
            if proc.poll() is not None:
                break
            if output_exceeded.is_set() or mem_exceeded.is_set() or cpu_exceeded.is_set():
                break
            # issue #797: та же проверка, что в POSIX-backend'е и LocalRunner.
            if spec.cancel_event is not None and spec.cancel_event.is_set():
                cancelled = True
                proc.kill()
                break
            if time.perf_counter() - start > spec.timeout:
                timed_out = True
                proc.kill()
                break
            time.sleep(0.02)

        # issue #798: при аварийном обрыве убиваем ВЕСЬ Job Object, а не только
        # процесс решения. `proc.kill()` уносит одного потомка, внуки же жили до
        # закрытия хендла job в `run()` — то есть ещё секунду-другую после TLE,
        # чего им хватало, чтобы дописать файл (проверено живым прогоном: маркер,
        # создаваемый внуком через 3 с, оказывался на диске).
        #
        # Именно TerminateJobObject, а не psutil-обход дерева: тот собирает
        # детей ЧЕРЕЗ родителя, а родитель к этому моменту уже мёртв — список
        # выходит пустым. Job знает всех своих процессов независимо от того,
        # жив ли первый из них.
        if timed_out or cancelled or output_exceeded.is_set() or mem_exceeded.is_set():
            if job is not None:
                with contextlib.suppress(OSError):
                    _kernel32().TerminateJobObject(job, 1)
            _kill_process_tree(proc)

        proc.wait()
        mem_stop.set()
        for r in readers:
            r.join(timeout=1.0)
        poll_thread.join(timeout=0.5)
        # issue #796: ждём писателя ограниченно — при нечитающем решении он
        # стоит в write до BrokenPipeError после смерти процесса.
        if stdin_writer is not None:
            stdin_writer.join(timeout=0.5)

        elapsed = time.perf_counter() - start
        # issue #798: частичный вывод прикладывается ко ВСЕМ аварийным исходам.
        # Reader-потоки уже слили в память то, что решение успело напечатать до
        # обрыва, — выбрасывать это значит оставлять студента без диагноза:
        # «превышен лимит» без единой строки вывода не подсказывает, где цикл
        # ушёл в разнос. POSIX-backend так делает с #556/#421, Windows отставал.
        partial_stdout = b"".join(stdout_chunks)
        partial_stderr = b"".join(stderr_chunks)
        if output_exceeded.is_set():
            return RunOutcome(
                sandbox_violation="output_size",
                stdout=partial_stdout,
                stderr=partial_stderr,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        if mem_exceeded.is_set():
            return RunOutcome(
                sandbox_violation="memory",
                stdout=partial_stdout,
                stderr=partial_stderr,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        if cpu_exceeded.is_set():
            return RunOutcome(
                sandbox_violation="cpu",
                stdout=partial_stdout,
                stderr=partial_stderr,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        # issue #797: отмена — свой исход, а не TLE: UI различает «слишком
        # медленное решение» и «пользователь нажал Отмена» (issue #262).
        if cancelled:
            return RunOutcome(
                stdout=partial_stdout,
                stderr=partial_stderr,
                cancelled=True,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        if timed_out:
            return RunOutcome(
                stdout=partial_stdout,
                stderr=partial_stderr,
                timed_out=True,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )

        return RunOutcome(
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            returncode=proc.returncode or 0,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
        )


def create_backend() -> WindowsSandboxRunner:
    """Проверить, что Job Object API доступен (создать и сразу закрыть
    пробный Job Object) — поднимает ``SandboxUnavailableError`` при сбое,
    никогда не тихий fallback на ``LocalRunner``."""
    from stepik_grader.core.sandbox import SandboxUnavailableError

    try:
        job = _create_job_object(max_memory_mb=1024.0, cpu_seconds=10, max_processes=32)
        _kernel32().CloseHandle(job)
    except OSError as exc:
        raise SandboxUnavailableError(f"Windows Job Object API недоступен: {exc}") from exc
    return WindowsSandboxRunner()
