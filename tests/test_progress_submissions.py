"""Сверка «наш вердикт ↔ вердикт Stepik» доходит до человека (issue #1607).

Таблица ``stepik_submissions`` пополнялась на каждой отправке, а читал её только
тест. Здесь закреплено, что расхождение видно в отчёте прогресса (веб-раздел
«Прогресс», ``GET /api/progress``) и в ``stepik-grader stats`` — тексте и JSON.
"""

import json
from pathlib import Path

import pytest

from stepik_grader import cli
from stepik_grader.core import history, progress_export
from stepik_grader.core import stats as stats_mod
from stepik_grader.core.history import CaseRecord


def _db(tmp_path: Path) -> Path:
    return tmp_path / history.HISTORY_DB_NAME


def _submit(db: Path, step: int, ours: str | None, platform: str) -> None:
    """Отправка решения задачи ``step:<step>``; ``ours`` — наш прогон до неё."""
    key = f"step:{step}"
    if ours is not None:
        history.record_run(1, [CaseRecord(1, ours)], db_path=db, task_key=key)
    history.record_stepik_submission(step, platform, db_path=db, task_key=key)


class TestSubmissionsSummary:
    def test_counts_and_names_the_disagreement(self, tmp_path: Path) -> None:
        """Всего, сверено, разошлось — и само расхождение с обеими сторонами."""
        db = _db(tmp_path)
        _submit(db, 1, "AC", "correct")  # согласны
        _submit(db, 2, "AC", "wrong")  # расхождение: у нас AC, у платформы нет
        _submit(db, 3, None, "wrong")  # нашего прогона не было — не сверено

        summary = progress_export.submissions_summary(db)

        assert (summary["total"], summary["compared"], summary["diverged"]) == (3, 2, 1)
        [row] = summary["recent_diverged"]
        assert row["task_key"] == "step:2"
        assert (row["verdict"], row["our_verdict"]) == ("wrong", "AC")

    def test_recent_is_newest_first_and_bounded(self, tmp_path: Path) -> None:
        """Последние расхождения — новые первыми и не больше ``recent``."""
        db = _db(tmp_path)
        for step in range(1, 5):
            _submit(db, step, "WA", "correct")

        summary = progress_export.submissions_summary(db, recent=2)

        assert summary["diverged"] == 4
        assert [r["task_key"] for r in summary["recent_diverged"]] == ["step:4", "step:3"]

    def test_no_history_is_zeros_not_error(self, tmp_path: Path) -> None:
        """Базы нет — нули, а не исключение: раздел «Прогресс» не должен падать."""
        summary = progress_export.submissions_summary(tmp_path / "nope.db")

        assert summary == {"total": 0, "compared": 0, "diverged": 0, "recent_diverged": []}

    def test_progress_report_carries_it(self, tmp_path: Path) -> None:
        """Отчёт прогресса (веб и экспорт) несёт сверку тем же полем."""
        db = _db(tmp_path)
        _submit(db, 2, "AC", "wrong")

        report = progress_export.build_progress_report(db)

        assert report["submissions"]["diverged"] == 1


class TestStatsCommand:
    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(stats_mod, "_default_path", lambda: tmp_path / ".grader_stats.jsonl")
        monkeypatch.setenv("STEPIK_GRADER_HISTORY_DB", str(_db(tmp_path)))

    def test_text_names_the_disagreement(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Строка сверки и само расхождение — в обычном выводе `stats`."""
        _submit(_db(tmp_path), 2, "AC", "wrong")

        assert cli.main(["stats"]) == 0

        out = capsys.readouterr().out
        assert "Stepik" in out
        assert "step:2" in out

    def test_no_submissions_no_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Отправок нет — строки нет: «0 из 0» ничего не сообщает."""
        assert cli.main(["stats"]) == 0

        assert "step:" not in capsys.readouterr().out

    def test_json_carries_the_same_field(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`--output json` отдаёт ту же сводку, что и отчёт прогресса."""
        _submit(_db(tmp_path), 2, "AC", "wrong")

        assert cli.main(["stats", "--output", "json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["submissions"]["diverged"] == 1
        assert payload["submissions"]["recent_diverged"][0]["our_verdict"] == "AC"
