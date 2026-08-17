"""atomic_io.py — атомарная запись JSON (общий leaf-хелпер, issue #551, ADR-0011).

Архитектурный слой: Infrastructure / Utilities (top-level shared-leaf).

Единый атомарный JSON-писатель для потребителей во ВСЁМ пакете — включая
подпакеты (``glossary/``/``rules/``), которые по архитектурному инварианту НЕ
импортируют ``core/``. Поэтому модуль живёт на верхнем уровне ``stepik_grader``
(не в ``core/``, решение зафиксировано в ADR-0011): ребро ``glossary → core`` не
возникает, а общий писатель всё равно доступен и core-модулям
(``user_settings``), и подпакетам. Как ``core/storage.py``, ничего из проекта не
импортирует (stdlib-only, leaf).

Пишет во временный файл в ТОЙ ЖЕ директории и заменяет цель через ``os.replace``
(атомарный rename на POSIX и Windows) — прерывание/краш/конкурентная запись не
оставляют усечённый файл: читатель видит либо старую, либо новую полную версию
(прежний ``open("w")`` сначала обрезал целевой файл, обрыв рвал backlog #363).
``mkstemp`` даёт уникальное имя (параллельные писатели не делят temp) и права
0600 на время записи. Temp рядом с целью — чтобы ``replace`` шёл в пределах одной
ФС (rename между ФС не атомарен).
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
import time
from collections.abc import Iterator
from typing import Any

__all__ = ["LOCK_SUFFIX", "atomic_write_json", "atomic_write_text", "file_lock"]

# Спутник-файл блокировки. Лочится ОН, а не сама цель: `atomic_write_json`
# заменяет цель через `os.replace`, то есть меняет inode — блокировка, взятая на
# прежнем inode, после замены не значит ничего, и второй писатель спокойно взял
# бы «свободный» файл (issue #1136, LNCH-3-06).
LOCK_SUFFIX = ".lock"

# Пауза между попытками взять лок. Записи в настройки редки и коротки, поэтому
# опрос дешевле, чем блокирующий вызов: он одинаково работает на обеих
# платформах и не требует отдельного пути с таймаутом.
_LOCK_POLL_S = 0.01

try:  # POSIX
    import fcntl

    def _try_acquire(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _release(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)

except ImportError:  # pragma: no cover — ветка исполняется только на Windows
    try:
        import msvcrt

        def _try_acquire(fd: int) -> bool:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                # Игнор нужен потому, что символы `msvcrt` существуют только на
                # Windows, а проверка типов гоняется на Linux — тот же приём, что
                # в core/sandbox/* (ради него в проекте и выключен
                # warn_unused_ignores: на «своей» ОС игнор выглядит лишним).
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError:
                return False
            return True

        def _release(fd: int) -> None:
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

    except ImportError:  # ни fcntl, ни msvcrt — экзотическая сборка Python

        def _try_acquire(fd: int) -> bool:
            return False

        def _release(fd: int) -> None:
            return None


@contextlib.contextmanager
def file_lock(path: pathlib.Path, *, timeout: float = 10.0) -> Iterator[bool]:
    """Взять межпроцессную блокировку на изменение ``path`` (issue #1136).

    Атомарная запись защищает от УСЕЧЁННОГО файла, но не от потерянного
    обновления: настройки меняются циклом «прочитать всё → заменить одно поле →
    записать всё», и два процесса (лаунчер и веб пишут один
    ``.grader_settings.json``) успевают прочитать одно и то же состояние.
    Проверено прогоном: две конкурентные записи роняли чужой ключ примерно в
    каждом десятом заходе.

    Блокировка **best-effort**, и это осознанный компромисс: она отдаёт
    ``False``, если лок не удалось взять за ``timeout`` или платформа его не
    поддерживает, но блок всё равно исполняется. Отказать в сохранении выбора
    было бы хуже потерянного обновления — пользователь бы решил, что настройка
    не работает вовсе.

    Yields:
        ``True`` — блокировка взята (запись сериализована), ``False`` — идёт
        как раньше, без неё.
    """
    lock_path = path.with_name(path.name + LOCK_SUFFIX)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        # Нет прав на запись рядом с целью / read-only ФС: цель, скорее всего,
        # тоже не запишется, но решать это не здесь.
        yield False
        return
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            acquired = _try_acquire(fd)
            if acquired or time.monotonic() >= deadline:
                break
            time.sleep(_LOCK_POLL_S)
        yield acquired
    finally:
        if acquired:
            _release(fd)
        os.close(fd)


def _inherit_mode(tmp_path: pathlib.Path, target: pathlib.Path) -> None:
    """Перенести на temp права уже существующей цели (issue #996, ``ARCH-3-05``).

    ``mkstemp`` даёт 0600, и для НОВОГО файла так и остаётся: не секрет, но
    owner-only безопаснее и локально достаточно. А вот у существующего файла
    права — решение пользователя, и замена содержимого не повод его отменять:
    сделанный когда-то `chmod g+r` на общий конфиг молча переставал работать.

    Прежде это умел только `core/storage.save_json_file`, и два «единых»
    атомарных писателя расходились ровно здесь. На Windows ``chmod`` по сути
    no-op (права — NTFS ACL), поэтому и там, и при отсутствии цели тихо ничего
    не происходит.
    """
    with contextlib.suppress(OSError):
        tmp_path.chmod(target.stat().st_mode & 0o777)


def atomic_write_text(path: pathlib.Path, text: str, *, fsync: bool = True) -> None:
    """Атомарно записать ``text`` в ``path`` (создавая директории).

    Тот же приём, что у :func:`atomic_write_json` — temp рядом с целью и
    ``replace`` — но для готовой строки: JSON Lines, отчёты и прочий текст,
    который не является одним JSON-документом (issue #793).

    Raises:
        OSError: при сбое записи/``replace`` (temp best-effort убирается, цель
            не тронута).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        _inherit_mode(tmp_path, path)
        tmp_path.replace(path)
    except BaseException:  # issue #996 (PY-3-05) — см. `atomic_write_json`
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def atomic_write_json(path: pathlib.Path, data: Any, *, fsync: bool = True) -> None:
    """Атомарно записать ``data`` как JSON в ``path`` (создавая директории).

    ``data`` — любое JSON-сериализуемое значение (dict/list/...). ``fsync=True``
    (по умолчанию) форсит запись на диск до ``replace`` — durability ценой одного
    fsync; ``fsync=False`` для часто пишущихся не критичных файлов (настройки),
    где достаточно атомарности замены без гарантии сохранности при сбое питания.

    Raises:
        OSError: при сбое записи/``replace`` (temp best-effort убирается, цель не
            тронута); вызывающая сторона решает, глушить ли (best-effort-писатели
            вроде ``save_missing_queue`` оборачивают вызов в ``try/except``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # mkstemp в той же директории → уникальное имя (параллельные писатели не
    # делят temp) и приватные права 0600 на время записи.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        _inherit_mode(tmp_path, path)
        tmp_path.replace(path)
    except BaseException:
        # issue #996 (PY-3-05): ловится ВСЁ, что прервало запись, а не только
        # `OSError`. Ctrl+C приходит `KeyboardInterrupt`, а он `BaseException`
        # и мимо прежнего `except OSError` проходил насквозь — temp-файл
        # `.<имя>.<случайное>.tmp` оставался рядом с целью навсегда, и никто
        # его не подметал. Исключение всегда пробрасывается дальше: уборка
        # temp'а не превращает сбой записи в успех.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
