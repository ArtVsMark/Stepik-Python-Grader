"""Тесты core/history.py — SQLite-история прогонов (issue #344, эпик #342).

Покрывают Acceptance из issue: roundtrip, отсутствие БД без создания файла
(#134), graceful на битой БД, идемпотентная миграция user_version 0→1 (#135),
FK-каскад, наполнение lint_violations, фильтр по task_key и конкурентная
запись под WAL.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from stepik_grader.core import history
from stepik_grader.core.history import CaseRecord, LintRecord, RunRecord


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


# ---------------------------------------------------------------------------
# issue #639: RunRecord + абстракция репозитория (ADR-0009)
# ---------------------------------------------------------------------------


def test_run_record_defaults() -> None:
    """RunRecord: обязательны mode/cases; source/task_key/lint — дефолты (ADR-0009)."""
    rec = RunRecord(mode=1, cases=[CaseRecord(1, "OK")])
    assert rec.source == "cli"
    assert rec.task_key == ""
    assert rec.solution_name is None
    assert rec.lint == []


def test_sqlite_repository_roundtrip(tmp_path: Path) -> None:
    """SqliteHistoryRepository.add_run/recent_runs — roundtrip доменной модели RunRecord."""
    repo = history.SqliteHistoryRepository(_db(tmp_path))
    run_id = repo.add_run(
        RunRecord(
            mode=2,
            cases=[CaseRecord(1, "OK"), CaseRecord(2, "WA")],
            task_key="course/01",
            solution_name="task.py",
            lint=[LintRecord("E501", 3)],
        )
    )
    assert run_id == 1
    runs = repo.recent_runs(task_key="course/01")
    assert len(runs) == 1
    assert runs[0]["mode"] == 2
    assert runs[0]["solution_name"] == "task.py"
    assert [c["verdict"] for c in runs[0]["cases"]] == ["OK", "WA"]
    assert runs[0]["lint"] == ["E501"]


def test_record_run_wrapper_matches_repository(tmp_path: Path) -> None:
    """record_run/read_recent_runs — тонкие обёртки: тот же результат, что прямой
    repo.add_run/recent_runs (обратная совместимость сохранена, ADR-0009)."""
    db = _db(tmp_path)
    rid = history.record_run(
        1, [CaseRecord(1, "OK")], db_path=db, task_key="t", solution_name="a.py"
    )
    assert rid == 1
    via_repo = history.SqliteHistoryRepository(db).recent_runs(task_key="t")
    via_func = history.read_recent_runs(db, task_key="t")
    assert via_repo == via_func
    assert via_func[0]["solution_name"] == "a.py"


def test_sqlite_repository_satisfies_protocol(tmp_path: Path) -> None:
    """SqliteHistoryRepository удовлетворяет @runtime_checkable HistoryRepository (ADR-0009)."""
    repo = history.SqliteHistoryRepository(_db(tmp_path))
    assert isinstance(repo, history.HistoryRepository)


def test_repository_retention_applies(tmp_path: Path) -> None:
    """Retention (#642) действует и через прямой repo.add_run(max_runs_per_task=...)."""
    repo = history.SqliteHistoryRepository(_db(tmp_path))
    for _ in range(5):
        repo.add_run(
            RunRecord(mode=1, cases=[CaseRecord(1, "OK")], task_key="t"), max_runs_per_task=2
        )
    assert len(repo.recent_runs(task_key="t")) == 2


# ---------------------------------------------------------------------------
# issue #819 — агрегат task_progress: «попытки до первого зачёта» не зависят
# от retention, а бенчмарк-прогоны в метрику обучения не попадают
# ---------------------------------------------------------------------------


def test_task_progress_survives_retention(tmp_path: Path) -> None:
    """Вытеснение ранних прогонов не занижает attempts.

    Репро находки: при `--watch` каждое сохранение файла — прогон, лимит
    (200 на задачу) достигается быстро, и первая попытка удалялась. Здесь тот
    же сценарий в миниатюре — cap=2 и пять прогонов.
    """
    db = _db(tmp_path)
    for _ in range(4):
        history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="t", max_runs_per_task=2)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t", max_runs_per_task=2)

    assert len(history.read_recent_runs(db)) == 2  # retention сработал

    (progress,) = history.read_task_progress(db)
    assert progress["runs_total"] == 5
    assert progress["attempts_to_first_ac"] == 5
    assert progress["first_ac_ts_utc"] is not None


