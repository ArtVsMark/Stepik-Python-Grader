"""Tests for the SQLite/WAL missing-queue backend (issue #552, ADR-0011).

Очередь пополнения глоссария переехала с JSON на SQLite/WAL: атомарность (#551)
плюс закрытие межпроцессной гонки CLI+web через ``BEGIN IMMEDIATE`` + busy_timeout.
Здесь — SQLite-roundtrip, дедуп/merge, порядок, обратная совместимость с legacy
JSON (чтение + in-place миграция + импорт ``.json``-соседа) и главный acceptance:
конкурентная межпроцессная дозапись ничего не теряет.
"""

from __future__ import annotations

import json
import sqlite3
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
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(db_path), str(worker_id), str(per)],
            # issue #924: stderr воркера обязан доехать до сообщения падения.
            # На windows-джобе CI тест упал с «воркер завершился с ненулевым
            # кодом» — и это всё, что осталось от диагноза: причина упала в
            # /dev/null вместе с трейсбеком дочернего процесса. Локально
            # воспроизвести не удалось (120 прогонов подряд зелёные; симптом
            # повторяется только искусственным сжатием `BUSY_TIMEOUT_MS` до
            # 1 мс, где 7 воркеров из 8 падают `database is locked`). Пока
            # причина неизвестна, ценнее всего — чтобы следующий случай на CI
            # объяснил себя сам, а не потребовал третьего расследования.
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for worker_id in range(procs)
    ]
    failures: list[str] = []
    for worker_id, proc in enumerate(running):
        _, stderr = proc.communicate(timeout=120)
        if proc.returncode != 0:
            failures.append(f"воркер {worker_id}: код {proc.returncode}\n{(stderr or '').strip()}")

    assert not failures, "воркеры завершились с ненулевым кодом:\n" + "\n".join(failures)

    concepts = {e.concept for e in load_missing_queue(db_path)}
    expected = {f"w{w}.c{i}" for w in range(procs) for i in range(per)}
    assert concepts == expected, f"потеряны добавки: {sorted(expected - concepts)}"


# ---------------------------------------------------------------------------
# issue #794 (MIGR-04) — нечитаемый файл очереди: диагноз не подменяется, а
# содержимое не пропадает. Репро из issue: испортить 1-й байт заголовка и вызвать
# append_missing_entries → GlossaryError «ошибка записи SQLite-очереди — 'utf-8'
# codec can't decode byte 0x8a», хотя запись тут ни при чём. Соседняя ветка на
# декодируемом мусоре молча удаляла единственную копию файла.
# ---------------------------------------------------------------------------


def test_broken_header_does_not_leak_decode_error(tmp_path: Path) -> None:
    """Битый бинарный заголовок: очередь пересоздаётся, а не падает UnicodeDecodeError."""
    db_path = tmp_path / "q.db"
    save_missing_queue(db_path, [_entry("a")])
    raw = bytearray(db_path.read_bytes())
    raw[0] = 0x8A  # больше не SQLite-магия, и как UTF-8 не декодируется
    db_path.write_bytes(bytes(raw))

    result = append_missing_entries(db_path, [_entry("b")])

    assert {e.concept for e in result} == {"b"}
    assert db_path.read_bytes()[:16] == _SQLITE_MAGIC


def test_unreadable_queue_is_quarantined_not_deleted(tmp_path: Path, capsys) -> None:
    """Нечитаемое содержимое уезжает в ``<имя>.corrupt``, а не в никуда."""
    db_path = tmp_path / "q.db"
    db_path.write_bytes(b"\x8a\x8b\x8c " + "не декодируется".encode("cp1251"))

    append_missing_entries(db_path, [_entry("b")])

    quarantine = db_path.with_name(db_path.name + ".corrupt")
    assert quarantine.exists(), "единственная копия данных не должна пропадать молча"
    err = capsys.readouterr().err
    assert any(str(quarantine) in line for line in err.splitlines())


def test_valid_legacy_json_still_migrates_in_place(tmp_path: Path) -> None:
    """Читаемая legacy-очередь по-прежнему мигрирует без карантина (#552 не сломан)."""
    db_path = tmp_path / "q.db"
    db_path.write_text(json.dumps([_entry("a").to_dict()]), encoding="utf-8")

    result = append_missing_entries(db_path, [_entry("b")])

    assert {e.concept for e in result} == {"a", "b"}
    assert not db_path.with_name(db_path.name + ".corrupt").exists()


def test_repeated_corruption_keeps_a_single_quarantine_copy(tmp_path: Path) -> None:
    """Карантин один: иначе рядом с очередью копился бы мусор без границы."""
    db_path = tmp_path / "q.db"
    for _ in range(3):
        db_path.write_bytes(b"\x8a\xff\xfe garbage")
        append_missing_entries(db_path, [_entry("b")])

    corrupt = sorted(p.name for p in tmp_path.iterdir() if ".corrupt" in p.name)
    assert corrupt == ["q.db.corrupt"]


def test_queue_from_newer_grader_refuses_with_its_own_message(tmp_path: Path) -> None:
    """Откат версии схемы очереди — свой диагноз, а не «ошибка записи» (MIGR-01)."""
    db_path = tmp_path / "q.db"
    save_missing_queue(db_path, [_entry("a")])
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(GlossaryError) as excinfo:
        append_missing_entries(db_path, [_entry("b")])
    assert "более новой версией" in str(excinfo.value)
    assert "ошибка записи" not in str(excinfo.value)

    with pytest.raises(GlossaryError, match="более новой версией"):
        load_missing_queue(db_path)


def test_directory_path_is_never_quarantined(tmp_path: Path) -> None:
    """Путь-директория не уезжает в карантин: переименовать чужую папку мы не вправе.

    ``--missing-out`` может показывать на директорию по ошибке пользователя.
    Карантин (issue #794) переименовывает файл, и на директории ``Path.replace``
    сработал бы успешно — папка бы «исчезла», а вместо неё появилась пустая БД.
    Правильное поведение прежнее: штатная ``GlossaryError`` (её ловит coverage-CLI).
    """
    queue_dir = tmp_path / "queue_is_a_dir"
    queue_dir.mkdir()
    (queue_dir / "keep.txt").write_text("не трогать", encoding="utf-8")

    with pytest.raises(GlossaryError):
        append_missing_entries(queue_dir, [_entry("b")])

    assert queue_dir.is_dir()
    assert (queue_dir / "keep.txt").exists()
    assert not queue_dir.with_name(queue_dir.name + ".corrupt").exists()


def test_quarantine_falls_back_to_delete_when_rename_fails(tmp_path: Path) -> None:
    """Переименовать нечем (нет прав, чужая ФС) — удаляем, иначе очередь не заведётся."""
    db_path = tmp_path / "q.db"
    db_path.write_bytes(b"\x8a\xff\xfe garbage")

    def _no_replace(self: Path, target: object) -> None:
        raise OSError("нельзя переименовать")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "replace", _no_replace)
        result = append_missing_entries(db_path, [_entry("b")])

    assert {e.concept for e in result} == {"b"}
    assert not db_path.with_name(db_path.name + ".corrupt").exists()


def test_save_missing_queue_also_refuses_newer_schema(tmp_path: Path) -> None:
    """Замена очереди целиком отказывает по той же причине, что и дозапись."""
    db_path = tmp_path / "q.db"
    save_missing_queue(db_path, [_entry("a")])
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(GlossaryError, match="более новой версией"):
        save_missing_queue(db_path, [_entry("b")])
