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


# ---------------------------------------------------------------------------
# select ровно по карточкам базы + preview (issue #728)
# ---------------------------------------------------------------------------


def test_lint_select_covers_exactly_bundled_cards() -> None:
    """Набор для ruff строится из базы — разойтись они не могут по построению."""
    from stepik_grader import rules

    select = rules.lint_select()

    assert select
    assert select.split(",") == sorted(card.id for card in rules.bundled_rules().all())


def test_empty_select_does_not_call_ruff(tmp_path) -> None:
    """Пустая база → ruff не запускается вовсе (нечего проверять)."""
    f = tmp_path / "x.py"
    f.write_text("x=1\n", encoding="utf-8")

    with patch("subprocess.run") as run:
        assert run_lint(f, select="") == []

    run.assert_not_called()


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_every_bundled_code_is_known_to_ruff() -> None:
    """Guard issue #728: карточка без живого правила ruff — мёртвая.

    Ловит и опечатку в ``id`` карточки, и правило, удалённое/переименованное в
    новой версии ruff. Именно отсутствие такой сверки позволило расхождению
    накопиться незаметно.
    """
    import json
    import sys

    from stepik_grader import rules

    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "rule", "--all", "--output-format", "json"],
        capture_output=True,
        check=True,
    )
    known = {rule["code"] for rule in json.loads(proc.stdout.decode("utf-8"))}

    assert set(rules.bundled_rule_codes()) <= known


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_preview_enables_whitespace_rules(tmp_path) -> None:
    """issue #728: без --preview «школьные» E2xx/E3xx не срабатывали вовсе.

    Именно они нужнее всего ученику: пробелы вокруг оператора, пробел после
    запятой, пробелы в скобках, пустые строки между определениями.
    """
    from stepik_grader import rules

    f = tmp_path / "bad_style.py"
    f.write_text("x=1\ny = [ 1,2 ]\ndef f(a,b):\n    return a+b\n", encoding="utf-8")
    select = rules.lint_select()

    without_preview = {v.rule_code for v in run_lint(f, select=select)}
    with_preview = {v.rule_code for v in run_lint(f, select=select, preview=True)}

    assert {"E225", "E231", "E201", "E202"} <= with_preview
    assert not {"E225", "E231", "E201", "E202"} & without_preview


@pytest.mark.skipif(not _HAS_RUFF, reason="ruff не установлен (extra [lint])")
def test_reported_codes_always_have_a_card(tmp_path) -> None:
    """Ни один показанный код не остаётся без объяснения в разделе «Правила»."""
    from stepik_grader import rules

    f = tmp_path / "messy.py"
    f.write_text(
        "import os\nx=1\ny = [ 1,2 ]\nif x == None :\n    pass\n"
        "try:\n    pass\nexcept:\n    pass\n",
        encoding="utf-8",
    )

    codes = {v.rule_code for v in run_lint(f, select=rules.lint_select(), preview=True)}

    assert codes
    assert codes <= set(rules.bundled_rule_codes())
