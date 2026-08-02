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


# ---------------------------------------------------------------------------
# issue #821: отчёт следует выбранному языку
#
# Экспорт — единственный артефакт, который пользователь показывает наружу
# (ментору, в резюме). До этого он всегда выходил русским, а `<html lang>`
# утверждал `ru` даже в английском отчёте.
# ---------------------------------------------------------------------------


def test_markdown_default_stays_russian(tmp_path: Path) -> None:
    """Без явного языка поведение прежнее — русский отчёт."""
    db = tmp_path / "h.db"
    _seed(db)
    md = progress_export.render_markdown(progress_export.build_progress_report(db))
    assert "# Прогресс Stepik-Grader" in md
    assert "## Вердикты" in md


def test_markdown_english(tmp_path: Path) -> None:
    """`lang="en"` переводит заголовки и подписи колонок."""
    db = tmp_path / "h.db"
    _seed(db)
    md = progress_export.render_markdown(progress_export.build_progress_report(db), lang="en")
    assert "# Stepik-Grader progress" in md
    assert "## Verdicts" in md
    assert "| Task | Solved | Attempts | Time to AC |" in md
    # Коды вердиктов и ключи задач — данные, не подписи: не переводятся.
    assert "`AC`" in md and "sum" in md
    assert "Прогресс" not in md and "Вердикты" not in md


def test_html_lang_attribute_follows_language(tmp_path: Path) -> None:
    """`<html lang>` перестал лгать: он совпадает с языком отчёта."""
    db = tmp_path / "h.db"
    _seed(db)
    report = progress_export.build_progress_report(db)
    assert "<html lang='ru'>" in progress_export.render_html(report)
    assert "<html lang='en'>" in progress_export.render_html(report, lang="en")


def test_html_english_has_no_cyrillic_labels(tmp_path: Path) -> None:
    """В английском HTML-отчёте не остаётся русских подписей."""
    db = tmp_path / "h.db"
    _seed(db)
    doc = progress_export.render_html(progress_export.build_progress_report(db), lang="en")
    assert "Stepik-Grader progress" in doc
    assert "Failure kinds" in doc
    # Единственная кириллица могла бы прийти из task_key — здесь его нет.
    assert not any("Ѐ" <= ch <= "ӿ" for ch in doc)


def test_empty_history_report_is_localized(tmp_path: Path) -> None:
    """Пустая история тоже переводится — и в markdown, и в html."""
    report = progress_export.build_progress_report(tmp_path / "nope.db")
    md = progress_export.render_markdown(report, lang="en")
    doc = progress_export.render_html(report, lang="en")
    assert "History is empty" in md
    assert "History is empty" in doc
    assert "<html lang='en'>" in doc


def test_unknown_language_falls_back_to_russian(tmp_path: Path) -> None:
    """Неизвестная локаль не роняет экспорт — подписи откатываются на русский."""
    db = tmp_path / "h.db"
    _seed(db)
    md = progress_export.render_markdown(progress_export.build_progress_report(db), lang="fr")
    assert "# Прогресс Stepik-Grader" in md


def test_legacy_dot_task_key_is_marked_not_shown_as_name() -> None:
    """Старые записи с ключом «.» помечаются, а не выдаются за название задачи."""
    from stepik_grader.core.progress_export import render_html, render_markdown

    report = {
        "schema": "stepik-grader/progress/1",
        "total_runs": 1,
        "total_tasks": 1,
        "solved_tasks": 1,
        "tasks": [
            {
                "task_key": ".",
                "attempts": 1,
                "solved": True,
                "total_runs": 1,
                "seconds_to_first_ac": 5.0,
            }
        ],
        "verdicts": {"AC": 1},
        "failure_kinds": {},
    }
    md = render_markdown(report)
    assert "| . |" not in md
    assert "(без задачи)" in md
    assert "(без задачи)" in render_html(report)
