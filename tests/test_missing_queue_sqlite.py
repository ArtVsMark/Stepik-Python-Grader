"""Tests for the SQLite/WAL missing-queue backend (issue #552, ADR-0011).

Очередь пополнения глоссария переехала с JSON на SQLite/WAL: атомарность (#551)
плюс закрытие межпроцессной гонки CLI+web через ``BEGIN IMMEDIATE`` + busy_timeout.
Здесь — SQLite-roundtrip, дедуп/merge, порядок, обратная совместимость с legacy
JSON (чтение + in-place миграция + импорт ``.json``-соседа) и главный acceptance:
конкурентная межпроцессная дозапись ничего не теряет.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stepik_grader.glossary import (
    append_missing_entries,
    load_missing_queue,
    save_missing_queue,
)
from stepik_grader.glossary.json_provider import GlossaryError
from stepik_grader.glossary.models import GlossaryMissingEntry

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _entry(concept: str, **kw: object) -> GlossaryMissingEntry:
    return GlossaryMissingEntry(concept=concept, **kw)  # type: ignore[arg-type]


def test_append_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    append_missing_entries(path, [_entry("functools.reduce", module="functools", seen_in=["a.py"])])
    loaded = load_missing_queue(path)
    assert [e.concept for e in loaded] == ["functools.reduce"]
    assert loaded[0].seen_in == ["a.py"]
    assert loaded[0].module == "functools"
    assert path.open("rb").read(16) == _SQLITE_MAGIC  # это действительно SQLite


def test_load_absent_returns_empty(tmp_path: Path) -> None:
    assert load_missing_queue(tmp_path / "nope.db") == []


def test_dedup_by_concept_merges_seen_in_and_enriches(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    append_missing_entries(path, [_entry("re.sub", seen_in=["a.py"])])
    result = append_missing_entries(
        path, [_entry("re.sub", seen_in=["b.py"], module="re", qualname="re.sub")]
    )
    assert len(result) == 1
    assert set(result[0].seen_in) == {"a.py", "b.py"}
    assert result[0].module == "re"
    assert result[0].qualname == "re.sub"


def test_insertion_order_preserved(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    append_missing_entries(path, [_entry("first")])
    append_missing_entries(path, [_entry("second")])
    append_missing_entries(path, [_entry("third")])
    assert [e.concept for e in load_missing_queue(path)] == ["first", "second", "third"]


def test_save_missing_queue_replaces_all(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    append_missing_entries(path, [_entry("a"), _entry("b")])
    save_missing_queue(path, [_entry("only")])
    assert [e.concept for e in load_missing_queue(path)] == ["only"]


def test_idempotent_reappend(tmp_path: Path) -> None:
    path = tmp_path / "q.db"
    entries = [_entry("x"), _entry("y")]
    first = append_missing_entries(path, entries)
    second = append_missing_entries(path, entries)
    assert first == second
    assert len(load_missing_queue(path)) == 2


def test_legacy_json_read_then_in_place_upgrade(tmp_path: Path) -> None:
    """Legacy JSON по пути читается, а первая запись мигрирует его в SQLite in-place."""
    path = tmp_path / "queue.db"  # .db-имя, но содержимое пока legacy JSON
    path.write_text(json.dumps([{"concept": "old.one", "seen_in": ["x.py"]}]), encoding="utf-8")
    # чтение видит legacy-содержимое
    assert [e.concept for e in load_missing_queue(path)] == ["old.one"]
    # первая запись мигрирует файл в SQLite, сохраняя старое + добавляя новое
    append_missing_entries(path, [_entry("new.one")])
    assert path.open("rb").read(16) == _SQLITE_MAGIC
    assert [e.concept for e in load_missing_queue(path)] == ["old.one", "new.one"]


def test_sibling_json_imported_once_on_fresh_db(tmp_path: Path) -> None:
    """Свежая ``.db`` разово импортирует backlog из legacy ``<stem>.json``-соседа (#552)."""
    stem = tmp_path / "queue"
    stem.with_suffix(".json").write_text(
        json.dumps([{"concept": "sib.one", "seen_in": ["s.py"]}]), encoding="utf-8"
    )
    db_path = stem.with_suffix(".db")
    append_missing_entries(db_path, [_entry("fresh.one")])
    assert [e.concept for e in load_missing_queue(db_path)] == ["sib.one", "fresh.one"]


def test_broken_sqlite_raises_glossary_error(tmp_path: Path) -> None:
    """Файл с SQLite-магией, но битым содержимым → GlossaryError (не сырой sqlite3.Error)."""
    path = tmp_path / "corrupt.db"
    path.write_bytes(_SQLITE_MAGIC + b"\x00garbage not a real database page")
    with pytest.raises(GlossaryError):
        load_missing_queue(path)


def test_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "q.db"
    append_missing_entries(path, [_entry("c")])
    assert path.exists()
    assert [e.concept for e in load_missing_queue(path)] == ["c"]


# Сниппет воркера для межпроцессного acceptance-теста: каждый процесс дозаписывает
# свой набор concept'ов в ОДНУ базу. BEGIN IMMEDIATE + busy_timeout сериализуют
# писателей между процессами — ни одна добавка не теряется.
_WORKER = """
import sys
from pathlib import Path
from stepik_grader.glossary import append_missing_entries
from stepik_grader.glossary.models import GlossaryMissingEntry

db_path = Path(sys.argv[1])
worker_id = int(sys.argv[2])
count = int(sys.argv[3])
for i in range(count):
    append_missing_entries(
        db_path, [GlossaryMissingEntry(concept=f"w{worker_id}.c{i}", seen_in=[f"{worker_id}.py"])]
    )
"""


def test_concurrent_cross_process_appends_lose_nothing(tmp_path: Path) -> None:
    """Acceptance (#552): конкурентная межпроцессная дозапись очереди ничего не теряет.

    Прежний JSON (+ только процессный ``_MISSING_QUEUE_LOCK``) терял добавки при
    одновременной записи из ДВУХ процессов (CLI + web). SQLite/WAL с
    ``BEGIN IMMEDIATE`` сериализует писателей и между процессами — все 4×15
    concept'ов доходят до базы.
    """
    db_path = tmp_path / "q.db"
    save_missing_queue(db_path, [])  # заранее создать пустую SQLite-очередь

    procs, per = 4, 15
    running = [
        subprocess.Popen([sys.executable, "-c", _WORKER, str(db_path), str(worker_id), str(per)])
        for worker_id in range(procs)
    ]
    for proc in running:
        assert proc.wait(timeout=120) == 0, "воркер завершился с ненулевым кодом"

    concepts = {e.concept for e in load_missing_queue(db_path)}
    expected = {f"w{w}.c{i}" for w in range(procs) for i in range(per)}
    assert concepts == expected, f"потеряны добавки: {sorted(expected - concepts)}"
