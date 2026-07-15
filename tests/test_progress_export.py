"""Tests for core/progress_export.py — экспорт прогресса в md/html (issue #432).

Проверяем: агрегат содержит только тали/TTFG (без исходников), рендер md/html
самодостаточен, пустая история → дружелюбный отчёт (не ошибка).
"""

from __future__ import annotations

from pathlib import Path

from stepik_grader.core import history, progress_export
from stepik_grader.core.history import CaseRecord


def _seed(db: Path) -> None:
    history.record_run(
        1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db, task_key="sum"
    )
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="sum")


def test_build_report_aggregates_only(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    _seed(db)
    report = progress_export.build_progress_report(db)
    assert report["schema"] == progress_export.SCHEMA
    assert report["total_runs"] == 2
    assert report["total_tasks"] == 1
    assert report["solved_tasks"] == 1
    assert report["verdicts"] == {"AC": 1, "WA": 1}
    assert report["failure_kinds"] == {"wrong-answer": 1}
    assert report["tasks"][0]["task_key"] == "sum"
    assert report["tasks"][0]["solved"] is True
    # Никаких исходников/кода решения в отчёте.
    assert "source" not in report
    assert all("source" not in t and "solution_hash" not in t for t in report["tasks"])


def test_build_report_empty_history(tmp_path: Path) -> None:
    report = progress_export.build_progress_report(tmp_path / "nope.db")
    assert report["total_runs"] == 0
    assert report["tasks"] == []


def test_render_markdown_contains_aggregates(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    _seed(db)
    md = progress_export.render_markdown(progress_export.build_progress_report(db))
    assert "# Прогресс Stepik-Grader" in md
    assert "sum" in md
    assert "`AC`: 1" in md
    assert "`wrong-answer`: 1" in md


def test_render_markdown_empty_is_friendly(tmp_path: Path) -> None:
    md = progress_export.render_markdown(progress_export.build_progress_report(tmp_path / "x.db"))
    assert "История пуста" in md


def test_render_html_self_contained_and_escaped(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    # task_key со спецсимволом — проверяем экранирование.
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="a<b>&c")
    htmlout = progress_export.render_html(progress_export.build_progress_report(db))
    assert htmlout.startswith("<!doctype html>")
    assert "<style>" in htmlout  # инлайн-стиль, без внешних ресурсов
    assert "a&lt;b&gt;&amp;c" in htmlout  # экранировано
    assert "a<b>" not in htmlout


def test_render_html_empty_is_friendly(tmp_path: Path) -> None:
    htmlout = progress_export.render_html(progress_export.build_progress_report(tmp_path / "x.db"))
    assert "История пуста" in htmlout
