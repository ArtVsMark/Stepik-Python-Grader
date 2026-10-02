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

issue #996 (``OPS-1-07``): у ``finally`` есть предел. ``SIGKILL``, падение
интерпретатора, выключение машины посреди прогона — и каталог остаётся в
temp навсегда: следующий запуск создаёт свой, прошлые никто не подметает.
Поэтому первый за процесс вызов ``ephemeral_run_dir()`` убирает **чужие**
брошенные каталоги того же префикса, старше суток (:func:`sweep_orphans`).
Отказ уборки теперь идёт и в диагностический лог: ``warnings.warn`` в CLI по
умолчанию не показывается, и симптом «temp пухнет» приходил без следа о
причине.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stepik_grader.core.diag_log import get_logger

__all__ = ["ephemeral_run_dir", "sweep_orphans"]

_log = get_logger("run_dir")  # короткое имя — соглашение diag_log.get_logger

RUN_DIR_PREFIX = "stepik-sandbox-"

#: Возраст, с которого брошенный каталог считается осиротевшим. Сутки —
#: намеренно много: подметаются каталоги ЧУЖИХ процессов, а знать наверняка,
#: жив ли владелец, нельзя. Прогон живёт минуты, поэтому запас на три порядка
#: исключает удаление каталога у работающего рядом грейдера.
ORPHAN_AGE_SECONDS = 24 * 60 * 60

#: Подметание — один раз за процесс: это уборка мусора прошлых запусков, а не
#: работа, которую надо делать перед каждым кейсом. При сотне тест-кейсов скан
#: temp на каждый вызов был бы стократ лишним.
_swept = False


@contextmanager
def ephemeral_run_dir(prefix: str = RUN_DIR_PREFIX) -> Iterator[Path]:
    """Создать per-run временную директорию и гарантированно попытаться
    удалить её при выходе — устойчиво к небольшой асинхронной задержке
    завершения дочерних процессов (см. докстринг модуля).

    Первый вызов в процессе заодно подметает осиротевшие каталоги прошлых
    запусков (issue #996). Уборка своя — в ``finally``; чужая — только по
    возрасту, см. :func:`sweep_orphans`.
    """
    global _swept
    if not _swept:
        _swept = True
        sweep_orphans(prefix)
    run_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield run_dir
    finally:
        _safe_rmtree(run_dir)


def sweep_orphans(
    prefix: str = RUN_DIR_PREFIX,
    *,
    max_age_seconds: float = ORPHAN_AGE_SECONDS,
    root: Path | None = None,
) -> int:
    """Удалить каталоги прошлых прогонов, брошенные аварийным завершением.

    Возвращает число убранных каталогов. Ничего не поднимает: уборка мусора не
    вправе ронять прогон, ради которого её позвали.

    Границы намеренно узкие — удаляется только то, что заведомо наше и
    заведомо мертво:

    * имя начинается с ``prefix`` и лежит прямо в temp-каталоге ОС;
    * это директория, а не симлинк (иначе ``rmtree`` ушёл бы по ссылке
      в чужое дерево);
    * последнее изменение старше ``max_age_seconds``.
    """
    base = root if root is not None else Path(tempfile.gettempdir())
    deadline = time.time() - max_age_seconds
    removed = 0
    try:
        candidates = sorted(base.glob(f"{prefix}*"))
    except OSError as exc:  # temp недоступен — не наша беда и не наш прогон
        _log.debug("sandbox: не удалось перечислить %s: %s", base, exc)
        return 0

    for path in candidates:
        try:
            if path.is_symlink() or not path.is_dir() or path.stat().st_mtime > deadline:
                continue
            shutil.rmtree(path)
        except OSError as exc:
            # Чужой каталог мог исчезнуть между glob и rmtree, а мог
            # принадлежать другому пользователю. И то, и другое — не ошибка:
            # уборка best-effort по определению.
            _log.debug("sandbox: осиротевший каталог %s не убран: %s", path, exc)
            continue
        removed += 1

    if removed:
        _log.info("sandbox: убрано осиротевших каталогов прошлых прогонов: %d", removed)
    return removed


def _safe_rmtree(path: Path, attempts: int = 5, delay: float = 0.1) -> None:
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            if attempt == attempts - 1:
                # issue #996 (OPS-1-07): и в лог, и предупреждением. warnings в
                # CLI по умолчанию не показывается, поэтому единственный след
                # отказа прежде терялся — а симптом («temp пухнет») всплывал
                # много позже и без причины.
                _log.warning(
                    "sandbox: не удалось удалить временный каталог прогона %s: %s "
                    "(вероятно, ещё не завершился дочерний процесс) — "
                    "оставлен на уборку ОС",
                    path,
                    exc,
                )
                warnings.warn(
                    f"sandbox: could not remove ephemeral run dir {path} "
                    "(likely a not-yet-reaped child process still holding it open) "
                    "-- leaving it for OS temp cleanup",
                    stacklevel=2,
                )
                return
            time.sleep(delay)
