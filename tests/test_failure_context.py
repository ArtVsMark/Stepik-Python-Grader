"""Tests for core/failure_context.build_failure_context (issue #542, эпик E3).

Хелпер вынесен из ``cli/commands`` в core, чтобы CLI-режимы 1–4 и web-слой
строили ОДИН И ТОТ ЖЕ заземляющий контекст упавшего кейса. Ключевой сценарий
приёмки: один WA-кейс из общего исполнителя ``run_tests`` даёт идентичный
``FailureContext``, кто бы его ни строил (CLI-обработчик или web-путь) — билдер
один, без дублирования.
"""

from __future__ import annotations

import pathlib

from stepik_grader.cli.commands import build_failure_context as cli_builder
from stepik_grader.core.failure_context import build_failure_context
from stepik_grader.web.grading import run_tests  # web достаёт исполнитель через фасад


def _make_task(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """Решение + tests/ (legacy-формат N/N.clue): вход 4, ожидание 5."""
    sol = tmp_path / "sol.py"
    sol.write_text(body, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")
    return sol


# --- юнит-ветки билдера -------------------------------------------------------


def test_build_wa_case_wrong_answer() -> None:
    case = {
        "passed": False,
        "verdict": "WA",
        "output": ["6"],
        "expected": ["5"],
        "diff": "- 5\n+ 6",
        "error": "",
        "stdin": "4",
    }
    fc = build_failure_context(case, code="print(1)", lang="ru")
    assert fc.verdict == "WA"
    assert fc.failure_kind == "wrong-answer"
    assert fc.expected == "5" and fc.actual == "6"
    assert fc.case_input == "4" and fc.diff == "- 5\n+ 6"
    assert fc.card_text == ""  # нет error → нет карточки
    assert fc.code == "print(1)"


def test_build_wa_format_only() -> None:
    """WA, различие только в форматировании → failure_kind = output-format."""
    case = {"passed": False, "verdict": "WA", "output": ["5 "], "expected": ["5"]}
    assert build_failure_context(case).failure_kind == "output-format"


def test_build_re_case_populates_kind_and_card() -> None:
    err = 'Traceback (most recent call last):\n  File "x"\nZeroDivisionError: division by zero'
    case = {"passed": False, "verdict": "RE", "error": err}
    fc = build_failure_context(case, lang="en")
    assert fc.failure_kind == "runtime-error:ZeroDivisionError"
    assert fc.card_text.startswith("ZeroDivisionError")
    assert fc.lang == "en"
    assert fc.error == err


def test_build_bench_dict_modes_3_4() -> None:
    """Режимы 3/4: bench-dict (verdict/error, без output/expected/stdin/diff) —
    билдер принимает форму без падения, отсутствующие поля пусты."""
    bench = {"verdict": "RE", "error": "Traceback\nValueError: bad", "runs": 5, "median": 0.1}
    fc = build_failure_context(
        {"verdict": bench.get("verdict") or "RE", "error": bench["error"]}, code="x=1"
    )
    assert fc.verdict == "RE"
    assert fc.failure_kind == "runtime-error:ValueError"
    assert fc.case_input == "" and fc.expected == "" and fc.actual == "" and fc.diff == ""
    assert fc.code == "x=1"


def test_build_defaults_on_empty_case() -> None:
    """Пустой case → безопасные дефолты (verdict WA, всё остальное пусто)."""
    fc = build_failure_context({})
    assert fc.verdict == "WA" and fc.lang == "ru"
    assert fc.error == "" and fc.card_text == "" and fc.code == ""


# --- приёмка: идентичный контекст CLI vs web-путь ----------------------------


def test_cli_and_web_build_identical_context(tmp_path: pathlib.Path) -> None:
    """#542: один WA-кейс из общего run_tests → идентичный FailureContext,
    кто бы ни строил (CLI-обработчик или web-путь). Билдер — один объект."""
    sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4+2=6 ≠ 5 → WA
    test_dir = sol.parent / "tests"
    result = run_tests(sol, test_dir)  # общий исполнитель: и CLI, и web зовут его
    case = next(c for c in result["cases"] if not c.get("passed"))
    code = sol.read_text(encoding="utf-8")

    fc_web = build_failure_context(case, code=code, lang="ru")
    fc_cli = cli_builder(case, code=code, lang="ru")

    # CLI-обработчик импортирует ровно этот core-билдер — без дублирующей сборки.
    assert cli_builder is build_failure_context
    assert fc_cli == fc_web  # идентичный контекст

    assert fc_web.verdict == "WA"
    assert fc_web.case_input == "4"
    assert fc_web.expected == "5"
    assert fc_web.actual == "6"
    assert fc_web.failure_kind == "wrong-answer"
    assert fc_web.card_text == ""
    assert fc_web.code == code
    assert fc_web.lang == "ru"
