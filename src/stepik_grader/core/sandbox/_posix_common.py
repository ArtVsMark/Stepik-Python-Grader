"""_posix_common.py — общий Popen+poll+drain цикл для Linux/macOS backend'ов
``SandboxRunner`` (issue #266).

Изоляция (bwrap/sandbox-exec) уже "снаружи" переданного ``argv`` — этот
модуль просто исполняет готовую команду и отвечает за то, что сам Runner
обязан детектировать активно (не полагаясь на ядро/сигналы одного лишь
``RLIMIT_AS``, см. ``_posix_bootstrap.py``): суммарный размер вывода и
(на обеих ОС одинаково) память через psutil-поллинг — тот же паттерн, что
``core/runner.py._measure_peak_memory``, но с принудительным ``proc.kill()``
при превышении, а не просто измерением. ``RLIMIT_CPU`` детектируется по
сигналу ``SIGXCPU`` — единственная POSIX-квота, для которой сигнал
однозначно атрибутируем (в отличие от ``RLIMIT_AS``/``RLIMIT_NPROC``,
которые внутри упавшего ребёнка обычно всплывают как обычный
``MemoryError``/``OSError`` traceback с ненулевым exit code, т.е. корректно
классифицируются выше по стеку как обычный ``RE`` — см. докстринг
``RunOutcome.sandbox_violation`` в ``core/runner.py``).
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from typing import Any

import psutil

from stepik_grader.core.runner import _KILL_REAP_TIMEOUT, RunOutcome, _kill_process_tree

__all__ = ["run_argv_with_limits"]


def _drain(pipe: Any, sink: list[bytes], on_chunk: Any) -> None:
    try:
        for chunk in iter(lambda: pipe.read(65536), b""):
            sink.append(chunk)
            on_chunk(len(chunk))
    except (OSError, ValueError):
        pass


def _poll_memory(
    proc: subprocess.Popen[bytes],
    max_memory_mb: float,
    exceeded: threading.Event,
    stop: threading.Event,
    peak_result: list[float],
) -> None:
    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        while not stop.is_set():
            try:
                rss = ps_proc.memory_info().rss / 1024 / 1024
                if rss > peak:
                    peak = rss
                if rss > max_memory_mb:
                    exceeded.set()
                    proc.kill()
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    peak_result[0] = peak


def run_argv_with_limits(
    argv: list[str],
    *,
    stdin: bytes | None,
    timeout: float,
    max_output_bytes: int,
    max_memory_mb: float | None = None,
    env: dict[str, str] | None = None,
) -> RunOutcome:
    """Запустить ``argv``, дренируя stdout/stderr с лимитом суммарного
    размера, опциональным psutil-поллингом памяти и wall-clock таймаутом.

    Приоритет при нескольких одновременных причин остановки: output_size >
    memory > timeout — порядок проверки в цикле ожидания ниже, не важен для
    корректности (после ``proc.kill()`` от любой причины дальнейшие проверки
    того же тика уже не имеют значения), важен только для детерминированного
    выбора *одной* причины в отчёте.
    """
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            # issue #418: своя сессия/группа — чтобы при TLE/лимите убить всё
            # дерево (os.killpg), а не только прямой процесс изоляции.
            start_new_session=True,
        )
    except OSError as exc:
        return RunOutcome(launch_error=str(exc))

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total_bytes = 0
    size_lock = threading.Lock()
    output_exceeded = threading.Event()

    def _on_chunk(n: int) -> None:
        nonlocal total_bytes
        with size_lock:
            total_bytes += n
            if total_bytes > max_output_bytes:
                output_exceeded.set()
                proc.kill()

    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks, _on_chunk), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks, _on_chunk), daemon=True),
    ]
    for r in readers:
        r.start()

    mem_stop = threading.Event()
    mem_exceeded = threading.Event()
    peak_mb_result: list[float] = [0.0]
    mem_thread: threading.Thread | None = None
    if max_memory_mb is not None:
        mem_thread = threading.Thread(
            target=_poll_memory,
            args=(proc, max_memory_mb, mem_exceeded, mem_stop, peak_mb_result),
            daemon=True,
        )
        mem_thread.start()

    if proc.stdin is not None:
        if stdin is not None:
            try:
                proc.stdin.write(stdin)
            except (BrokenPipeError, OSError):
                pass
        try:
            proc.stdin.close()
        except OSError:
            pass

    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if output_exceeded.is_set() or mem_exceeded.is_set():
            break
        if time.perf_counter() - start > timeout:
            timed_out = True
            proc.kill()
            break
        time.sleep(0.02)

    # issue #418: reap ограничен по времени; если процесс ещё жив (внук держит
    # pipe), добить всё дерево и подождать ограниченно, а не висеть в wait().
    try:
        proc.wait(timeout=_KILL_REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=_KILL_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
    mem_stop.set()
    for r in readers:
        r.join(timeout=1.0)
    if mem_thread is not None:
        mem_thread.join(timeout=0.5)

    elapsed = time.perf_counter() - start

    if output_exceeded.is_set():
        return RunOutcome(
            sandbox_violation="output_size", elapsed=elapsed, peak_memory_mb=peak_mb_result[0]
        )
    if mem_exceeded.is_set():
        return RunOutcome(
            sandbox_violation="memory", elapsed=elapsed, peak_memory_mb=peak_mb_result[0]
        )
    if timed_out:
        # issue #421: вернуть частичный вывод, накопленный reader'ами до TLE.
        return RunOutcome(
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            timed_out=True,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
        )

    # signal.SIGXCPU отсутствует в typeshed под Windows -- модуль реально
    # импортируется только backend'ами _linux.py/_macos.py, но CI гоняет mypy
    # на каждой ОС матрицы отдельно и без ignore здесь падал бы на
    # windows-latest.
    returncode = proc.returncode
    if returncode is not None and returncode < 0 and -returncode == signal.SIGXCPU:  # type: ignore[attr-defined]
        return RunOutcome(
            sandbox_violation="cpu",
            elapsed=elapsed,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            returncode=returncode,
            peak_memory_mb=peak_mb_result[0],
        )

    return RunOutcome(
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        returncode=returncode or 0,
        elapsed=elapsed,
        peak_memory_mb=peak_mb_result[0],
    )
