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

import contextlib
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from stepik_grader.core.runner import (
    _KILL_REAP_TIMEOUT,
    RunOutcome,
    _kill_process_tree,
    _write_stdin,
    sample_tree_rss,
)

__all__ = ["run_argv_with_limits"]


def _drain(pipe: Any, sink: list[bytes], on_chunk: Any) -> None:
    """Слить трубу в ``sink``, отдавая байты по мере прихода (issue #1143).

    ``read1``, а не ``read``: последний ждёт ЛИБО полные 65536 байт, ЛИБО EOF, и
    отдаёт накопленное только тогда. Решение, оставившее живого внука с открытым
    stdout, EOF не даёт — «7» лежит в буфере, ``proc.wait()`` уже вернулся по
    основному процессу, ``join(timeout=1.0)`` истекает, поток бросают, и sink
    остаётся ПУСТЫМ. Верное решение получает ``WA`` с пустым ``Actual``.

    То же исправление, что в ``LocalRunner`` (issue #952, RUN-4-01): там его
    сделали, а в backend'ы песочницы не перенесли — и контракт паритета
    ``--sandbox`` (issue #986) держался лишь до первого такого решения.
    """
    try:
        for chunk in iter(lambda: pipe.read1(65536), b""):
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
    """Поллинг RSS дерева процесса изоляции и принудительный kill при
    превышении ``max_memory_mb`` (issue #556).

    ``proc.pid`` здесь — процесс изоляции (bwrap/sandbox-exec), а решение
    исполняется его потомком; замеряем всё поддерево через общий
    ``sample_tree_rss`` (тот же helper, что и ``LocalRunner``), иначе память
    решения-внука не видна и детектор не срабатывает. Это и есть активное
    enforcement памяти в песочнице — на Linux поверх него ещё стоит kernel-
    backstop ``RLIMIT_AS`` (см. ``_linux.py``); на macOS поллинг —
    единственная линия обороны.
    """
    # issue #996 (JRN-1-01): пик публикуется СРАЗУ, а не одной строкой на
    # выходе из функции. Выход наступает после `stop`, а `join` вызывающей
    # стороны ограничен по времени: не успел поток — замер терялся целиком.
    # В `LocalRunner` тот же порядок делал пик нулевым ВСЕГДА (см. runner.py).
    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        while not stop.is_set():
            try:
                rss = sample_tree_rss(ps_proc)
                if rss > peak:
                    peak = rss
                    peak_result[0] = peak
                if rss > max_memory_mb:
                    exceeded.set()
                    proc.kill()
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def build_minimal_env() -> dict[str, str]:
    """Минимальное окружение дочернего процесса — без секретов родителя.

    Изолированному коду не нужен ``os.environ`` грейдера: там живут BYOK
    AI-ключ (``CONFIG.ai_api_key_env``), а в server-mode — весь env оператора.
    Наследование этого окружения обходит единственный сетевой контроль
    macOS-профиля: вывести ``os.environ`` можно прямо в stdout, который
    грейдер возвращает в ответе (issue #627).

    Соответствует тому, что Linux-backend задаёт через ``bwrap --clearenv
    --setenv`` (``_linux.py``), а Windows-backend — явным dict (``_windows.py``).
    Передавать результат в ``run_argv_with_limits(env=...)`` обязательно:
    ``env=None`` означает наследование окружения родителя.
    """
    return {
        "PATH": str(Path(sys.executable).resolve().parent),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        # issue #726: тот же детерминированный stderr, что у LocalRunner —
        # без ANSI-раскраски traceback'а (Python 3.13+).
        "PYTHON_COLORS": "0",
    }


def _killed_by(returncode: int, sig: int) -> bool:
    """Убит ли процесс сигналом ``sig`` (issue #986).

    Две формы одного и того же: прямой потомок даёт отрицательный код
    (``-SIGXCPU``), а процесс, запущенный через посредника вроде ``bwrap``, —
    обычный ``128 + N``. Жёсткий ``RLIMIT_CPU`` шлёт ``SIGXCPU`` и следом
    добивает ``SIGKILL``, поэтому оба варианта означают одно: квота исчерпана.
    """
    if returncode < 0:
        return -returncode in {sig, signal.SIGKILL}
    return returncode - 128 in {sig, signal.SIGKILL} if returncode > 128 else False


