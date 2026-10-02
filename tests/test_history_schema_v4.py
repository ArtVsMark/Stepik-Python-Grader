"""Схема истории v4: вердикт платформы, изоляция, глоссарий (issue #1175/#1220).

Три вещи проверяются здесь и нигде больше.

**Расхождение считается при записи.** Наш вердикт — величина изменчивая:
следующий прогон перепишет `runs`, retention вытеснит старые записи. Вопрос же
задаётся про момент отправки, поэтому флаг обязан пережить и то, и другое.

**Связь с прогоном рвётся, факт остаётся.** У `stepik_submissions.run_id`
намеренно `ON DELETE SET NULL`, а не каскад: вердикт платформы невосстановим
(повторно его взять неоткуда), а наш прогон воспроизводится запуском.

**«Неизвестно» — не «без изоляции».** Записи до появления колонки читаются как
`NULL`, и приписывать им «прогон шёл без песочницы» нельзя: этого никто не
измерял.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

import pytest

from stepik_grader.core import history
from stepik_grader.core.history import (
    CaseRecord,
    GlossaryHitRecord,
    SqliteHistoryRepository,
    StepikSubmissionRecord,
)


def _db(tmp_path: Path) -> Path:
    return tmp_path / history.HISTORY_DB_NAME


def _v3_database(db: Path) -> None:
    """Свести уже созданную базу к состоянию схемы v3 (до issue #1175/#1220)."""
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP TABLE IF EXISTS stepik_submissions")
        conn.execute("DROP TABLE IF EXISTS glossary_hits")
        conn.execute("ALTER TABLE runs DROP COLUMN isolation")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()


class TestMigration:
    def test_schema_version_is_four(self) -> None:
        assert history.SCHEMA_VERSION == 4

    def test_v3_database_upgrades_without_losing_runs(self, tmp_path: Path) -> None:
        """Существующая база мигрирует, а накопленные прогоны остаются на месте."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t", task_title="задача")
        _v3_database(db)

        assert history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t") is not None

        with contextlib.closing(sqlite3.connect(db)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert len(history.read_recent_runs(db)) == 2
        assert [row["task_key"] for row in history.read_task_progress(db)] == ["t"]

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Повторное открытие базы не роняет «duplicate column»/«table exists»."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")
        _v3_database(db)
        for _ in range(3):
            assert history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")

    def test_parallel_upgrade_does_not_break_either_process(self, tmp_path: Path) -> None:
        """`ADD COLUMN` не идемпотентен — гонка двух домиграций ловится по факту.

        Без проверки наличия колонки второй писатель падал бы на «duplicate
        column name», а из-за best-effort это выглядело бы как молча пропавшая
        запись, а не как ошибка.
        """
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")
        _v3_database(db)

        results: list[int | None] = []
        barrier = threading.Barrier(4)

        def writer() -> None:
            barrier.wait()
            results.append(history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t"))

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(run_id is not None for run_id in results)

    def test_newer_database_is_left_untouched(self, tmp_path: Path) -> None:
        """БД версии 5 кодом версии 4 не читается и НЕ правится.

        Внутри поднимается ``SchemaTooNewError``; наружу — пустой результат и
        названная вслух причина (best-effort всего модуля). Важно здесь второе:
        данные более новой схемы остаются на месте, а не переписываются.
        """
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("PRAGMA user_version = 5")
            conn.commit()

        assert history.read_recent_runs(db) == []
        with contextlib.closing(sqlite3.connect(db)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


class TestIsolationColumn:
    def test_value_is_stored(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t", isolation="bwrap")

        assert history.read_recent_runs(db)[0]["isolation"] == "bwrap"

    def test_absent_value_is_unknown_not_none_backend(self, tmp_path: Path) -> None:
        """`NULL` — «неизвестно». Отличается от `ISOLATION_NONE` намеренно."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")

        assert history.read_recent_runs(db)[0]["isolation"] is None
        assert history.ISOLATION_NONE == "none"

    def test_old_rows_stay_unknown_after_migration(self, tmp_path: Path) -> None:
        """Домиграция не приписывает прошлым прогонам «без изоляции»."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="t")
        _v3_database(db)
        history.record_run(
            1, [CaseRecord(1, "AC")], db_path=db, task_key="t", isolation=history.ISOLATION_NONE
        )

        rows = history.read_recent_runs(db)
        assert rows[0]["isolation"] == "none"
        assert rows[1]["isolation"] is None


class TestStepikVerdict:
    def test_verdict_is_stored_and_read_back(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_stepik_submission(
            42, "correct", db_path=db, task_key="step:42", submission_id=7, hint="ок"
        )
        rows = history.read_stepik_submissions(db)

        assert len(rows) == 1
        assert rows[0]["step_id"] == 42
        assert rows[0]["verdict"] == "correct"
        assert rows[0]["submission_id"] == 7
        assert rows[0]["hint"] == "ок"

    def test_without_local_run_run_id_is_null(self, tmp_path: Path) -> None:
        """Отправить решение можно и не запуская проверку локально."""
        db = _db(tmp_path)
        history.record_stepik_submission(42, "wrong", db_path=db, task_key="step:42")
        row = history.read_stepik_submissions(db)[0]

        assert row["run_id"] is None
        assert row["our_verdict"] is None
        assert row["diverged"] == 0

    @pytest.mark.parametrize(
        ("our_cases", "platform", "expected"),
        [
            ([CaseRecord(1, "AC")], "correct", 0),
            ([CaseRecord(1, "WA")], "wrong", 0),
            ([CaseRecord(1, "AC")], "wrong", 1),
            ([CaseRecord(1, "WA")], "correct", 1),
        ],
    )
    def test_divergence_is_detected_both_ways(
        self, tmp_path: Path, our_cases: list[CaseRecord], platform: str, expected: int
    ) -> None:
        """«Наш AC / платформа wrong» и обратное — оба расхождения."""
        db = _db(tmp_path)
        history.record_run(1, our_cases, db_path=db, task_key="step:42")
        history.record_stepik_submission(42, platform, db_path=db, task_key="step:42")

        assert history.read_stepik_submissions(db)[0]["diverged"] == expected

    def test_our_verdict_names_the_failing_case(self, tmp_path: Path) -> None:
        """Сохраняем не «не AC», а код первого упавшего: он объясняет несогласие."""
        db = _db(tmp_path)
        history.record_run(
            1, [CaseRecord(1, "AC"), CaseRecord(2, "RE")], db_path=db, task_key="step:42"
        )
        history.record_stepik_submission(42, "correct", db_path=db, task_key="step:42")

        assert history.read_stepik_submissions(db)[0]["our_verdict"] == "RE"

    def test_benchmark_run_is_not_our_verdict(self, tmp_path: Path) -> None:
        """Режимы 3/4 отвечают «быстрее ли», сравнивать их с «зачтено» нельзя."""
        db = _db(tmp_path)
        history.record_run(3, [CaseRecord(1, "SLOWER")], db_path=db, task_key="step:42")
        history.record_stepik_submission(42, "correct", db_path=db, task_key="step:42")

        assert history.read_stepik_submissions(db)[0]["our_verdict"] is None

    def test_flag_frozen_at_write_time(self, tmp_path: Path) -> None:
        """Более поздний зачёт не задним числом «мирит» нас с платформой."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="step:42")
        history.record_stepik_submission(42, "correct", db_path=db, task_key="step:42")
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42")

        assert history.read_stepik_submissions(db)[0]["diverged"] == 1

    def test_verdict_survives_retention_of_its_run(self, tmp_path: Path) -> None:
        """Каскад унёс бы невосстановимое: вердикт платформы заново не получить."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42")
        history.record_stepik_submission(42, "wrong", db_path=db, task_key="step:42")
        for _ in range(3):
            history.record_run(
                1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42", max_runs_per_task=1
            )

        row = history.read_stepik_submissions(db)[0]
        assert row["verdict"] == "wrong"
        assert row["run_id"] is None
        assert row["diverged"] == 1

    def test_diverged_only_filters(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:1")
        history.record_stepik_submission(1, "correct", db_path=db, task_key="step:1")
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:2")
        history.record_stepik_submission(2, "wrong", db_path=db, task_key="step:2")

        rows = history.read_stepik_submissions(db, diverged_only=True)
        assert [row["step_id"] for row in rows] == [2]

    def test_filter_by_step(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_stepik_submission(1, "correct", db_path=db)
        history.record_stepik_submission(2, "wrong", db_path=db)

        assert [r["step_id"] for r in history.read_stepik_submissions(db, step_id=2)] == [2]

    def test_missing_database_reads_empty_without_creating_it(self, tmp_path: Path) -> None:
        db = _db(tmp_path)

        assert history.read_stepik_submissions(db) == []
        assert not db.exists()

    def test_purge_by_task_removes_the_verdict(self, tmp_path: Path) -> None:
        """«Историю по задаче удалили» обязано означать всю историю по задаче."""
        db = _db(tmp_path)
        history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="step:42")
        history.record_stepik_submission(42, "wrong", db_path=db, task_key="step:42")
        history.record_stepik_submission(7, "wrong", db_path=db, task_key="step:7")

        history.purge_history(db, task_key="step:42")

        assert [r["step_id"] for r in history.read_stepik_submissions(db)] == [7]


class TestGlossaryHits:
    def test_hit_is_stored_with_the_error_key(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_glossary_hit(
            "indexerror",
            db_path=db,
            failure_kind="runtime-error:IndexError",
            error_class="IndexError",
        )
        rows = history.read_glossary_hits(db)

        assert len(rows) == 1
        assert rows[0]["card_id"] == "indexerror"
        assert rows[0]["failure_kind"] == "runtime-error:IndexError"
        assert rows[0]["error_class"] == "IndexError"

    def test_hit_without_taxonomy_is_still_recorded(self, tmp_path: Path) -> None:
        """Подсказка есть, таксономия не сработала — событие всё равно ценно."""
        db = _db(tmp_path)
        history.record_glossary_hit("keyerror", db_path=db)
        row = history.read_glossary_hits(db)[0]

        assert row["failure_kind"] is None
        assert row["error_class"] is None

    def test_newest_first(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        history.record_glossary_hit("a", db_path=db)
        history.record_glossary_hit("b", db_path=db)

        assert [row["card_id"] for row in history.read_glossary_hits(db)] == ["b", "a"]

    def test_missing_database_reads_empty_without_creating_it(self, tmp_path: Path) -> None:
        db = _db(tmp_path)

        assert history.read_glossary_hits(db) == []
        assert not db.exists()

    def test_hits_go_away_with_the_whole_database(self, tmp_path: Path) -> None:
        """У события нет задачи — точечно его не удалить, только вместе с базой."""
        db = _db(tmp_path)
        history.record_glossary_hit("indexerror", db_path=db)

        history.purge_history(db)

        assert history.read_glossary_hits(db) == []


class TestRepositoryProtocol:
    def test_sqlite_backend_still_satisfies_it(self, tmp_path: Path) -> None:
        """Новые методы объявлены в протоколе — серверный бэкенд встанет рядом."""
        assert isinstance(SqliteHistoryRepository(_db(tmp_path)), history.HistoryRepository)

    def test_records_have_forgiving_defaults(self) -> None:
        submission = StepikSubmissionRecord(step_id=1, verdict="correct")
        hit = GlossaryHitRecord(card_id="indexerror")

        assert (submission.task_key, submission.run_id, submission.our_verdict) == ("", None, None)
        assert (hit.failure_kind, hit.error_class) == (None, None)

    def test_corrupt_database_is_graceful(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        db.write_bytes(b"not a database at all")

        assert history.read_stepik_submissions(db) == []
        assert history.read_glossary_hits(db) == []
        assert history.record_glossary_hit("x", db_path=db) is None
        assert history.record_stepik_submission(1, "correct", db_path=db) is None
