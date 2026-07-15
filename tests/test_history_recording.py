"""Tests for core/history_recording.py — сборка записей истории (issue #395/#403).

Хелперы вынесены из cli/commands.py, чтобы CLI и web писали историю одним кодом.
Здесь — прямые юнит-тесты преобразователей (cases/lint/db-path).
"""

from __future__ import annotations

import pathlib

from stepik_grader.core import history, history_recording
from stepik_grader.core.lint import Violation


def test_cases_from_test_results_maps_verdict_and_time() -> None:
    cases = [
        {"passed": True, "time": 0.01},
        {"passed": False, "verdict": "WA", "time": 0.02},
        {"passed": False, "verdict": "RE", "error": "ZeroDivisionError: x", "time": 0.0},
    ]
    records = history_recording.cases_from_test_results(cases)
    assert [r.case_no for r in records] == [1, 2, 3]
    assert records[0].verdict == "AC"
    assert records[0].time_ms == 10.0  # 0.01s → 10ms
    assert records[1].verdict == "WA"
    assert records[2].verdict == "RE"
    # failure_kind проставлен (таксономия), точное значение — забота insights.
    assert records[2].failure_kind is not None


def test_cases_from_bench_results_one_record_per_solution() -> None:
    results = {
        pathlib.Path("fast.py"): {"median": 1.0, "verdict": "SIMILAR"},
        pathlib.Path("slow.py"): {"median": 2.0, "verdict": "MUCH_SLOWER"},
        pathlib.Path("broken.py"): {"error": "SyntaxError"},
    }
    records = history_recording.cases_from_bench_results(results)
    verdicts = [r.verdict for r in records]
    assert verdicts == ["SIMILAR", "MUCH_SLOWER", "ERR"]


def test_lint_records_from_violations_drops_column() -> None:
    """issue #403: Violation → LintRecord (rule_code/line_no/message; column не пишется)."""
    violations = [
        Violation(rule_code="F401", line_no=1, message="unused import", column=5),
        Violation(rule_code="E501", line_no=10, message="line too long", column=80),
    ]
    records = history_recording.lint_records_from_violations(violations)
    assert all(isinstance(r, history.LintRecord) for r in records)
    assert [(r.rule_code, r.line_no, r.message) for r in records] == [
        ("F401", 1, "unused import"),
        ("E501", 10, "line too long"),
    ]


def test_lint_records_from_violations_empty() -> None:
    assert history_recording.lint_records_from_violations([]) == []


def test_default_history_db_path_uses_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert history_recording.default_history_db_path() == tmp_path / history.HISTORY_DB_NAME
