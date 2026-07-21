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
        solution_hash=hashlib.sha256(b"print(1)").hexdigest(),
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


def test_concurrent_init_no_data_loss(tmp_path: Path) -> None:
    """issue #393: одновременная ПЕРВАЯ инициализация свежей БД многими потоками
    (без прогрева) не теряет записи. Раньше второй мигрирующий поток получал
    ``OperationalError: table runs already exists``, и запись тихо терялась
    через best-effort except в record_run."""
    db = _db(tmp_path)
    n = 40
    barrier = threading.Barrier(n)
    ids: list[int | None] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()  # стартуем миграцию максимально одновременно
        rid = history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key=f"t{i}")
        with lock:
            ids.append(rid)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r is not None for r in ids), "часть записей потеряна на гонке миграции"
    assert len(history.read_recent_runs(db, limit=1000)) == n


def test_record_run_retries_transient_operational_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #605: транзиентный ``OperationalError`` (SQLITE_BUSY) повторяется,
    а не теряется тихо — запись проходит со второй попытки."""
    db = _db(tmp_path)
    real_connect = history._connect
    calls = {"n": 0}

    def flaky_connect(path: Path) -> sqlite3.Connection:
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path)

    monkeypatch.setattr(history, "_connect", flaky_connect)
    monkeypatch.setattr(history.time, "sleep", lambda *_: None)  # тест не спит

    rid = history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="t")

    assert rid is not None
    assert calls["n"] == 2
    assert len(history.read_recent_runs(db, limit=10)) == 1


def test_record_run_gives_up_after_persistent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #605: при неснимаемой блокировке — best-effort ``None`` после
    ``_WRITE_ATTEMPTS`` попыток, без исключения (грейдинг не должен падать)."""
    db = _db(tmp_path)
    calls = {"n": 0}

    def always_locked(path: Path) -> sqlite3.Connection:
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(history, "_connect", always_locked)
    monkeypatch.setattr(history.time, "sleep", lambda *_: None)

    rid = history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="t")

    assert rid is None
    assert calls["n"] == history._WRITE_ATTEMPTS