def test_task_progress_ignores_bench_runs(tmp_path: Path) -> None:
    """Прогоны режимов 3/4 не попадают в агрегат ни как попытка, ни как провал."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="t")
    history.record_run(3, [CaseRecord(1, "SIMILAR")], db_path=db, task_key="t")
    history.record_run(4, [CaseRecord(1, "SLOWER")], db_path=db, task_key="t")
    history.record_run(2, [CaseRecord(1, "AC")], db_path=db, task_key="t")

    (progress,) = history.read_task_progress(db)
    assert progress["runs_total"] == 2  # только режимы 1 и 2
    assert progress["attempts_to_first_ac"] == 2


def test_task_progress_first_ac_is_frozen(tmp_path: Path) -> None:
    """Первый зачёт фиксируется однажды: последующие прогоны его не двигают."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")
    first = history.read_task_progress(db)[0]
    history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="t")
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")

    (progress,) = history.read_task_progress(db)
    assert progress["attempts_to_first_ac"] == 1
    assert progress["first_ac_ts_utc"] == first["first_ac_ts_utc"]
    assert progress["runs_total"] == 3


def test_task_progress_absent_until_solved(tmp_path: Path) -> None:
    """Нерешённая задача есть в агрегате, но без отметки о зачёте."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "TLE")], db_path=db, task_key="t")

    (progress,) = history.read_task_progress(db)
    assert progress["attempts_to_first_ac"] is None
    assert progress["first_ac_ts_utc"] is None
    assert progress["runs_total"] == 1


def test_migration_v1_to_v2_backfills_task_progress(tmp_path: Path) -> None:
    """БД схемы v1 домигрируется до v2, агрегат восстанавливается по runs.

    Точнее, чем ничего: вытесненные ранее прогоны не вернуть, но всё, что
    осталось в таблице, попадает в агрегат — и дальше он ведётся инкрементально.
    """
    from stepik_grader import db as db_module

    db = _db(tmp_path)
    with contextlib.closing(db_module.connect(db)) as conn:
        db_module.apply_schema(conn, version=1, ddl=history._SCHEMA_V1)
        for i, (mode, verdict) in enumerate([(1, "WA"), (3, "SIMILAR"), (1, "AC")], 1):
            conn.execute(
                "INSERT INTO runs (id, ts_utc, mode, source, task_key) "
                "VALUES (?, ?, ?, 'cli', 't')",
                (i, f"2026-08-0{i}T10:00:00Z", mode),
            )
            conn.execute(
                "INSERT INTO case_results (run_id, case_no, verdict) VALUES (?, 1, ?)",
                (i, verdict),
            )
        conn.commit()

    (progress,) = history.read_task_progress(db)  # чтение домигрирует схему
    assert progress["runs_total"] == 2  # бенчмарк-прогон не в счёт
    assert progress["attempts_to_first_ac"] == 2
    assert progress["first_ts_utc"] == "2026-08-01T10:00:00Z"
    assert progress["first_ac_ts_utc"] == "2026-08-03T10:00:00Z"

    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION


def test_task_progress_repository_satisfies_protocol(tmp_path: Path) -> None:
    """Новый метод не выводит SqliteHistoryRepository из-под протокола (ADR-0009)."""
    repo = history.SqliteHistoryRepository(_db(tmp_path))
    assert isinstance(repo, history.HistoryRepository)
    assert repo.task_progress() == []  # БД нет — graceful пустой список


# ---------------------------------------------------------------------------
# issue #817 — ключ задачи не зависит от каталога запуска: «.» схлопывал разные
# задачи в одну строку «Прогресса», а запуск из tests/ давал «..»
# ---------------------------------------------------------------------------


def test_task_key_from_task_folder_is_its_name(tmp_path: Path) -> None:
    """Запуск ИЗ папки задачи (рекомендованный сценарий) даёт имя папки, не «.»."""
    task = tmp_path / "04-slug"
    task.mkdir()
    assert history.task_key_for(task, task) == "04-slug"


def test_task_key_from_parent_folder_is_relative(tmp_path: Path) -> None:
    """Запуск из родительской папки — прежний относительный путь."""
    task = tmp_path / "module1" / "04-slug"
    task.mkdir(parents=True)
    assert history.task_key_for(task, tmp_path) == str(Path("module1") / "04-slug")


def test_task_key_from_subfolder_does_not_escape(tmp_path: Path) -> None:
    """Запуск из tests/ внутри задачи даёт имя задачи, а не «..»."""
    task = tmp_path / "04-slug"
    (task / "tests").mkdir(parents=True)
    assert history.task_key_for(task, task / "tests") == "04-slug"


def test_different_tasks_get_different_keys(tmp_path: Path) -> None:
    """Две задачи, прогнанные каждая из своей папки, не схлопываются в один ключ.

    Ровно этот дефект искажал TTFG: обе задачи писались под ключом «.».
    """
    first, second = tmp_path / "01-a", tmp_path / "02-b"
    first.mkdir()
    second.mkdir()
    assert history.task_key_for(first, first) != history.task_key_for(second, second)


def test_task_key_survives_mismatched_anchors(tmp_path: Path) -> None:
    """Относительный путь против абсолютной базы — путь как есть, без падения."""
    assert history.task_key_for(Path("04-slug"), tmp_path) == "04-slug"


# ---------------------------------------------------------------------------
# Устойчивый ключ задачи — issue #990 (JRN-4A-01 / JRN-4B-02)
# ---------------------------------------------------------------------------


def _make_task(dir_path: Path, step_id: int | None) -> Path:
    """Папка задачи; с ``meta.json``, если ``step_id`` задан."""
    dir_path.mkdir(parents=True)
    if step_id is not None:
        (dir_path / "meta.json").write_text(
            json.dumps({"step_id": step_id, "lesson_id": 1}), encoding="utf-8"
        )
    return dir_path


def test_same_folder_name_in_different_courses_keeps_keys_apart(tmp_path: Path) -> None:
    """Сценарий из issue: `~/курс-А/задача-3` и `~/курс-Б/задача-3` — разные задачи.

    Пока ключом было имя папки, обе писались в одну строку истории: общий зачёт
    и общее удаление. `--purge-history задача-3` из курса Б стирал и курс А.
    """
    first = _make_task(tmp_path / "курс-А" / "задача-3", step_id=111)
    second = _make_task(tmp_path / "курс-Б" / "задача-3", step_id=222)

    assert history.task_key_for(first, first) != history.task_key_for(second, second)


def test_stable_key_survives_folder_rename(tmp_path: Path) -> None:
    """Переименование папки не заводит вторую задачу: ключ держится за шаг."""
    task = _make_task(tmp_path / "04-slug", step_id=42)
    before = history.task_key_for(task, task)

    renamed = task.rename(tmp_path / "четвёртая-задача")

    assert history.task_key_for(renamed, renamed) == before


def test_folder_without_meta_keeps_path_key(tmp_path: Path) -> None:
    """Папка, скачанная не downloader'ом, ведёт себя как раньше — по пути."""
    task = _make_task(tmp_path / "своя-задача", step_id=None)
    assert history.task_key_for(task, task) == "своя-задача"


