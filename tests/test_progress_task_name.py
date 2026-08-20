"""«Прогресс» показывает название задачи, а не идентификатор шага (issue #1212).

С #990 `task_key` — устойчивый идентификатор (`step:<id>`), а не путь: имя папки
в ключ не годится, потому что `~/курс-А/задача-3` и `~/курс-Б/задача-3`
схлопывались в одну строку. Тогда же завели колонку `display_name`, чтобы
человеку показывать имя, а не номер.

Колонка заполняется каждым прогоном — но до экрана не доезжала: агрегат
`build_progress_report` собирал строки задач вручную и это поле не переносил.
Больше месяца «Прогресс» и экспорт для ментора показывали номер шага.

Ключ при этом обязан остаться доступным: без него две одноимённые папки из
разных курсов снова становятся неразличимы — ровно то, от чего спасал #990.
"""

from __future__ import annotations

import pathlib

from stepik_grader.core import history, progress_export
from stepik_grader.core.history import CaseRecord
from stepik_grader.web import insights_adapter

_STATIC = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "static"


def _seed(db: pathlib.Path, *, key: str = "step:42", title: str | None = "Сумма чисел") -> None:
    history.record_run(
        1,
        [CaseRecord(1, "WA", failure_kind="wrong-answer")],
        db_path=db,
        task_key=key,
        task_title=title,
    )
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key=key, task_title=title)


class TestReport:
    """Агрегат обязан донести имя: дальше его читают и web, и экспорт."""

    def test_display_name_reaches_the_report(self, tmp_path: pathlib.Path) -> None:
        db = tmp_path / "h.db"
        _seed(db)

        report = progress_export.build_progress_report(db)

        assert report["tasks"][0]["display_name"] == "Сумма чисел"
        assert report["tasks"][0]["task_key"] == "step:42", "ключ остаётся рядом с именем"

    def test_old_records_keep_the_key(self, tmp_path: pathlib.Path) -> None:
        """Старая история без имени показывается как раньше, а не пустой ячейкой."""
        db = tmp_path / "h.db"
        _seed(db, title=None)

        report = progress_export.build_progress_report(db)

        assert report["tasks"][0]["display_name"] is None


class TestExport:
    """Экспорт — «то единственное, что показывают ментору» (#823)."""

    def test_markdown_shows_the_name(self, tmp_path: pathlib.Path) -> None:
        db = tmp_path / "h.db"
        _seed(db)

        text = progress_export.render_markdown(progress_export.build_progress_report(db))

        assert "Сумма чисел" in text

    def test_html_shows_the_name(self, tmp_path: pathlib.Path) -> None:
        db = tmp_path / "h.db"
        _seed(db)

        text = progress_export.render_html(progress_export.build_progress_report(db))

        assert "Сумма чисел" in text

    def test_without_name_falls_back_to_key(self, tmp_path: pathlib.Path) -> None:
        """Fallback, а не догадка: имени нет — печатается ключ."""
        db = tmp_path / "h.db"
        _seed(db, title=None)

        text = progress_export.render_markdown(progress_export.build_progress_report(db))

        assert "step:42" in text

    def test_no_task_row_stays_labelled(self, tmp_path: pathlib.Path) -> None:
        """Записи с «.» (до нормализации ключа) по-прежнему «без задачи» (#817)."""
        db = tmp_path / "h.db"
        _seed(db, key=".", title=None)

        text = progress_export.render_markdown(progress_export.build_progress_report(db))

        assert "без задачи" in text


class TestWebAdapter:
    """Тот же отчёт уезжает в `/api/progress` — имя должно быть и там."""

    def test_progress_report_carries_display_name(self, tmp_path: pathlib.Path) -> None:
        db = tmp_path / "h.db"
        _seed(db)

        report = insights_adapter.progress_report(db_path=db)

        assert report["tasks"][0]["display_name"] == "Сумма чисел"

    def test_progress_rows_carry_display_name(self, tmp_path: pathlib.Path) -> None:
        db = tmp_path / "h.db"
        _seed(db)

        rows = insights_adapter.progress_rows(db_path=db)

        assert rows[0]["display_name"] == "Сумма чисел"


class TestFrontend:
    """Фронтенд печатает имя и держит ключ рядом — статически проверяемое."""

    def test_task_label_uses_display_name(self) -> None:
        source = (_STATIC / "content.js").read_text(encoding="utf-8")

        assert "display_name" in source, "подпись задачи по-прежнему берёт только ключ"

    def test_key_stays_visible_next_to_the_name(self) -> None:
        """Ключ рядом — иначе одноимённые папки из разных курсов не различить."""
        source = (_STATIC / "content.js").read_text(encoding="utf-8")
        start = source.index("function taskCell(")

        assert "task_key" in source[start : start + 700]
