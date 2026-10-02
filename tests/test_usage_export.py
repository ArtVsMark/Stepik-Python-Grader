"""Выгрузка журнала прогонов для соседних инструментов (issue #1365).

Главное свойство здесь не формат, а **граница**: экспорт отдаёт уже накопленное
и ничего не собирает сверх журнала. Поэтому первым тестом идёт закрытый список
полей — если он разойдётся с кодом, наружу поедет то, о чём в SECURITY.md не
сказано.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader.core import usage_export


def _journal(path: pathlib.Path, entries: list[object]) -> pathlib.Path:
    lines = [entry if isinstance(entry, str) else json.dumps(entry) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_export_carries_only_declared_fields(tmp_path: pathlib.Path) -> None:
    """Закрытый список полей — то, чем экспорт отличается от телеметрии."""
    journal = _journal(
        tmp_path / "stats.jsonl",
        [
            {
                "v": 1,
                "ts": 1.0,
                "mode": 1,
                "os": "Linux",
                "verdicts": {"OK": 1},
                "total_time": 0.5,
                "user": "кто-то",
                "path": "/home/user/solution.py",
            }
        ],
    )

    event = usage_export.collect_events(stats_path=journal).events[0]

    assert set(event) == {"schema", "ts", "mode", "os", "verdicts", "total_time"}
    assert "user" not in event
    assert "path" not in event


def test_every_record_names_its_schema(tmp_path: pathlib.Path) -> None:
    """Версия в каждой строке: потребитель читает файл построчно."""
    journal = _journal(
        tmp_path / "stats.jsonl",
        [{"ts": 1.0, "mode": 1}, {"ts": 2.0, "mode": 2}],
    )

    events = usage_export.collect_events(stats_path=journal).events

    assert [event["schema"] for event in events] == [usage_export.USAGE_SCHEMA] * 2


def test_broken_lines_are_skipped_and_counted(tmp_path: pathlib.Path) -> None:
    """Одна покалеченная запись не отменяет остальные — но и не исчезает молча."""
    journal = _journal(
        tmp_path / "stats.jsonl",
        [{"ts": 1.0, "mode": 1}, "}{ не json", {"ts": 2.0}, {"ts": 3.0, "mode": 3}],
    )

    result = usage_export.collect_events(stats_path=journal)

    assert len(result.events) == 2
    assert result.skipped == 2


def test_missing_journal_is_empty_not_error(tmp_path: pathlib.Path) -> None:
    """Статистика opt-in: выключенная — законное состояние, а не сбой."""
    result = usage_export.collect_events(stats_path=tmp_path / "нет.jsonl")

    assert result.events == []
    assert result.skipped == 0


def test_unreadable_journal_is_not_reported_as_empty(tmp_path: pathlib.Path) -> None:
    """«Пусто» и «прочитать не смогли» — разные ответы."""
    directory = tmp_path / "как-каталог.jsonl"
    directory.mkdir()

    result = usage_export.collect_events(stats_path=directory)

    assert result.events == []
    assert result.skipped == 1


def test_since_filters_older_records(tmp_path: pathlib.Path) -> None:
    journal = _journal(
        tmp_path / "stats.jsonl",
        [{"ts": 10.0, "mode": 1}, {"ts": 20.0, "mode": 2}, {"ts": 30.0, "mode": 3}],
    )

    events = usage_export.collect_events(stats_path=journal, since=20.0).events

    assert [event["mode"] for event in events] == [2, 3]


def test_events_are_sorted_oldest_first(tmp_path: pathlib.Path) -> None:
    journal = _journal(
        tmp_path / "stats.jsonl",
        [{"ts": 30.0, "mode": 3}, {"ts": 10.0, "mode": 1}, {"ts": 20.0, "mode": 2}],
    )

    events = usage_export.collect_events(stats_path=journal).events

    assert [event["ts"] for event in events] == [10.0, 20.0, 30.0]


def test_render_is_json_lines_with_trailing_newline(tmp_path: pathlib.Path) -> None:
    journal = _journal(tmp_path / "stats.jsonl", [{"ts": 1.0, "mode": 1}, {"ts": 2.0, "mode": 2}])

    rendered = usage_export.render_jsonl(usage_export.collect_events(stats_path=journal).events)

    assert rendered.endswith("\n")
    assert len(rendered.strip().splitlines()) == 2
    for line in rendered.strip().splitlines():
        assert json.loads(line)["schema"] == usage_export.USAGE_SCHEMA


def test_render_of_nothing_is_empty_string() -> None:
    """Пустой экспорт — пустой файл, а не строка из одного перевода."""
    assert usage_export.render_jsonl([]) == ""


def test_write_export_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    journal = _journal(tmp_path / "stats.jsonl", [{"ts": 1.0, "mode": 1}])
    destination = tmp_path / "usage" / "2026-08" / "runs.jsonl"

    result = usage_export.write_export(destination, stats_path=journal)

    assert destination.exists()
    assert len(result.events) == 1
    assert json.loads(destination.read_text(encoding="utf-8").strip())["mode"] == 1


def test_write_export_raises_when_it_cannot_write(tmp_path: pathlib.Path) -> None:
    """Команду позвали ради файла: молчаливый успех без файла — худший исход."""
    journal = _journal(tmp_path / "stats.jsonl", [{"ts": 1.0, "mode": 1}])
    occupied = tmp_path / "занято"
    occupied.mkdir()

    with pytest.raises(OSError):
        usage_export.write_export(occupied, stats_path=journal)


def test_export_never_touches_the_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Обещание SECURITY.md проверяется, а не подразумевается."""
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("экспорт полез в сеть")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    journal = _journal(tmp_path / "stats.jsonl", [{"ts": 1.0, "mode": 1}])

    usage_export.write_export(tmp_path / "out.jsonl", stats_path=journal)
