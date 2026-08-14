"""spawn.py — порождение процессов со страховкой от зависания на ЗАПУСКЕ.

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib).

``timeout=`` у ``subprocess`` покрывает ожидание **уже стартовавшего** процесса.
Зависнуть можно раньше — в самом ``Popen.__init__``, на чтении errpipe после
fork/exec:

.. code-block:: text

    subprocess.py:1943 in _execute_child
        part = os.read(errpipe_read, 50000)
    +++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++

Вызывающий поток остаётся заблокированным навсегда: в CLI это повисший
грейдер, под ``--serve`` — занятый воркер, которого никто не отменит. Тот же
симптом ловил pytest-timeout на macOS и Windows с Python 3.14 — сперва в
``run_lint`` (issue #877, лечилось прямо там), затем в ``core/runner`` и в
``scripts/preflight``. Три точки на один механизм — повод вынести приём в общий
модуль, а не лечить каждую отдельно.

**Как устроено.** Запуск уходит в daemon-поток с собственным дедлайном. Поток
может остаться висеть, но daemon не мешает процессу завершиться (в отличие от
воркеров ``ThreadPoolExecutor`` — тот случай разбирался в issue #806).

**Сироту не оставляем.** Если дедлайн истёк, а ``Popen`` всё-таки родил
процесс, поток убивает его сам: иначе решение продолжало бы выполняться без
всякого надзора, занимая CPU до конца прогона.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from typing import Any

__all__ = ["DEFAULT_LAUNCH_TIMEOUT_S", "SpawnTimeout", "guarded_popen", "guarded_run"]

# Сколько ждём САМ запуск. Здоровый ``Popen`` укладывается в миллисекунды даже
# на медленном раннере, поэтому запас велик намеренно: цель — отличить
# «подвисло навсегда» от «система под нагрузкой», а не подгонять порог.
DEFAULT_LAUNCH_TIMEOUT_S = 20.0


class SpawnTimeout(RuntimeError):
    """Процесс не удалось породить за отведённое время (не «упал», а «не стартовал»)."""


def guarded_popen(
    args: list[str],
    *,
    launch_timeout: float | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    """``subprocess.Popen`` с дедлайном на сам запуск.

    Возвращает готовый процесс — вызывающая сторона работает с ним как обычно
    (``communicate``, ``kill``, ``killpg``). Отличие только в том, что
    зависший запуск даёт ошибку, а не вечное ожидание.

    Raises:
        SpawnTimeout: запуск не уложился в ``launch_timeout``. Процесс, если он
            всё же появится позже, будет убит фоновым потоком.
        Exception: то же, что бросил бы обычный ``Popen`` (``FileNotFoundError``,
            ``PermissionError`` и прочее) — исключение переносится из потока
            без изменения типа, чтобы вызывающий ловил его как раньше.
    """
    # Дефолт читается ЗДЕСЬ, а не в сигнатуре: значение по умолчанию
    # связывается при определении функции, и подмена константы (в тестах или
    # ради разовой настройки) на такой дефолт уже не влияла бы.
    deadline = DEFAULT_LAUNCH_TIMEOUT_S if launch_timeout is None else launch_timeout
    outcome: list[subprocess.Popen[Any] | BaseException] = []
    gave_up = threading.Event()

    def _worker() -> None:
        try:
            proc = subprocess.Popen(args, **kwargs)
        except BaseException as exc:  # переносим в вызывающий поток как есть
            outcome.append(exc)
            return
        if gave_up.is_set():
            # Родился уже после того, как мы перестали ждать: убиваем, иначе
            # решение работает без надзора до конца прогона.
            with contextlib.suppress(OSError):
                proc.kill()
            return
        outcome.append(proc)

    thread = threading.Thread(target=_worker, daemon=True, name="guarded-spawn")
    thread.start()
    thread.join(deadline)
    if thread.is_alive() or not outcome:
        gave_up.set()
        raise SpawnTimeout(f"процесс не стартовал за {deadline:g} с: {args[0]}")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def guarded_run(
    args: list[str],
    *,
    launch_timeout: float | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` с тем же дедлайном на запуск.

    Общий дедлайн потока — ``launch_timeout`` плюс ``timeout`` самого вызова
    (если он задан): первый покрывает старт, второй — работу процесса.

    Raises:
        SpawnTimeout: не уложились в суммарный дедлайн.
        Exception: то же, что бросил бы обычный ``subprocess.run``.
    """
    deadline = DEFAULT_LAUNCH_TIMEOUT_S if launch_timeout is None else launch_timeout
    outcome: list[subprocess.CompletedProcess[Any] | BaseException] = []

    def _worker() -> None:
        try:
            outcome.append(subprocess.run(args, **kwargs))
        except BaseException as exc:  # переносим в вызывающий поток как есть
            outcome.append(exc)

    thread = threading.Thread(target=_worker, daemon=True, name="guarded-run")
    thread.start()
    thread.join(deadline + float(kwargs.get("timeout") or 0.0))
    if thread.is_alive() or not outcome:
        raise SpawnTimeout(f"процесс не завершился в срок: {args[0]}")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result
