"""db.py — общий SQLite-коннектор (top-level leaf, issue #552, ADR-0011).

Архитектурный слой: Infrastructure / Utilities (top-level shared-leaf).

Единая точка подключения к локальным SQLite-базам пакета: PRAGMA-набор
(``busy_timeout`` + best-effort ``WAL`` + ``foreign_keys``) и примитивы
``user_version`` для идемпотентных миграций схемы. Раньше это было заперто внутри
``core/history.py`` — теперь переиспользуемо (ADR-0011): им пользуются и
``core/history`` (история прогонов), и очередь пополнения глоссария
(``glossary/json_provider``).

Живёт на верхнем уровне ``stepik_grader`` (не в ``core/``, как и ``atomic_io``,
issue #551): подпакет ``glossary/`` по архитектурному инварианту НЕ импортирует
``core/``, поэтому общий коннектор — top-level, чтобы им могли пользоваться и
core-модули, и ``glossary/``, не порождая ребра ``glossary → core``. Stdlib-only
(``sqlite3``), ничего из проекта не импортирует (leaf).

Best-effort по духу (как ``core/history``/``core/cache``): вызывающая сторона
оборачивает работу с БД в ``try/except (sqlite3.Error, OSError)`` и тихо
деградирует (пропуск записи / пустое чтение), не роняя грейдинг.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "BUSY_TIMEOUT_MS",
    "apply_schema",
    "connect",
    "set_user_version",
    "user_version",
]

# Явный busy_timeout: конкурентные писатели ЖДУТ write-lock, а не падают
# ``sqlite3.OperationalError`` → тихая потеря записи (issue #393/#552).
BUSY_TIMEOUT_MS = 10000


def connect(
    db_path: Path, *, migrate: Callable[[sqlite3.Connection], None] | None = None
) -> sqlite3.Connection:
    """Открыть соединение: ``busy_timeout`` + best-effort ``WAL`` + FK, опц. миграция.

    ``sqlite3.connect`` создаёт файл БД — вызывать только на пути записи или после
    проверки ``db_path.is_file()`` на пути чтения (не плодить БД при отключённой
    фиче). ``migrate`` (если задан) исполняется на открытом соединении для
    домиграции схемы. При сбое PRAGMA/миграции соединение закрывается и
    исключение пробрасывается (иначе fd утёк бы — вызывающий ``closing(...)`` не
    обернёт объект, которого не получил, issue #393).

    Raises:
        sqlite3.Error: при сбое PRAGMA/миграции (соединение уже закрыто).
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        # Смена journal_mode в WAL невозможна, пока к БД открыты ДРУГИЕ соединения
        # (барьерная конкурентная первая инициализация) — sqlite отдаёт
        # SQLITE_BUSY, busy_timeout тут не помогает. WAL — оптимизация, а не
        # требование: глотаем отказ и работаем в дефолтном rollback-journal
        # (следующее соединение доставит WAL, когда контекст разрядится) — иначе
        # весь прогон терялся бы на Windows (issue #393).
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if migrate is not None:
            migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    """Прочитать ``PRAGMA user_version`` (0 — свежая/пустая БД)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Записать ``PRAGMA user_version`` (int в f-string безопасен; PRAGMA не биндится)."""
    conn.execute(f"PRAGMA user_version = {int(version)}")


def apply_schema(conn: sqlite3.Connection, *, version: int, ddl: str) -> None:
    """Идемпотентная миграция ``user_version`` 0→``version`` аддитивным ``ddl``.

    Если текущая версия уже ``>= version`` — no-op (повторный вызов безопасен).
    Иначе исполняется ``ddl`` (должен быть идемпотентным — ``CREATE ... IF NOT
    EXISTS``, чтобы параллельная инициализация двумя процессами не падала
    ``table already exists``, issue #393) и выставляется ``version``. Для
    инкрементальных (1→2) изменений схемы вызывающая сторона пишет свою миграцию
    поверх этого примитива.
    """
    if user_version(conn) >= version:
        return
    conn.executescript(ddl)
    set_user_version(conn, version)
    conn.commit()
