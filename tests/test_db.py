"""Tests for db.py — общий SQLite-коннектор (top-level leaf, issue #552, ADR-0011).

Проверяет контракт ``connect`` (PRAGMA WAL/FK/busy_timeout, callback-миграция,
close-on-fail) и примитивы ``user_version``/``set_user_version``/``apply_schema``.
"""

from __future__ import annotations

import contextlib
import sqlite3
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