@pytest.mark.parametrize(
    "payload",
    ["{ битый json", json.dumps({"step_id": "42"}), json.dumps({}), json.dumps([1, 2])],
)
def test_unusable_meta_falls_back_to_path(tmp_path: Path, payload: str) -> None:
    """Битый/чужой meta.json — не ошибка, а «устойчивого ключа нет»."""
    task = _make_task(tmp_path / "задача", step_id=None)
    (task / "meta.json").write_text(payload, encoding="utf-8")

    assert history.task_key_for(task, task) == "задача"


def test_display_name_is_stored_and_updated(tmp_path: Path) -> None:
    """Имя папки хранится рядом с ключом и обновляется после переименования.

    Ключ показывать нельзя — `step:42` человеку ничего не говорит, а в
    «Прогрессе» задачу ищут по названию.
    """
    db = _db(tmp_path)
    history.record_run(
        1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42", task_title="старое"
    )
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42", task_title="новое")

    rows = history.read_task_progress(db)

    assert [row["display_name"] for row in rows] == ["новое"]


def test_display_name_survives_run_without_title(tmp_path: Path) -> None:
    """Прогон без имени не обезличивает уже названную задачу."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:7", task_title="задача")

    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:7", task_title=None)

    assert history.read_task_progress(db)[0]["display_name"] == "задача"


def test_migration_v2_to_v3_adds_display_name(tmp_path: Path) -> None:
    """База схемы v2 домигрируется до v3, не потеряв накопленный агрегат.

    Ступень 2→3 обязана идти ДО заполнения агрегата: backfill пишет через
    `_bump_task_progress`, который уже знает про новую колонку. Обратный порядок
    ронял домиграцию на «no such column», и из-за best-effort это выглядело как
    молча пропавшая запись.
    """
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t", task_title="задача")
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE task_progress DROP COLUMN display_name")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

    assert history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t") is not None

    with contextlib.closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION
    assert [row["task_key"] for row in history.read_task_progress(db)] == ["t"]


def test_purge_by_step_key_leaves_the_other_course_alone(tmp_path: Path) -> None:
    """Удаление истории одной задачи не трогает одноимённую из другого курса.

    Это исходная потеря данных из #990, проверенная целиком: две записи, удаление
    по ключу одной, вторая обязана остаться.
    """
    db = tmp_path / "history.db"
    first = _make_task(tmp_path / "курс-А" / "задача-3", step_id=111)
    second = _make_task(tmp_path / "курс-Б" / "задача-3", step_id=222)
    for task in (first, second):
        history.record_run(
            1,
            [CaseRecord(1, "OK")],
            db_path=db,
            task_key=history.task_key_for(task, task),
            solution_name="solution.py",
        )

    history.purge_history(db, task_key=history.task_key_for(first, first))

    survivors = history.read_recent_runs(db)
    assert [run["task_key"] for run in survivors] == [history.task_key_for(second, second)]


# ---------------------------------------------------------------------------
# Удаление истории — issue #813 (SECD-03)
# ---------------------------------------------------------------------------


class TestPurgeHistory:
    """У локального журнала обучения есть штатный способ удаления.

    Раньше `--no-history` лишь переставал писать, а убрать накопленное можно
    было только `rm .grader_history.db` вместе с `-wal`/`-shm` — то есть зная о
    файлах, которых пользователь не создавал и в документации не видел.
    """

    def _seed(self, db_path: Path, tasks: tuple[str, ...]) -> None:
        for task in tasks:
            history.record_run(
                2, [], db_path=db_path, task_key=task, solution_name="s.py", solution_hash="h"
            )

    def test_purge_all_removes_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".grader_history.db"
        self._seed(db_path, ("alpha", "beta"))

        removed = history.purge_history(db_path)

        assert removed == 2
        assert not db_path.exists()

    def test_purge_targets_wal_sidecars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Удаляется не только `.db`, но и `-wal`/`-shm`: в журнале те же данные.

        Проверяется намерение кода (какие пути он трогает), а не итоговое
        содержимое каталога: на штатном пути SQLite сливает журнал и убирает
        спутники сам при закрытии соединения, поэтому «каталог пуст» проходит и
        без их явного удаления. Остаточные спутники реальны — их оставляет
        аварийно завершённый процесс, и тогда журнал переживает «удаление».
        """
        db_path = tmp_path / ".grader_history.db"
        self._seed(db_path, ("alpha",))

        unlinked: list[str] = []
        real_unlink = Path.unlink

        def _spy(self: Path, **kwargs: object) -> None:
            unlinked.append(self.name)
            real_unlink(self, **kwargs)

        monkeypatch.setattr(Path, "unlink", _spy)
        history.purge_history(db_path)

        assert unlinked == [
            ".grader_history.db",
            ".grader_history.db-wal",
            ".grader_history.db-shm",
        ]
        assert list(tmp_path.iterdir()) == []

    def test_purge_single_task_keeps_others(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".grader_history.db"
        self._seed(db_path, ("alpha", "alpha", "beta"))

        removed = history.purge_history(db_path, task_key="alpha")

        assert removed == 2
        assert db_path.is_file()  # выборочное удаление базу не сносит
        left = history.read_recent_runs(db_path)
        assert [r["task_key"] for r in left] == ["beta"]

    def test_purge_missing_db_is_zero_not_error(self, tmp_path: Path) -> None:
        """Best-effort, как и вся работа с историей: нечего удалять — 0."""
        assert history.purge_history(tmp_path / "nope.db") == 0

    def test_purge_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".grader_history.db"
        self._seed(db_path, ("alpha",))
        assert history.purge_history(db_path) == 1
        assert history.purge_history(db_path) == 0


def test_purge_by_task_clears_progress_aggregate(tmp_path: Path) -> None:
    """Удаление прогонов задачи уносит и агрегат «до первого зачёта».

    Агрегат (issue #819) живёт отдельной таблицей и каскадом FK не удаляется —
    без явной чистки «историю удалили» оставляло бы число попыток и время
    первого зачёта лежать в базе (issue #813).
    """
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="a")
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="b")

    history.purge_history(db, task_key="a")

    remaining = {row["task_key"] for row in history.read_task_progress(db)}
    assert remaining == {"b"}