def run_argv_with_limits(
    argv: list[str],
    *,
    stdin: bytes | None,
    timeout: float,
    max_output_bytes: int,
    max_memory_mb: float | None = None,
    env: dict[str, str] | None = None,
    cancel_event: threading.Event | None = None,
    cwd: Path | None = None,
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
            # issue #799 (SECC-06): рабочий каталог решения — run_dir, а не
            # каталог, из которого запущен грейдер. Linux задаёт его через
            # `bwrap --chdir`, а macOS-backend не задавал вовсе: относительный
            # путь в решении открывал файлы рядом с решениями пользователя.
            cwd=cwd,
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

    # issue #796: запись stdin — в daemon-потоке, а НЕ синхронно перед циклом
    # ожидания. Решение, не читающее ввод, при stdin больше pipe-буфера (~64 KiB
    # на Linux) заблокировало бы главный поток до входа в while ниже — и ни
    # timeout, ни проверка нарушений не выполнились бы ни разу: прогон висел бы
    # до самостоятельного завершения ребёнка, то есть навсегда. Ровно этот
    # deadlock уже был закрыт в LocalRunner (issue #419), но фикс не перенесли
    # в sandbox-backend'ы. Переиспользуем ту же функцию, а не копию: копия и
    # разъехалась бы снова.
    stdin_writer: threading.Thread | None = None
    if proc.stdin is not None:
        stdin_writer = threading.Thread(
            target=_write_stdin,
            args=(proc.stdin, stdin),
            name="sandbox-stdin",
            daemon=True,
        )
        stdin_writer.start()

    timed_out = False
    cancelled = False
    try:
        while True:
            if proc.poll() is not None:
                break
            if output_exceeded.is_set() or mem_exceeded.is_set():
                break
            # issue #797: отмена проверяется в том же цикле, что и таймаут. Без
            # неё `RunSpec.cancel_event` не читался ни одним backend'ом: под
            # `--serve --sandbox` кнопка «Отмена» ничего не делала, а вердикт
            # CANCELLED был недостижим — воркер держался до конца прогона.
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                proc.kill()
                break
            if time.perf_counter() - start > timeout:
                timed_out = True
                proc.kill()
                break
            time.sleep(0.02)
    finally:
        # issue #798: гарантированная уборка — перенос исправления #624 из
        # LocalRunner. KeyboardInterrupt или неожиданное исключение уходили
        # наружу, оставляя живым процесс недоверенного решения; на сервере это
        # прямая утечка. На штатных путях процесс уже мёртв (poll() != None),
        # поэтому убийство не срабатывает и поведение не меняется.
        mem_stop.set()
        if proc.poll() is None:
            _kill_process_tree(proc)

    # issue #798: при любом принудительном обрыве бьём по ВСЕЙ группе, а не
    # только по прямому потомку. `proc.kill()` выше убивает процесс изоляции;
    # на Linux этого достаточно (bwrap уносит PID-namespace целиком), но на
    # macOS `sandbox-exec` такого не делает — форкнутые внуки продолжали жить,
    # а прежняя ветка `_kill_process_tree` срабатывала ТОЛЬКО если reap упёрся
    # в таймаут. Ребёнок, умерший сразу, оставлял внуков навсегда.
    if timed_out or cancelled or output_exceeded.is_set() or mem_exceeded.is_set():
        _kill_process_tree(proc)

    # issue #418: reap ограничен по времени; если процесс ещё жив (внук держит
    # pipe), добить всё дерево и подождать ограниченно, а не висеть в wait().
    try:
        proc.wait(timeout=_KILL_REAP_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_KILL_REAP_TIMEOUT)
    mem_stop.set()
    for r in readers:
        r.join(timeout=1.0)
    if mem_thread is not None:
        mem_thread.join(timeout=0.5)
    # issue #796: писатель stdin ждём ограниченно и не блокируемся на нём. Если
    # ребёнок так и не прочитал ввод, поток стоит в write и разблокируется лишь
    # BrokenPipeError после смерти процесса — daemon-поток умрёт с интерпретатором.
    if stdin_writer is not None:
        stdin_writer.join(timeout=0.5)

    elapsed = time.perf_counter() - start

    # issue #556: приложить частичный stdout/stderr, накопленный reader'ами до
    # обрыва по нарушению — как это уже делает ветка TLE ниже. Без этого студент
    # видел бы пустой вывод у решения, которое что-то напечатало перед тем, как
    # упереться в лимит вывода/памяти.
    if output_exceeded.is_set():
        return RunOutcome(
            sandbox_violation="output_size",
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
        )
    if mem_exceeded.is_set():
        return RunOutcome(
            sandbox_violation="memory",
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
        )
    # issue #797: отмена — отдельный исход, не TLE. Вердикт различает «решение
    # слишком медленное» и «пользователь нажал Отмена» (issue #262), и путать
    # их в UI нельзя. Проверяется ПЕРЕД timed_out: если оба флага успели
    # выставиться, причина остановки — та, что сработала в цикле первой.
    if cancelled:
        return RunOutcome(
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            cancelled=True,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
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
    # issue #986 (SBX-3-01): сигнал ищется и в положительном коде. Прямой потомок
    # отдаёт убийство сигналом как отрицательный код, но под изоляцией мы ждём
    # bwrap — он переживает своего ребёнка и транслирует его гибель обычным
    # кодом `128 + N` (SIGXCPU → 152, добитый следом SIGKILL → 137). Проверка
    # смотрела только на отрицательные, поэтому исчерпание CPU-квоты не
    # распознавалось ВООБЩЕ: ни таймаута, ни нарушения — «ненулевой код», то
    # есть RE, «решение упало». Падала квота, а отвечало решение.
    if returncode is not None and _killed_by(returncode, signal.SIGXCPU):  # type: ignore[attr-defined]
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
