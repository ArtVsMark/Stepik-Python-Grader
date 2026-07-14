"""Тесты core/history.py — SQLite-история прогонов (issue #344, эпик #342).

Покрывают Acceptance из issue: roundtrip, отсутствие БД без создания файла
(#134), graceful на битой БД, идемпотентная миграция user_version 0→1 (#135),
FK-каскад, наполнение lint_violations, фильтр по task_key и конкурентная
запись под WAL.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from stepik_grader.core import history
from stepik_grader.core.history import CaseRecord, LintRecord


def _db(tmp_path: Path) -> Path:
    return tmp_path / history.HISTORY_DB_NAME


def test_record_and_read_roundtrip(tmp_path: Path) -> None:
    """Записанный прогон читается обратно со всеми полями и cases."""
    db = _db(tmp_path)
    run_id = history.record_run(
        1,
        [CaseRecord(1, "OK", time_ms=12.5), CaseRecord(2, "RE", error_class="IndexError")],
        db_path=db,
        task_key="course/lesson/01",
        solution_name="task.py",
        solution_hash=history.hash_solution("print(1)"),
        duration_s=0.3,
    )
    assert run_id == 1

    runs = history.read_recent_runs(db)
    assert len(runs) == 1
    run = runs[0]
    assert run["mode"] == 1
    assert run["source"] == "cli"
    assert run["task_key"] == "course/lesson/01"
    assert run["solution_name"] == "task.py"
    assert run["duration_s"] == pytest.approx(0.3)
    assert [c["verdict"] for c in run["cases"]] == ["OK", "RE"]
    assert run["cases"][1]["error_class"] == "IndexError"
    assert run["cases"][0]["failure_kind"] is None  # заполняет #347


def test_absent_db_reads_empty_without_creating(tmp_path: Path) -> None:
    """Чтение несуществующей БД → [] и файл не создаётся (#134)."""
    db = _db(tmp_path)
    assert history.read_recent_runs(db) == []
    assert not db.exists()


def test_corrupt_db_is_graceful(tmp_path: Path) -> None:
    """Битый файл БД не роняет ни запись, ни чтение (best-effort)."""
    db = _db(tmp_path)
    db.write_bytes(b"not a sqlite database at all")
    assert history.record_run(1, [CaseRecord(1, "OK")], db_path=db) is None
    assert history.read_recent_runs(db) == []


def test_migration_sets_user_version_and_is_idempotent(tmp_path: Path) -> None:
    """Второй connect не пересоздаёт схему; user_version = SCHEMA_VERSION (#135)."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db)
    history.record_run(2, [CaseRecord(1, "WA")], db_path=db)  # миграция на 2-м connect — no-op
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


def test_foreign_key_cascade_on_delete(tmp_path: Path) -> None:
    """Удаление run каскадит case_results (PRAGMA foreign_keys=ON)."""
    db = _db(tmp_path)
    rid = history.record_run(1, [CaseRecord(1, "OK"), CaseRecord(2, "WA")], db_path=db)
    with contextlib.closing(history._connect(db)) as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (rid,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM case_results").fetchone()[0] == 0


def test_lint_records_persisted(tmp_path: Path) -> None:
    """lint-нарушения пишутся в lint_violations (наполнит #346)."""
    db = _db(tmp_path)
    rid = history.record_run(
        1,
        [CaseRecord(1, "OK")],
        db_path=db,
        lint=[LintRecord("E501", 10, "line too long"), LintRecord("F841", 3)],
    )
    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT rule_code, line_no FROM lint_violations WHERE run_id = ? ORDER BY rule_code",
            (rid,),
        ).fetchall()
    assert rows == [("E501", 10), ("F841", 3)]


def test_read_filters_by_task_key(tmp_path: Path) -> None:
    """read_recent_runs(task_key=...) возвращает только совпавшие прогоны."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="a")
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="b")
    only_a = history.read_recent_runs(db, task_key="a")
    assert len(only_a) == 1
    assert only_a[0]["task_key"] == "a"


def test_recent_runs_newest_first(tmp_path: Path) -> None:
    """Прогоны отдаются новыми первыми (ORDER BY id DESC)."""
    db = _db(tmp_path)
    for i in range(3):
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db, solution_name=f"s{i}.py")
    names = [r["solution_name"] for r in history.read_recent_runs(db)]
    assert names == ["s2.py", "s1.py", "s0.py"]


def test_concurrent_writes_under_wal(tmp_path: Path) -> None:
    """Конкурентные записи из нескольких потоков не теряются (WAL)."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db)  # прогрев: включить WAL до гонки
    n = 15

    def worker(i: int) -> None:
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db, solution_name=f"s{i}.py")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(history.read_recent_runs(db, limit=1000)) == n + 1


def test_hash_solution_is_deterministic_sha256() -> None:
    """hash_solution == sha256 hexdigest от utf-8 кода."""
    assert history.hash_solution("print(1)") == hashlib.sha256(b"print(1)").hexdigest()
