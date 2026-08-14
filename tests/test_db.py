"""Tests for db.py — общий SQLite-коннектор (top-level leaf, issue #552, ADR-0011).

Проверяет контракт ``connect`` (PRAGMA WAL/FK/busy_timeout, callback-миграция,
close-on-fail) и примитивы ``user_version``/``set_user_version``/``apply_schema``.
"""

from __future__ import annotations

import contextlib
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from stepik_grader import db

_DDL = "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT);"


def test_connect_creates_db_and_enables_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        assert path.exists()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS


def test_user_version_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        assert db.user_version(conn) == 0  # свежая БД
        db.set_user_version(conn, 7)
        assert db.user_version(conn) == 7


def test_apply_schema_creates_tables_and_sets_version(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        assert db.user_version(conn) == 1
        conn.execute("INSERT INTO t (v) VALUES ('a')")  # таблица существует
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "a"


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        db.apply_schema(conn, version=1, ddl=_DDL)  # повторно — no-op, не падает
        assert db.user_version(conn) == 1


def test_connect_runs_migrate_callback(tmp_path: Path) -> None:
    path = tmp_path / "x.db"

    def _migrate(conn: sqlite3.Connection) -> None:
        db.apply_schema(conn, version=1, ddl=_DDL)

    with contextlib.closing(db.connect(path, migrate=_migrate)) as conn:
        assert db.user_version(conn) == 1
        conn.execute("INSERT INTO t (v) VALUES ('b')")


def test_connect_migrate_failure_closes_connection_and_raises(tmp_path: Path) -> None:
    """Сбой миграции пробрасывается, а соединение не утекает (закрыто внутри)."""
    path = tmp_path / "x.db"
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def _tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(conn)
        return conn

    def _boom(_conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("migration boom")

    import stepik_grader.db as db_mod

    original = db_mod.sqlite3.connect
    db_mod.sqlite3.connect = _tracking_connect  # type: ignore[assignment]
    try:
        with pytest.raises(sqlite3.OperationalError, match="migration boom"):
            db.connect(path, migrate=_boom)
    finally:
        db_mod.sqlite3.connect = original  # type: ignore[assignment]

    assert opened, "sqlite3.connect не вызывался"
    # Соединение закрыто (повторный execute на закрытом → ProgrammingError).
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_second_connection_sees_committed_schema(tmp_path: Path) -> None:
    """Схема, созданная миграцией, видна следующему соединению (commit сработал)."""
    path = tmp_path / "x.db"

    def _migrate(conn: sqlite3.Connection) -> None:
        db.apply_schema(conn, version=1, ddl=_DDL)

    with contextlib.closing(db.connect(path, migrate=_migrate)) as conn:
        conn.execute("INSERT INTO t (v) VALUES ('c')")
        conn.commit()
    with contextlib.closing(db.connect(path, migrate=_migrate)) as conn2:
        assert db.user_version(conn2) == 1
        assert conn2.execute("SELECT v FROM t").fetchone()[0] == "c"


# ---------------------------------------------------------------------------
# Приватные права БД — issue #813 (SECD-04)
# ---------------------------------------------------------------------------


def test_connect_restricts_db_to_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """БД сужается до 0600 сразу при создании — ДО включения WAL.

    База истории появляется рядом с решениями (личный репозиторий задач) с
    правами по umask, обычно 0644 — журнал обучения читаем другими
    пользователями машины. Порядок важен: SQLite создаёт ``-wal``/``-shm`` с
    правами основной БД, поэтому сужать нужно раньше, чем он их создаст.
    """
    calls: list[tuple[str, int]] = []
    real_chmod = Path.chmod

    def _spy(self: Path, mode: int, **kwargs: object) -> None:
        calls.append((self.name, mode))
        real_chmod(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", _spy)
    with contextlib.closing(db.connect(tmp_path / "h.db")) as conn:
        conn.execute(_DDL)

    assert calls, "права БД не сужались вовсе"
    assert {mode for _name, mode in calls} == {0o600}
    assert calls[0][0] == "h.db"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-биты режима; на Windows chmod — no-op")
def test_db_file_mode_is_owner_only(tmp_path: Path) -> None:
    """Фактический режим файла на POSIX — только владелец."""
    path = tmp_path / "h.db"
    with contextlib.closing(db.connect(path)) as conn:
        conn.execute(_DDL)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_restrict_to_owner_covers_wal_sidecars(tmp_path: Path) -> None:
    """Спутники WAL/SHM тоже сужаются: в журнале те же данные, что в базе.

    Отдельный случай — БД от прежней версии, где спутники уже лежат с широкими
    правами: одного chmod по основному файлу тут мало.
    """
    path = tmp_path / "h.db"
    path.write_bytes(b"")
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).write_bytes(b"")

    calls: list[str] = []
    real_chmod = Path.chmod

    def _spy(self: Path, mode: int, **kwargs: object) -> None:
        calls.append(self.name)
        real_chmod(self, mode, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "chmod", _spy)
        db.restrict_to_owner(path)

    assert calls == ["h.db", "h.db-wal", "h.db-shm"]


def test_restrict_to_owner_survives_missing_file(tmp_path: Path) -> None:
    """Файла нет — не ошибка: работа с историей best-effort по всему модулю."""
    db.restrict_to_owner(tmp_path / "nope.db")  # не должно бросать


# ---------------------------------------------------------------------------
# issue #794 (MIGR-01) — защита от отката версии схемы. До фикса `cur >= version`
# схлопывал «уже мигрировано» и «база новее нас» в один молчаливый no-op: после
# отката грейдера старый код продолжал писать в базу будущей схемы.
# ---------------------------------------------------------------------------


def test_apply_schema_is_noop_on_exact_version(tmp_path: Path) -> None:
    """Равная версия — по-прежнему no-op: повторный вызов обязан быть безопасен."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        db.apply_schema(conn, version=1, ddl="SELECT raise_this_would_fail;")
        assert db.user_version(conn) == 1


def test_apply_schema_refuses_newer_database(tmp_path: Path) -> None:
    """Версия в базе выше ожидаемой — явный отказ вместо молчаливого прохода."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.set_user_version(conn, 7)
        with pytest.raises(db.SchemaTooNewError) as excinfo:
            db.apply_schema(conn, version=1, ddl=_DDL)
        assert excinfo.value.found == 7
        assert excinfo.value.expected == 1
        assert "7" in str(excinfo.value)
        # Отказ, а не порча: DDL не исполнялся, версию не понизили.
        assert db.user_version(conn) == 7
        assert (
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 't'").fetchone()[0] == 0
        )


def test_schema_too_new_is_a_sqlite_error(tmp_path: Path) -> None:
    """Потомок ``sqlite3.DatabaseError``: прежние best-effort обработчики ловят его.

    Не деталь реализации, а контракт: весь код вокруг локальных баз написан под
    ``except (sqlite3.Error, OSError)``, и новое исключение не должно пролетать
    мимо них, роняя грейдинг.
    """
    assert issubclass(db.SchemaTooNewError, sqlite3.DatabaseError)
    assert issubclass(db.SchemaTooNewError, sqlite3.Error)


def test_connect_closes_connection_when_migration_refuses(tmp_path: Path) -> None:
    """Отказ миграции закрывает соединение — fd не течёт (контракт connect, #393)."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.set_user_version(conn, 9)

    captured: list[sqlite3.Connection] = []

    def _migrate(conn: sqlite3.Connection) -> None:
        captured.append(conn)
        db.apply_schema(conn, version=1, ddl=_DDL)

    with pytest.raises(db.SchemaTooNewError):
        db.connect(path, migrate=_migrate)
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")


# ---------------------------------------------------------------------------
# issue #947 — write-lock на время миграции: версия читалась вне транзакции,
# и два процесса входили в неё одновременно
# ---------------------------------------------------------------------------


def test_exclusive_transaction_holds_write_lock(tmp_path: Path) -> None:
    """Внутри блока лок взят, после выхода — снят и данные закоммичены."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        with db.exclusive_transaction(conn):
            assert conn.in_transaction
            conn.execute("INSERT INTO t (v) VALUES ('a')")
        assert not conn.in_transaction

    with contextlib.closing(sqlite3.connect(path)) as other:
        assert other.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_exclusive_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    """Исключение внутри блока откатывает всё, что успело записаться."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        with pytest.raises(RuntimeError), db.exclusive_transaction(conn):
            conn.execute("INSERT INTO t (v) VALUES ('a')")
            raise RuntimeError("сбой посреди транзакции")
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_exclusive_transaction_is_reentrant(tmp_path: Path) -> None:
    """Вложенный вход не открывает вторую транзакцию и не коммитит раньше срока.

    Нужно ``core/history._migrate``: он берёт лок на всю многоступенчатую
    миграцию и вызывает внутри ``apply_schema``, который тоже просит лок.
    Sqlite вложенных ``BEGIN`` не умеет — вложенный вход обязан быть no-op.
    """
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.apply_schema(conn, version=1, ddl=_DDL)
        with db.exclusive_transaction(conn):
            conn.execute("INSERT INTO t (v) VALUES ('outer')")
            with db.exclusive_transaction(conn):
                conn.execute("INSERT INTO t (v) VALUES ('inner')")
            assert conn.in_transaction, "вложенный выход закоммитил внешнюю транзакцию"
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_execute_ddl_script_keeps_transaction_open(tmp_path: Path) -> None:
    """Многооператорный DDL не разрывает транзакцию — в отличие от executescript.

    Это и есть причина дефекта #947: ``executescript`` делает неявный COMMIT,
    то есть снимает взятый write-lock ровно в середине миграции. Проверяем оба
    поведения рядом, чтобы разница была видна, а не подразумевалась.
    """
    path = tmp_path / "x.db"
    script = "CREATE TABLE IF NOT EXISTS a (x INTEGER);\nCREATE TABLE IF NOT EXISTS b (y INTEGER);"
    with contextlib.closing(db.connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.execute_ddl_script(conn, script)
        assert conn.in_transaction, "execute_ddl_script разорвал транзакцию"
        conn.execute("COMMIT")

        conn.execute("BEGIN IMMEDIATE")
        conn.executescript(script)
        assert not conn.in_transaction, (
            "поведение executescript изменилось — фикс #947 пересмотреть"
        )

    with contextlib.closing(sqlite3.connect(path)) as other:
        names = {
            row[0] for row in other.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"a", "b"} <= names


def test_execute_ddl_script_runs_statement_without_trailing_semicolon(tmp_path: Path) -> None:
    """Последнее выражение без ``;`` тоже исполняется, а не теряется молча."""
    path = tmp_path / "x.db"
    with contextlib.closing(db.connect(path)) as conn:
        db.execute_ddl_script(conn, "CREATE TABLE IF NOT EXISTS solo (x INTEGER)")
        assert conn.execute("SELECT COUNT(*) FROM solo").fetchone()[0] == 0


def test_apply_schema_rechecks_version_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Версия перечитывается под локом: сосед мог мигрировать, пока мы ждали.

    Патчим ``user_version`` так, чтобы первое (быстрое) чтение отдало 0, а
    второе — уже 1: ровно то, что видит процесс, дождавшийся чужой миграции.
    DDL при этом исполняться не должен.
    """
    path = tmp_path / "x.db"
    versions = iter([0, 1])

    def fake_user_version(conn: sqlite3.Connection) -> int:
        return next(versions)

    with contextlib.closing(db.connect(path)) as conn:
        monkeypatch.setattr(db, "user_version", fake_user_version)
        db.apply_schema(conn, version=1, ddl=_DDL)
        monkeypatch.undo()

        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "t" not in tables, "DDL применён поверх чужой завершённой миграции"
