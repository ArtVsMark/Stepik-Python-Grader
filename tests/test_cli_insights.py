"""Тесты CLI-витрины инсайтов: --insights и --lint (issue #349, эпик #342).

Реальные ruff-прогоны — под skipif; graceful-ветка (ruff нет) замокана.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from stepik_grader import cli
from stepik_grader.core import history, history_recording, progress_export
from stepik_grader.core.history import CaseRecord
from stepik_grader.core.lint import ruff_available

_HAS_RUFF = ruff_available()


def _make_task(tmp_path) -> None:
    """Минимальное решение + tests/ для режима 1."""
    (tmp_path / "sol.py").write_text("import os\nprint(int(input()) + 1)\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")


def test_insights_empty_history_is_friendly_and_exit0(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # нет .grader_history.db в cwd
    cli.main(["--insights"])  # не должно кидать / завершается нормально
    out = capsys.readouterr().out.lower()
    assert "нет инсайтов" in out or "no insights" in out


def test_insights_shows_active_card(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    # issue #818: путь базы резолвится (env → конфиг → рядом → пользовательская),
    # поэтому тест готовит данные ТАМ ЖЕ, куда смотрит грейдер.
    db = history_recording.default_history_db_path()
    for _ in range(3):  # 3 падения → active
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    cli.main(["--insights"])
    out = capsys.readouterr().out
    assert "wrong-answer" in out
    assert "активна" in out


def test_insights_runtime_error_card_from_history(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    # issue #818: путь базы резолвится (env → конфиг → рядом → пользовательская),
    # поэтому тест готовит данные ТАМ ЖЕ, куда смотрит грейдер.
    db = history_recording.default_history_db_path()
    for _ in range(2):
        history.record_run(
            1, [CaseRecord(1, "RE", failure_kind="runtime-error:KeyError")], db_path=db
        )
    cli.main(["--insights"])
    out = capsys.readouterr().out
    assert "runtime-error:KeyError" in out


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_lint_flag_mode1_shows_style_block(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path)
    cli.main(["--mode", "1", "--file", str(tmp_path / "sol.py"), "--lint"])
    out = capsys.readouterr().out
    assert "Стиль" in out
    assert "F401" in out  # неиспользованный import os


def test_lint_flag_without_ruff_shows_hint(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path)
    with patch("stepik_grader.core.lint.ruff_available", return_value=False):
        cli.main(["--mode", "1", "--file", str(tmp_path / "sol.py"), "--lint"])
    out = capsys.readouterr().out
    assert "ruff не установлен" in out
    assert "stepik-python-grader[lint]" in out


def test_no_lint_flag_no_style_block(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path)
    cli.main(["--mode", "1", "--file", str(tmp_path / "sol.py")])  # без --lint
    out = capsys.readouterr().out
    assert "Стиль" not in out


# ---------------------------------------------------------------------------
# История как машинный интерфейс — issue #997 (VIS-1-03, COM-1-07)
# ---------------------------------------------------------------------------


class TestHistoryMachineReadable:
    """--insights уважает --output json, --export-progress умеет json."""

    def _history_with_one_run(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Прогон с падением → в истории есть и карточка, и задача."""
        task = tmp_path / "task1"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text("print(int(input()) * 2)\n", encoding="utf-8")
        (task / "tests" / "1").write_text("5", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("11", encoding="utf-8")
        return task

    def test_insights_json_is_parseable(self, tmp_path, monkeypatch, capsys):
        """VIS-1-03: сводку можно забрать скриптом, а не парсингом таблиц rich."""
        task = self._history_with_one_run(tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--history"])
        capsys.readouterr()

        cli.main(["--insights", "--output", "json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == cli.INSIGHTS_SCHEMA
        assert [card["key"] for card in payload["cards"]] == ["wrong-answer"]
        assert payload["tasks"][0]["task_key"] == "task1"
        assert payload["tasks"][0]["solved"] is False

    def test_insights_json_on_empty_history_is_still_json(self, tmp_path, monkeypatch, capsys):
        """Пустая история — объект с пустыми списками, а не русская проза."""
        monkeypatch.chdir(tmp_path)

        cli.main(["--insights", "--output", "json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["cards"] == [] and payload["tasks"] == []

    def test_insights_text_output_unchanged(self, tmp_path, monkeypatch, capsys):
        """Регрессия: без --output json печатается прежний человекочитаемый текст."""
        monkeypatch.chdir(tmp_path)

        cli.main(["--insights"])

        out = capsys.readouterr().out
        assert out.strip() and not out.lstrip().startswith("{")

    def test_export_progress_json_has_stable_schema(self, tmp_path, monkeypatch, capsys):
        """COM-1-07: прогресс сводится по группе без разбора вёрстки отчёта."""
        task = self._history_with_one_run(tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--history"])
        capsys.readouterr()

        cli.main(["--export-progress", "json"])

        exported = json.loads((tmp_path / "grader-progress.json").read_text(encoding="utf-8"))
        assert exported["schema"] == progress_export.SCHEMA
        assert exported["tasks"][0]["task_key"] == "task1"
        assert exported["verdicts"] == {"WA": 1}
