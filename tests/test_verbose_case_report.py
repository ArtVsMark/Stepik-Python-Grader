"""Подробный отчёт по кейсу при RE и TLE (issue #965, находка MET-1-01).

Экран, который студент видит в момент падения решения. Ветка ``error`` в
``print_case_verbose`` печатала ``[ERROR]`` и делала ``return``, поэтому блок
со входом кейса был недостижим — при RE и TLE, то есть ровно тогда, когда
«на каких данных сломалось» нужнее всего, вход не показывался. Докстринг той
же функции обещал обратное (issue #824, DESC-01).

Тесты идут через ``print_case_verbose`` напрямую (plain-режим, без rich):
проверяется то, что попадает в терминал, а не внутреннее состояние.
"""

from __future__ import annotations

from typing import Any

import pytest

from stepik_grader.core import reporter
from stepik_grader.core.test_loader import TestCase


@pytest.fixture(autouse=True)
def _plain_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без rich — иначе перенос строк таблицей ломает проверку подстрок."""
    monkeypatch.setattr(reporter, "_RICH", False)


def _case() -> TestCase:
    return TestCase(index=7, input_lines=["3 1 4"], expected_lines=["8"])


def _result(**over: Any) -> Any:
    base: dict[str, Any] = {
        "passed": False,
        "verdict": "WA",
        "error": "",
        "expected": ["8"],
        "output": ["9"],
        "diff": "-8\n+9",
    }
    base.update(over)
    return base


def test_runtime_error_report_shows_case_input(capsys: pytest.CaptureFixture[str]) -> None:
    """При RE виден ВХОД кейса, а не только текст исключения (issue #965)."""
    reporter.print_case_verbose(
        _case(),
        _result(verdict="RE", error="IndexError: list index out of range", output=[], diff=""),
    )

    out = capsys.readouterr().out
    assert "Input:" in out, "вход кейса не напечатан — студенту снова идти в tests/"
    assert "3 1 4" in out
    assert "IndexError" in out


def test_timeout_report_shows_input_expected_and_actual(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """При TLE видно и вход, и чего ждали, и что решение успело напечатать."""
    reporter.print_case_verbose(
        _case(),
        _result(verdict="TLE", error="Превышен лимит времени (5.0 с)", output=[], diff=""),
    )

    out = capsys.readouterr().out
    assert "Input:" in out
    assert "3 1 4" in out
    assert "Expected:" in out
    assert "Actual:" in out


def test_runtime_error_report_stops_after_the_hint(capsys: pytest.CaptureFixture[str]) -> None:
    """При RE сравнения нет: ``output`` пуст по построению, diff не строился.

    Печатать «Expected: 8 / Actual: (empty)» здесь — не помощь, а шум: решение
    не дало ответа вовсе, и это уже сказано вердиктом и текстом ошибки.
    """
    reporter.print_case_verbose(
        _case(),
        _result(verdict="RE", error="ValueError: boom", output=[], diff=""),
    )

    out = capsys.readouterr().out
    assert "Expected:" not in out
    assert "Diff:" not in out


def test_wrong_answer_report_keeps_its_shape(capsys: pytest.CaptureFixture[str]) -> None:
    """Главный риск правки: не потерять прежний отчёт WA, ради которого блок и писался."""
    reporter.print_case_verbose(_case(), _result())

    out = capsys.readouterr().out
    for marker in ("Input:", "3 1 4", "Expected:", "Actual:", "Diff:", "-8", "+9"):
        assert marker in out, f"из отчёта WA пропало: {marker}"


def test_passed_case_prints_only_the_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    """Пройденный кейс не обрастает входом: отчёт остаётся одной строкой."""
    reporter.print_case_verbose(_case(), _result(passed=True, verdict="AC", output=["8"], diff=""))

    out = capsys.readouterr().out
    assert "Test 7" in out
    assert "Input:" not in out
