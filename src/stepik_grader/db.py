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
деградирует (пропуск записи / пустое чтение), не роняя грейдинг. Единственное
исключение из «тихо» — ``SchemaTooNewError`` (issue #794): откат грейдера на
версию со старой схемой обязан быть слышен, иначе старый код молча пишет в базу
будущей схемы. Класс — потомок ``sqlite3.DatabaseError``, чтобы прежние
обработчики ловили его сами и деградация оставалась штатной.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "BUSY_TIMEOUT_MS",
    "SchemaTooNewError",
    "apply_schema",
    "connect",
    "restrict_to_owner",
    "set_user_version",
    "user_version",
]


class SchemaTooNewError(sqlite3.DatabaseError):
    """БД записана более новой версией схемы, чем понимает этот код (issue #794).

    Наследуется от ``sqlite3.DatabaseError`` намеренно: вся работа с локальными
    базами best-effort и обёрнута в ``except (sqlite3.Error, OSError)``, поэтому
    новое исключение подхватывается существующими обработчиками и деградирует
    штатно, а не роняет грейдинг мимо них. Отличать откат от обычного сбоя БД
    вызывающая сторона может по типу — так делает ``core/history``, печатая
    отдельную подсказку.
    """

    def __init__(self, *, found: int, expected: int) -> None:
        super().__init__(
            f"версия схемы БД {found} новее ожидаемой {expected} — "
            "база создана более новой версией грейдера"
        )
        self.found = found
        self.expected = expected


# Явный busy_timeout: конкурентные писатели ЖДУТ write-lock, а не падают
# ``sqlite3.OperationalError`` → тихая потеря записи (issue #393/#552).
BUSY_TIMEOUT_MS = 10000


# issue #813 (SECD-04): к каким файлам применяется приватный режим — сама БД и
# её WAL/SHM-спутники. Журнал WAL содержит те же данные, что и база, поэтому
# ограничивать права только на `.db` было бы половиной меры.
_DB_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def restrict_to_owner(db_path: Path) -> None:
    """Привести БД и её WAL/SHM-спутники к правам 0600 (best-effort).

    issue #813 (SECD-04): база истории создавалась с правами по umask (обычно
    0644) рядом с решениями — то есть в личном репозитории задач, читаемая
    другими пользователями машины. Содержимое не секрет в смысле токенов, но
    это журнал обучения: что решал, когда, сколько раз ошибся.

    На Windows ``os.chmod`` не имеет эквивалента Unix-битам group/other (модель
    доступа — NTFS ACL), поэтому там вызов практически no-op и файл остаётся
    защищён правами профиля пользователя — тот же компромисс, что у
    ``storage.save_secrets`` (issue #243).
    """
    for suffix in _DB_SIDECAR_SUFFIXES:
        sidecar = db_path.with_name(db_path.name + suffix)
        with contextlib.suppress(OSError):
            if sidecar.exists():
                sidecar.chmod(0o600)


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
    # issue #818: каталог базы может не существовать — с единой пользовательской
    # историей путь стал `~/.stepik-grader/history.db`, и `sqlite3.connect`
    # падает «unable to open database file», если каталога нет (поймано
    # прогоном: база не создавалась вовсе, разделы «Подучить»/«Прогресс»
    # оставались пустыми). Раньше база всегда лежала в существующей cwd.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # issue #813: сужаем права СРАЗУ после создания файла и ДО включения WAL —
    # SQLite создаёт `-wal`/`-shm` с правами основной БД, поэтому достаточно
    # опередить его. Цикл в restrict_to_owner при этом подтянет и спутники,
    # оставшиеся от прежних версий с широкими правами.
    restrict_to_owner(db_path)
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

    Если текущая версия РАВНА ``version`` — no-op (повторный вызов безопасен).
    Иначе исполняется ``ddl`` (должен быть идемпотентным — ``CREATE ... IF NOT
    EXISTS``, чтобы параллельная инициализация двумя процессами не падала
    ``table already exists``, issue #393) и выставляется ``version``. Для
    инкрементальных (1→2) изменений схемы вызывающая сторона пишет свою миграцию
    поверх этого примитива — и сама сверяет ИТОГОВУЮ версию с базой, потому что
    здесь про промежуточные ступени ничего не известно (см. ``core/history``).

    Raises:
        SchemaTooNewError: ``user_version`` в базе БОЛЬШЕ ``version``. Раньше это
            была та же ветка «уже мигрировано» — молчаливый no-op (issue #794):
            после отката грейдера старый код продолжал писать в базу будущей
            схемы, портя данные, а при несовместимом DDL прогоны просто терялись.
    """
    current = user_version(conn)
    if current == version:
        return
    if current > version:
        raise SchemaTooNewError(found=current, expected=version)
    conn.executescript(ddl)
    set_user_version(conn, version)
    conn.commit()
