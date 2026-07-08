"""Tests for the stdlib coverage report / missing-entry generator (issue #197).

Покрывает ``stepik_grader.glossary.coverage``: сопоставление офлайн-инвентаря
stdlib (``stdlib_inventory``) с известными терминами локальной базы карточек и
генерацию ``GlossaryMissingEntry(origin="stdlib_scan")`` для очереди пополнения.
"""

from __future__ import annotations

import pathlib

from stepik_grader.glossary import (
    CATEGORIES,
    StdlibItem,
    append_missing_entries,
    build_coverage_report,
    load_missing_queue,
    missing_entries_from_inventory,
)

_INVENTORY = [
    StdlibItem(qualname="ValueError", module="builtins", kind="exception", python_version="3.14"),
    StdlibItem(qualname="map", module="builtins", kind="class", python_version="3.14"),
    StdlibItem(
        qualname="functools.reduce", module="functools", kind="function", python_version="3.14"
    ),
    StdlibItem(
        qualname="dataclasses.dataclass",
        module="dataclasses",
        kind="function",
        python_version="3.14",
    ),
    StdlibItem(
        qualname="json.JSONDecodeError", module="json", kind="exception", python_version="3.14"
    ),
]


def test_missing_entries_skip_known_by_qualname_or_tail() -> None:
    known = {"valueerror", "reduce"}  # "reduce" — известный алиас-хвост
    entries = missing_entries_from_inventory(_INVENTORY, known=known, today="2026-07-08")
    concepts = {e.concept for e in entries}
    assert "ValueError" not in concepts
    assert "functools.reduce" not in concepts
    assert "map" in concepts
    assert "dataclasses.dataclass" in concepts
    assert "json.JSONDecodeError" in concepts


def test_missing_entries_have_stdlib_scan_origin_and_fields() -> None:
    entries = missing_entries_from_inventory(_INVENTORY, known=set(), today="2026-07-08")
    by_concept = {e.concept: e for e in entries}
    reduce_entry = by_concept["functools.reduce"]
    assert reduce_entry.origin == "stdlib_scan"
    assert reduce_entry.module == "functools"
    assert reduce_entry.qualname == "functools.reduce"
    assert reduce_entry.status == "new"
    assert reduce_entry.first_seen == "2026-07-08"
    assert by_concept["map"].kind == "class"
    assert by_concept["ValueError"].kind == "exception"


def test_missing_entries_are_deterministic() -> None:
    first = missing_entries_from_inventory(_INVENTORY, known=set(), today="2026-07-08")
    second = missing_entries_from_inventory(_INVENTORY, known=set(), today="2026-07-08")
    assert first == second


def test_missing_entries_default_today_when_not_passed() -> None:
    entries = missing_entries_from_inventory(_INVENTORY, known=set())
    assert all(e.first_seen for e in entries)


def test_coverage_report_counts_known_and_missing() -> None:
    known = {"valueerror"}  # только ValueError известен
    report = build_coverage_report(_INVENTORY, known=known, today="2026-07-08")
    assert set(report.categories) == set(CATEGORIES)

    exceptions = report.categories["exceptions"]
    assert exceptions.total == 2  # ValueError, json.JSONDecodeError
    assert exceptions.covered == 1
    assert exceptions.missing == ("json.JSONDecodeError",)

    builtins_cat = report.categories["builtins"]
    assert builtins_cat.total == 1  # map (ValueError отнесён к exceptions)
    assert builtins_cat.covered == 0
    assert builtins_cat.missing == ("map",)

    stdlib_cat = report.categories["stdlib"]
    assert stdlib_cat.total == 2  # functools.reduce, dataclasses.dataclass
    assert stdlib_cat.covered == 0
    assert set(stdlib_cat.missing) == {"functools.reduce", "dataclasses.dataclass"}

    assert report.total == 5
    assert report.total_missing == 4
    assert report.python_version == "3.14"


def test_coverage_report_full_coverage_has_zero_missing() -> None:
    known = {item.qualname.lower() for item in _INVENTORY}
    report = build_coverage_report(_INVENTORY, known=known)
    assert report.total_missing == 0
    for category in report.categories.values():
        assert category.missing == ()
        assert category.ratio == 1.0


def test_coverage_report_empty_inventory() -> None:
    report = build_coverage_report([], known=set())
    assert report.total == 0
    assert report.total_missing == 0
    assert report.python_version == ""
    for category in report.categories.values():
        assert category.ratio == 1.0


def test_missing_entries_write_is_idempotent(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "missing.json"
    entries = missing_entries_from_inventory(_INVENTORY, known=set(), today="2026-07-08")

    first = append_missing_entries(path, entries)
    second = append_missing_entries(path, entries)  # повторный запуск скана

    assert first == second
    assert len(second) == len(entries)
    on_disk = load_missing_queue(path)
    assert len(on_disk) == len(entries)
    assert {e.concept for e in on_disk} == {e.concept for e in entries}
