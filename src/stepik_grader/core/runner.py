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
(bubblewrap на Linux, sandbox-exec на macOS, Job Objects на Windows), без
изменений в логике ``grader_core.py`` (только новый ``set_runner()`` для
инъекции и маппинг ``sandbox_violation`` в отдельный verdict).
"""

from __future__ import annotations

import os
import pathlib
import signal
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

# issue #418: после kill дерева процессов даём ограниченное время на reap —
# внук, унаследовавший pipe, может держать его открытым, и тогда
# communicate()/wait() без предела виснет (репро: 8.1 с при timeout=2.0).
_KILL_REAP_TIMEOUT = 5.0


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

    path: pathlib.Path
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
    #
    # issue: the message used to interpolate f"pid={proc.pid}", which made
    # every occurrence a distinct string -- Python's default warning filter
    # dedupes on the exact rendered message text, so a batch grading run full
    # of trivially-fast solutions (print(1), etc. -- common Stepik exercises)
    # printed one UserWarning PER test case instead of once. Keeping the
    # message text constant lets the stdlib's own "default" filter show it
    # once per interpreter session and silently drop the rest, with no extra
    # state to maintain here.
    def _warn_unreliable() -> None:
        warnings.warn(
            "peak memory measurement unreliable for a fast-exiting process: it "
            "exited before it could be sampled (reported peak may be 0.0 or an "
            "undercount, common for trivially fast solutions). Shown once per "
            "process by Python's default warning filter.",
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


def _write_stdin(pipe: Any, data: bytes | None) -> None:
    """Записать stdin и закрыть pipe — в отдельном потоке (issue #419).

    Ребёнок, не читающий ввод, при ``stdin`` больше pipe-буфера заблокировал бы
    синхронный ``write`` в главном потоке до входа в poll-цикл — тогда ни
    ``spec.timeout``, ни ``spec.cancel_event`` не сработали бы. Вынос в
    daemon-поток разрывает этот deadlock: поток просто зависнет на ``write`` и
    умрёт вместе с процессом (``BrokenPipeError`` после kill дерева).
    """
    try:
        if data is not None:
            pipe.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Убить всё дерево процессов решения, а не только прямого ребёнка (issue #418).

    Решение может породить внуков (multiprocessing/subprocess); внук,
    унаследовавший stdout/stderr, держит pipe открытым и вешает
    ``communicate()``/``wait()`` без таймаута, а осиротевший внук продолжает
    жечь CPU после TLE/cancel. Бьём по группе процессов на POSIX (``os.killpg``
    — работает благодаря ``start_new_session=True`` при spawn) и добиваем дерево
    через psutil (кросс-ОС, единственный путь на Windows). Best-effort:
    полностью демонизированный (double-fork + setsid) внук может уйти — это
    осознанный предел без OS-sandbox.
    """
    # Собираем детей ДО убийства родителя, пока связь parent->child ещё видна.
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        children = []

    if os.name == "posix":
        try:
            # os.killpg/getpgid + signal.SIGKILL — POSIX-only, отсутствуют в
            # typeshed под Windows; ветка защищена os.name, но CI гоняет mypy на
            # каждой ОС матрицы (та же причина, что SIGXCPU в _posix_common.py).
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
        except (ProcessLookupError, PermissionError, OSError):
            pass

    try:
        proc.kill()
    except OSError:
        pass

    for child in children:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _reap_after_kill(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    """``communicate()`` после kill, ограниченный по времени (issue #418/#421).

    Возвращает частичный ``(stdout, stderr)``, накопленный к моменту TLE, и не
    блокируется дольше ``_KILL_REAP_TIMEOUT`` даже если внук держит pipe.
    """
    try:
        out, err = proc.communicate(timeout=_KILL_REAP_TIMEOUT)
        return out or b"", err or b""
    except subprocess.TimeoutExpired:
        return b"", b""
    except (OSError, ValueError):
        return b"", b""


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
        popen_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            # issue #418: своя сессия/группа процессов, чтобы при TLE/cancel
            # убить всё дерево решения (os.killpg), а не только прямого ребёнка.
            popen_kwargs["start_new_session"] = True
        try:
            proc: subprocess.Popen[bytes] = subprocess.Popen(
                [sys.executable, spec.path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                **popen_kwargs,
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
                    # issue #418: убить всё дерево (внуки держат pipe → иначе
                    # communicate() виснет); issue #421: вернуть частичный вывод,
                    # накопленный до TLE, с ограниченным reap.
                    _kill_process_tree(proc)
                    partial_out, partial_err = _reap_after_kill(proc)
                    return RunOutcome(
                        stdout=partial_out,
                        stderr=partial_err,
                        timed_out=True,
                        elapsed=spec.timeout,
                    )
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
            # issue #419: запись stdin — в отдельном потоке, а не синхронно в
            # главном (иначе не-читающий ребёнок при большом stdin повесил бы
            # write до входа в poll-цикл, и timeout/cancel не сработали бы).
            writer = threading.Thread(
                target=_write_stdin, args=(proc.stdin, spec.stdin), daemon=True
            )
            writer.start()

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
            # issue #418: убить всё дерево, reap ограничен по времени.
            _kill_process_tree(proc)
            try:
                proc.wait(timeout=_KILL_REAP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass

        for reader in readers:
            reader.join(timeout=1.0)

        elapsed = time.perf_counter() - start
        # issue #421: reader'ы уже слили частичный вывод в память — вернуть его
        # и при TLE/cancel, а не выбрасывать.
        stdout = b"".join(stdout_chunks)
        stderr = b"".join(stderr_chunks)
        if timed_out:
            return RunOutcome(
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                elapsed=spec.timeout,
                peak_memory_mb=peak_mb_result[0],
            )
        if cancelled:
            return RunOutcome(
                stdout=stdout,
                stderr=stderr,
                cancelled=True,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        return RunOutcome(
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
            timed_out=False,
        )
