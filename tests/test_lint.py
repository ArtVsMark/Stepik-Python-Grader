"""Тесты core/lint.py — opt-in PEP-проверка через ruff (issue #346, эпик #342).

Реальные ruff-прогоны — под skipif (ruff ставится extra `[lint]`/`[dev]`);
graceful-ветки (ruff нет / упал / мусор) замоканы и работают всегда.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from stepik_grader.core.lint import (
    LintUnavailable,
    Violation,
    ruff_available,
    run_lint,
)

_HAS_RUFF = ruff_available()


def test_ruff_available_returns_bool() -> None:
    assert isinstance(ruff_available(), bool)


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_run_lint_finds_real_violations(tmp_path) -> None:
    f = tmp_path / "bad.py"
    f.write_text("import os\nx=1\n", encoding="utf-8")  # F401 (unused) + E225 (no ws)
    violations = run_lint(f)
    codes = {v.rule_code for v in violations}
    assert "F401" in codes
    assert all(isinstance(v, Violation) for v in violations)
    assert any(v.line_no > 0 for v in violations)


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_run_lint_clean_file_is_empty(tmp_path) -> None:
    f = tmp_path / "good.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert run_lint(f) == []


def test_run_lint_raises_when_ruff_missing(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = subprocess.CompletedProcess([], 1, stdout="", stderr="python: No module named ruff")
    with patch("subprocess.run", return_value=fake):
        with pytest.raises(LintUnavailable, match=r"\[lint\]"):
            run_lint(f)


def test_run_lint_garbage_json_is_graceful(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = subprocess.CompletedProcess([], 1, stdout="not json at all", stderr="")
    with patch("subprocess.run", return_value=fake):
        assert run_lint(f) == []


def test_run_lint_abnormal_exit_is_graceful(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    fake = subprocess.CompletedProcess([], 2, stdout="", stderr="ruff: usage error")
    with patch("subprocess.run", return_value=fake):
        assert run_lint(f) == []


def test_run_lint_subprocess_failure_is_graceful(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    with patch("subprocess.run", side_effect=OSError("boom")):
        assert run_lint(f) == []


def test_run_lint_parses_mocked_json(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    payload = '[{"code": "E501", "location": {"row": 3, "column": 80}, "message": "line too long"}]'
    fake = subprocess.CompletedProcess([], 1, stdout=payload, stderr="")
    with patch("subprocess.run", return_value=fake):
        [v] = run_lint(f)
    assert v == Violation(rule_code="E501", line_no=3, message="line too long", column=80)


def test_run_lint_skips_null_code(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    # ruff даёт code=null для синтаксических ошибок — их пропускаем
    payload = '[{"code": null, "location": {"row": 1}, "message": "SyntaxError"}]'
    fake = subprocess.CompletedProcess([], 1, stdout=payload, stderr="")
    with patch("subprocess.run", return_value=fake):
        assert run_lint(f) == []