def test_purge_all_removes_progress_aggregate_with_the_file(tmp_path: Path) -> None:
    """Полное удаление уносит базу целиком — агрегата тоже не остаётся."""
    db = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="a")

    history.purge_history(db)

    assert not db.exists()
    assert history.read_task_progress(db) == []


# ---------------------------------------------------------------------------
# issue #794 (MIGR-01/MIGR-03) — непригодная БД называется вслух. До фикса
# повреждённая база выглядела ровно как «истории нет»: recent_runs() → [],
# add_run() → None на каждом прогоне, и пользователь писал в никуда бесконечно.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_warned_paths() -> None:
    """Дедуп предупреждений процессный — без сброса тесты влияют друг на друга."""
    history._WARNED_DB_PROBLEMS.clear()
    yield
    history._WARNED_DB_PROBLEMS.clear()


def _corrupt_db(tmp_path: Path) -> Path:
    """Живая БД, обрезанная до 1/3 длины — репро из issue."""
    path = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")
    raw = path.read_bytes()
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    path.write_bytes(raw[: len(raw) // 3])
    return path


def test_corrupt_db_is_reported_not_silently_empty(tmp_path: Path, capsys) -> None:
    """Битая БД: чтение по-прежнему пустое, но пользователь слышит почему."""
    path = _corrupt_db(tmp_path)

    assert history.read_recent_runs(path) == []

    err = capsys.readouterr().err
    # Путь целиком в ОДНОЙ строке: без soft_wrap rich переносит его по ширине 80,
    # и подсказка «удалите файл» перестаёт называть файл, который надо удалить.
    assert any(str(path) in line for line in err.splitlines())
    assert "Удалите" in err


def test_corrupt_db_reported_once_per_process(tmp_path: Path, capsys) -> None:
    """Папка из 40 кейсов не должна давать 40 одинаковых абзацев."""
    path = _corrupt_db(tmp_path)

    for _ in range(3):
        history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")
        history.read_recent_runs(path)

    assert capsys.readouterr().err.count("История прогонов недоступна") == 1


def test_transient_lock_stays_silent(tmp_path: Path, capsys) -> None:
    """Транзиентная блокировка не печатает ничего — её лечит повтор, шум лишний."""
    path = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")

    def _busy(_db_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(history, "_connect", _busy)
        mp.setattr(history, "_WRITE_RETRY_DELAY_S", 0)
        assert history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t") is None

    assert capsys.readouterr().err == ""


def test_rolled_back_grader_refuses_future_schema(tmp_path: Path, capsys) -> None:
    """БД от более новой версии: запись пропускается, причина названа (MIGR-01).

    Репро — откат грейдера: база осталась на схеме vN+1, код понимает vN. Раньше
    ``apply_schema`` молча выходил, и старый код писал по чужой схеме.
    """
    path = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute(f"PRAGMA user_version = {history.SCHEMA_VERSION + 1}")
        conn.commit()

    assert history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t") is None
    assert history.read_recent_runs(path) == []
    assert history.read_task_progress(path) == []

    err = capsys.readouterr().err
    assert "более новой версией" in err
    assert str(path) in err


def test_future_schema_database_is_left_untouched(tmp_path: Path) -> None:
    """Отказ не портит данные: чужая схема и её записи остаются как были."""
    path = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")
    future = history.SCHEMA_VERSION + 1
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute(f"PRAGMA user_version = {future}")
        conn.commit()

    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")

    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == future
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_migration_still_upgrades_v1_database(tmp_path: Path) -> None:
    """Ступень v1→v2 не сломана защитой от отката: старая база домигрируется."""
    path = _db(tmp_path)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t")
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE task_progress")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    assert history.record_run(1, [CaseRecord(1, "AC")], db_path=path, task_key="t") is not None

    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == history.SCHEMA_VERSION
    assert [row["task_key"] for row in history.read_task_progress(path)] == ["t"]


class TestTaskKeyWithRelativePaths:
    """issue #818: ключ задачи при относительном `--file`.

    `task_key_for` сравнивала пути ДО резолва: относительный `task.py` из папки
    задачи давал `task_dir == "."`, ветка «разные anchor'ы» ловила ValueError и
    возвращала ту же точку — фикс #817 до неё не доходил. Пока база истории
    лежала в каждой папке отдельно, это было почти незаметно; с единой базой
    все задачи схлопнулись в одну строку «Прогресса».
    """

    def test_relative_dot_resolves_to_folder_name(self, tmp_path, monkeypatch) -> None:
        task = tmp_path / "task-a"
        task.mkdir()
        monkeypatch.chdir(task)
        # Ровно то, что приходит из `--file task.py`: solution.parent == Path(".")
        assert history.task_key_for(Path("."), Path.cwd()) == "task-a"

    def test_relative_subdir_still_relative(self, tmp_path, monkeypatch) -> None:
        """Запуск из корня курса по-прежнему даёт путь внутрь, а не имя папки."""
        course = tmp_path / "course"
        task = course / "module1" / "04-slug"
        task.mkdir(parents=True)
        monkeypatch.chdir(course)
        key = history.task_key_for(Path("module1/04-slug"), Path.cwd())
        assert key.replace("\\", "/") == "module1/04-slug"

    def test_two_sibling_tasks_get_distinct_keys(self, tmp_path, monkeypatch) -> None:
        """Соседние задачи не схлопываются — иначе единая база хуже раздельных."""
        keys = []
        for name in ("task-a", "task-b"):
            folder = tmp_path / name
            folder.mkdir()
            monkeypatch.chdir(folder)
            keys.append(history.task_key_for(Path("."), Path.cwd()))
        assert keys == ["task-a", "task-b"]


def test_record_run_creates_missing_parent_dir(tmp_path) -> None:
    """issue #818: каталог базы создаётся сам.

    С единой пользовательской историей путь стал `~/.stepik-grader/history.db`,
    а `sqlite3.connect` не создаёт промежуточные каталоги — падал «unable to
    open database file». Поймано прогоном: база не создавалась вовсе, разделы
    «Подучить»/«Прогресс» молча оставались пустыми. Раньше база всегда лежала
    в существующей cwd, поэтому проблема не проявлялась.
    """
    db = tmp_path / "nested" / "deep" / "history.db"
    assert not db.parent.exists()

    run_id = history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")

    assert run_id is not None
    assert db.is_file()


def test_relative_task_dir_is_resolved_against_base_not_cwd(tmp_path, monkeypatch) -> None:
    """issue #818: относительный путь задачи считается ОТ BASE, а не от cwd.

    Ошибка, которую этот тест ловит: резолв обоих путей от текущей папки. На
    CI рабочая копия и tmp лежат на разных дисках Windows, и такой резолв
    возвращал абсолютный путь вместо ожидаемого имени — три windows-job'а
    покраснели, а локально (один диск) всё проходило. Здесь диски ни при чём:
    cwd намеренно уведена в сторону от base.
    """
    base = tmp_path / "course"
    (base / "module1" / "04-slug").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # cwd НЕ совпадает с base

    # Вложенный путь обязателен: на одноимённой папке обе ветки случайно дают
    # «04-slug» (резолв от cwd уходит в «..», и берётся имя папки), и мутация
    # «резолвить от cwd» проходила бы незамеченной.
    key = history.task_key_for(Path("module1/04-slug"), base)

    assert key.replace("\\", "/") == "module1/04-slug"
