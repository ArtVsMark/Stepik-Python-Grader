"""Тесты CLI-витрины инсайтов: --insights и --lint (issue #349, эпик #342).

Реальные ruff-прогоны — под skipif; graceful-ветка (ruff нет) замокана.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from stepik_grader import cli
from stepik_grader.core import history
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
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(3):  # 3 падения → active
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    cli.main(["--insights"])
    out = capsys.readouterr().out
    assert "wrong-answer" in out
    assert "активна" in out


def test_insights_runtime_error_card_from_history(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / history.HISTORY_DB_NAME
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
