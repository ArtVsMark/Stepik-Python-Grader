"""_run_dir.py — общий per-run tmp dir с устойчивой очисткой (issue #266).

Обычный ``tempfile.TemporaryDirectory()`` context manager падает
``PermissionError``, если внутри директории всё ещё держит хэндл
(CWD/открытый файл) процесс, который ещё не успел полностью завершиться —
наблюдалось на практике на Windows backend'е (``_windows.py``): после
``CloseHandle(job)`` с ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` завершение всех
процессов job'а (включая "внучатые" — форк-бомбой запущенные) инициируется
асинхронно, и попытка ``rmtree`` сразу после может обогнать фактическое
освобождение хэндлов ОС на директорию (их CWD). На POSIX (bwrap/sandbox-exec)
теоретически возможна аналогичная гонка для процессов, вышедших из-под
видимого дерева. ``ephemeral_run_dir()`` даёт несколько попыток с паузой
перед тем как сдаться — а если директория так и не удалилась, не поднимает
исключение (это лишь несколько лишних КБ во временной папке ОС, а не
ошибка корректности запуска) — только предупреждение.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["ephemeral_run_dir"]


@contextmanager
def ephemeral_run_dir(prefix: str = "stepik-sandbox-") -> Iterator[Path]:
    """Создать per-run временную директорию и гарантированно попытаться
    удалить её при выходе — устойчиво к небольшой асинхронной задержке
    завершения дочерних процессов (см. докстринг модуля)."""
    run_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield run_dir
    finally:
        _safe_rmtree(run_dir)


def _safe_rmtree(path: Path, attempts: int = 5, delay: float = 0.1) -> None:
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == attempts - 1:
                warnings.warn(
                    f"sandbox: could not remove ephemeral run dir {path} "
                    "(likely a not-yet-reaped child process still holding it open) "
                    "-- leaving it for OS temp cleanup",
                    stacklevel=2,
                )
                return
            time.sleep(delay)
