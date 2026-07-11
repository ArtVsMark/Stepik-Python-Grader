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
``SandboxRunner`` (issue #266, реализация требований дизайна #157) — в
``core/sandbox/``: тот же протокол ``Runner`` с ОС-уровневой изоляцией
(bubblewrap/nsjail на Linux, sandbox-exec на macOS, Job Objects на Windows),
без изменений в логике ``grader_core.py`` (только новый ``set_runner()``
для инъекции и маппинг ``sandbox_violation`` в отдельный verdict).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

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

    ``cancel_event`` (issue #262) — опциональный сигнал best-effort отмены
    для async job-модели (``web/runs.py``). ``None`` (по умолчанию) сохраняет
    прежнее поведение ``LocalRunner.run()`` один в один (единственный
    блокирующий ``proc.communicate(timeout=...)``, без poll-накладных
    расходов) — CLI и синхронный ``/api/grade`` его не передают.
    """

    path: str
    stdin: bytes | None
    timeout: float
    measure_memory: bool = True
    max_memory_mb: int | None = None
    cancel_event: threading.Event | None = None


@dataclass
class RunOutcome:
    """Сырой итог запуска — без вердикта (маппинг в case result выше по стеку,
    см. [`docs/result-contract.md`](../../../docs/result-contract.md)).

    ``launch_error`` заполняется, если процесс не удалось даже запустить
    (``OSError`` при spawn) — тогда ``stdout``/``stderr``/``returncode``
    неопределены (остаются дефолтами).

    ``cancelled`` (issue #262) — процесс убит из-за ``RunSpec.cancel_event``,
    а не из-за истечения ``timeout`` (``timed_out`` в этом случае остаётся
    ``False`` — маппится в отдельный verdict ``CANCELLED``, не ``TLE``, выше
    по стеку в ``grader_core.run_single_test()``).

    ``sandbox_violation`` (issue #266) — заполняется реализациями ``Runner``,
    изолирующими выполнение на уровне ОС (``core/sandbox/``), когда САМ
    Runner проактивно распознал и оборвал превышение квоты: ``"memory"``
    (RSS перешёл порог — psutil-поллинг, общий для всех 3 backend'ов),
    ``"output_size"`` (накопленный stdout+stderr превысил лимит) или
    ``"cpu"`` (``SIGXCPU`` от ``RLIMIT_CPU``, POSIX). Нарушения сети/ФС/
    лимита процессов **не** попадают сюда — ядро отклоняет их ВНУТРИ
    песочницы, ребёнок падает с обычным ненулевым exit code/traceback,
    и это корректно классифицируется как обычный ``RE`` (см.
    `docs/server-mode.md § Классы ошибок <../../../docs/server-mode.md>`_) —
    Runner не заглядывает внутрь чужого traceback, чтобы отличить их.
    ``LocalRunner`` никогда его не выставляет (остаётся ``None``). Маппится в
    отдельный verdict ``SANDBOX_VIOLATION`` (аддитивно к AC/WA/RE/TLE/
    CANCELLED), не ``RE``/``TLE``, чтобы UI не путал нарушение,
    которое сам Runner детектировал и оборвал, с обычным провалом решения.
    """

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0
    elapsed: float = 0.0
    peak_memory_mb: float = 0.0
    timed_out: bool = False
    launch_error: str | None = None
    cancelled: bool = False
    sandbox_violation: str | None = None


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

            if spec.cancel_event is None:
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

            try:
                outcome = self._run_with_polling(proc, spec, start, peak_mb_result)
            finally:
                stop_event.set()
            if mem_thread is not None:
                mem_thread.join(timeout=0.5)
            return outcome
        except OSError as exc:
            stop_event.set()
            return RunOutcome(launch_error=str(exc), timed_out=False)

    def _run_with_polling(
        self,
        proc: subprocess.Popen[bytes],
        spec: RunSpec,
        start: float,
        peak_mb_result: list[float],
    ) -> RunOutcome:
        """Poll-версия ``proc.communicate()`` — прерывается по
        ``spec.cancel_event``, не только по ``spec.timeout`` (issue #262).

        Дренирует stdout/stderr в фоновых потоках всё время ожидания — как
        это делает сам ``communicate()`` внутри себя. Без этого дочерний
        процесс, пишущий много в stdout, застрял бы на заполненном OS
        pipe-буфере, пока мы просто опрашиваем ``proc.poll()`` каждые ~100мс.
        stdin пишется и закрывается до входа в цикл опроса (тот же порядок,
        что ``communicate()``), а не построчно синхронно с чтением — иначе
        возможен классический deadlock subprocess (записываем stdin, пока
        никто не читает переполненный stdout).
        """
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        def _drain(pipe: Any, sink: list[bytes]) -> None:
            try:
                for chunk in iter(lambda: pipe.read(65536), b""):
                    sink.append(chunk)
            except (OSError, ValueError):
                pass

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True),
        ]
        for reader in readers:
            reader.start()

        if proc.stdin is not None:
            if spec.stdin is not None:
                try:
                    proc.stdin.write(spec.stdin)
                except (BrokenPipeError, OSError):
                    pass
            try:
                proc.stdin.close()
            except OSError:
                pass

        assert spec.cancel_event is not None
        cancelled = False
        timed_out = False
        while True:
            if proc.poll() is not None:
                break
            remaining = spec.timeout - (time.perf_counter() - start)
            if remaining <= 0:
                timed_out = True
                break
            if spec.cancel_event.wait(min(0.1, remaining)):
                cancelled = True
                break

        if cancelled or timed_out:
            proc.kill()
            proc.wait()

        for reader in readers:
            reader.join(timeout=1.0)

        elapsed = time.perf_counter() - start
        if timed_out:
            return RunOutcome(timed_out=True, elapsed=spec.timeout)
        if cancelled:
            return RunOutcome(cancelled=True, elapsed=elapsed)
        return RunOutcome(
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            returncode=proc.returncode,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
            timed_out=False,
        )