def test_record_run_non_operational_error_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #605: не-транзиентная ``sqlite3.Error`` (напр. IntegrityError) —
    сразу ``None``, без ретраев (повторять смысла нет)."""
    db = _db(tmp_path)
    calls = {"n": 0}

    def integrity_boom(path: Path) -> sqlite3.Connection:
        calls["n"] += 1
        raise sqlite3.IntegrityError("boom")

    monkeypatch.setattr(history, "_connect", integrity_boom)

    rid = history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="t")

    assert rid is None
    assert calls["n"] == 1


def test_connect_closes_connection_on_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #393: если _connect падает ДО return (PRAGMA/миграция кинули),
    соединение закрывается сами, а не течёт fd — ``closing(_connect(...))`` не
    успевает обернуть объект, если само выражение бросает исключение."""
    db = _db(tmp_path)
    real_connect = history.sqlite3.connect
    closed: list[bool] = []

    class _TrackingConn:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._inner, name)

        def close(self) -> None:
            closed.append(True)
            self._inner.close()

    def _fake_connect(*args: object, **kwargs: object) -> _TrackingConn:
        return _TrackingConn(real_connect(*args, **kwargs))  # type: ignore[arg-type]

    def _boom(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(history.sqlite3, "connect", _fake_connect)
    monkeypatch.setattr(history, "_migrate", _boom)

    with pytest.raises(sqlite3.OperationalError):
        history._connect(db)

    assert closed == [True], "соединение не закрыто при сбое миграции (утечка fd)"


def test_read_recent_runs_batches_cases_and_lint_across_runs(tmp_path: Path) -> None:
    """read_recent_runs группирует cases/lint по всем прогонам без N+1 (issue #553).

    Контракт после батч-выборки (один запрос на cases, один на lint для всех
    прогонов): у каждого прогона свои cases (по case_no) и lint (по rule_code);
    прогон без cases/lint отдаёт пустые списки; порядок прогонов — id DESC.
    """
    db = _db(tmp_path)
    r1 = history.record_run(
        1,
        [CaseRecord(2, "WA"), CaseRecord(1, "OK")],
        db_path=db,
        task_key="t",
        lint=[LintRecord("E501", 3), LintRecord("E225", 1)],
    )
    r2 = history.record_run(2, [], db_path=db, task_key="t")  # без cases и lint
    r3 = history.record_run(
        1, [CaseRecord(1, "RE", error_class="ValueError")], db_path=db, task_key="t"
    )
    assert (r1, r2, r3) == (1, 2, 3)

    runs = history.read_recent_runs(db, task_key="t")
    assert [run["id"] for run in runs] == [3, 2, 1]  # id DESC

    by_id = {run["id"]: run for run in runs}
    # r1: cases по case_no (1,2), lint по rule_code (E225,E501)
    assert [c["case_no"] for c in by_id[1]["cases"]] == [1, 2]
    assert by_id[1]["lint"] == ["E225", "E501"]
    # r2: пусто (пустые списки, а не отсутствие ключей)
    assert by_id[2]["cases"] == []
    assert by_id[2]["lint"] == []
    # r3: один case, без lint
    assert [c["verdict"] for c in by_id[3]["cases"]] == ["RE"]
    assert by_id[3]["cases"][0]["error_class"] == "ValueError"
    assert by_id[3]["lint"] == []
    # контракт case-словаря: ровно 5 ключей (run_id из батч-запроса исключён)
    assert set(by_id[1]["cases"][0]) == {
        "case_no",
        "verdict",
        "time_ms",
        "error_class",
        "failure_kind",
    }


# ---------------------------------------------------------------------------
# issue #642: retention — backstop роста БД истории
# ---------------------------------------------------------------------------


def test_retention_caps_runs_per_task(tmp_path: Path) -> None:
    """max_runs_per_task оставляет только N последних прогонов на task_key (#642)."""
    db = _db(tmp_path)
    for i in range(5):
        history.record_run(
            1,
            [CaseRecord(1, "OK")],
            db_path=db,
            task_key="t",
            solution_name=f"s{i}.py",
            max_runs_per_task=3,
        )
    runs = history.read_recent_runs(db, task_key="t")
    # последние 3 (id DESC); s0/s1 вытеснены
    assert [r["solution_name"] for r in runs] == ["s4.py", "s3.py", "s2.py"]


def test_retention_cascades_to_cases_and_lint(tmp_path: Path) -> None:
    """Удержание уносит case_results/lint_violations вытесненных прогонов (#642, FK cascade)."""
    db = _db(tmp_path)
    for _ in range(4):
        history.record_run(
            1,
            [CaseRecord(1, "OK"), CaseRecord(2, "WA")],
            db_path=db,
            task_key="t",
            lint=[LintRecord("E501", 1)],
            max_runs_per_task=2,
        )
    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        # 2 оставшихся прогона × 2 case = 4; по 1 lint = 2 — без сирот
        assert conn.execute("SELECT COUNT(*) FROM case_results").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM lint_violations").fetchone()[0] == 2
        orphans = conn.execute(
            "SELECT COUNT(*) FROM case_results WHERE run_id NOT IN (SELECT id FROM runs)"
        ).fetchone()[0]
        assert orphans == 0


def test_retention_is_per_task_key(tmp_path: Path) -> None:
    """Удержание для одного task_key не трогает прогоны другого (#642)."""
    db = _db(tmp_path)
    for _ in range(4):
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="a", max_runs_per_task=2)
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="b", max_runs_per_task=2)
    assert len(history.read_recent_runs(db, task_key="a")) == 2  # обрезан до cap
    assert len(history.read_recent_runs(db, task_key="b")) == 1  # не тронут


def test_retention_disabled_when_none(tmp_path: Path) -> None:
    """max_runs_per_task=None — retention выключен, все прогоны остаются (#642)."""
    db = _db(tmp_path)
    for _ in range(6):
        history.record_run(
            1, [CaseRecord(1, "OK")], db_path=db, task_key="t", max_runs_per_task=None
        )
    assert len(history.read_recent_runs(db, task_key="t")) == 6


def test_retention_default_keeps_small_history(tmp_path: Path) -> None:
    """Дефолтный cap (_MAX_RUNS_PER_TASK) не режет обычные объёмы (#642)."""
    db = _db(tmp_path)
    assert history._MAX_RUNS_PER_TASK >= 5  # sanity: дефолт заметно больше теста
    for _ in range(5):
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db, task_key="t")
    assert len(history.read_recent_runs(db, task_key="t")) == 5
