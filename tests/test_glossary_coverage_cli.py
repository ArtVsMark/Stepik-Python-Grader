"""Smoke tests for the coverage scan CLI entrypoint (issue #198).

Покрывает ``python -m stepik_grader.glossary.coverage``: печать сводки
покрытия по категориям, запись missing-очереди (SQLite/WAL, issue #552),
обработку невалидного пути к базе карточек. Расчёт покрытия/пробелов уже покрыт
``tests/test_glossary_coverage.py`` — здесь только CLI-обвязка.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.glossary import load_missing_queue
from stepik_grader.glossary.coverage import build_coverage_report, format_report_summary, main
from stepik_grader.glossary.stdlib_inventory import build_stdlib_inventory

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "docs" / "examples" / "glossary.sample.json"


def test_format_report_summary_lists_all_categories_and_total() -> None:
    report = build_coverage_report(build_stdlib_inventory(), known=set())
    summary = format_report_summary(report)
    assert "builtins" in summary
    assert "exceptions" in summary
    assert "stdlib" in summary
    assert "total" in summary
    assert "missing" in summary


def test_main_smoke_prints_summary_without_cards(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    out = capsys.readouterr().out
    assert "coverage" in out.lower()
    assert "missing" in out.lower()


def test_main_with_cards_reduces_reported_missing(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    without_cards = capsys.readouterr().out

    main(["--cards", str(SAMPLE_FIXTURE)])
    with_cards = capsys.readouterr().out

    def _total_missing(output: str) -> int:
        line = next(ln for ln in output.splitlines() if ln.strip().startswith("total"))
        return int(line.split("covered, ")[1].split(" missing")[0])

    assert _total_missing(with_cards) <= _total_missing(without_cards)


def test_main_writes_missing_queue(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "missing.db"
    main(["--missing-out", str(out_path)])
    assert out_path.exists()
    entries = load_missing_queue(out_path)
    assert len(entries) > 0
    assert all(entry.origin == "stdlib_scan" for entry in entries)


def test_main_writing_missing_queue_twice_is_idempotent(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "missing.db"
    main(["--missing-out", str(out_path)])
    first = load_missing_queue(out_path)
    main(["--missing-out", str(out_path)])
    second = load_missing_queue(out_path)
    assert len(first) == len(second)


def test_main_restricts_modules_via_flag(tmp_path: pathlib.Path) -> None:
    # Ограничение --modules влияет только на не-exception сущности курируемых
    # модулей (kind function/class); exceptions собираются рекурсивным обходом
    # BaseException по ВСЕМ уже загруженным в процессе классам (см.
    # stdlib_inventory.py), поэтому их модули отфильтровываем из проверки.
    out_path = tmp_path / "missing.db"
    main(["--modules", "math", "--missing-out", str(out_path)])
    entries = load_missing_queue(out_path)
    non_exception_modules = {
        entry.module
        for entry in entries
        if entry.kind != "exception" and entry.module != "builtins"
    }
    assert non_exception_modules == {"math"}


def test_main_invalid_cards_path_exits_with_error() -> None:
    with pytest.raises(SystemExit):
        main(["--cards", "/definitely/does/not/exist.json"])


def test_main_unwritable_queue_warns_not_crashes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Незаписываемая очередь пополнения не роняет coverage-CLI (issue #551/#552).

    Путь-директория вместо файла: ``sqlite3`` не откроет её как БД →
    ``append_missing_entries`` заворачивает ошибку в ``GlossaryError``. CLI ловит
    её (и ``OSError``), печатает предупреждение и завершается штатно — сводка
    покрытия уже напечатана, скан не должен падать из-за неисправного backlog.
    """
    out_path = tmp_path / "queue_is_a_dir"
    out_path.mkdir()

    main(["--modules", "math", "--missing-out", str(out_path)])  # без исключения

    out = capsys.readouterr().out
    # "Warning" стоит в начале строки предупреждения — rich-перенос по ширине
    # консоли (CI не-TTY → 80) его не рвёт.
    assert "Warning" in out
