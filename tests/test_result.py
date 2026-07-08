"""Tests for core/result.py — typed TestResult/Verdict model (issue #112/#113/#115).

Two halves:
- unit tests of ``TestResult.from_dict``/``to_dict`` against hand-built
  case-result dicts (defaulting behavior, round-trip);
- characterizing tests that feed *real* ``run_single_test()`` output (AC/WA/
  RE/TLE) through ``TestResult.from_dict`` to lock the model against actual
  grader behavior, not just synthetic fixtures — this is the part #115 asks
  for ("тесты покрывают AC/WA/RE/TLE" / "нет регрессий существующего сьюта").
"""

from __future__ import annotations

import pathlib

from stepik_grader.core.result import TestResult
from stepik_grader.grader import TestCase, run_single_test

# ---------------------------------------------------------------------------
# TestResult.from_dict / to_dict — unit tests
# ---------------------------------------------------------------------------


def test_from_dict_full_ac_roundtrip() -> None:
    raw = {
        "passed": True,
        "verdict": "AC",
        "output": ["5"],
        "expected": ["5"],
        "diff": "",
        "time": 0.01,
        "memory": 1.5,
        "error": "",
        "timed_out": False,
    }
    result = TestResult.from_dict(raw)
    assert result == TestResult(
        passed=True,
        verdict="AC",
        output=["5"],
        expected=["5"],
        diff="",
        time=0.01,
        memory=1.5,
        error="",
        timed_out=False,
    )
    assert result.to_dict() == raw


def test_from_dict_wa_with_diff() -> None:
    raw = {
        "passed": False,
        "verdict": "WA",
        "output": ["9"],
        "expected": ["10"],
        "diff": "-10\n+9",
        "time": 0.02,
        "memory": 1.2,
        "error": "",
        "timed_out": False,
    }
    result = TestResult.from_dict(raw)
    assert result.verdict == "WA"
    assert result.diff == "-10\n+9"
    assert result.to_dict() == raw


def test_from_dict_re_with_error() -> None:
    raw = {
        "passed": False,
        "verdict": "RE",
        "output": [],
        "expected": ["1"],
        "diff": "",
        "time": 0.0,
        "memory": 0.0,
        "error": "NameError: name 'x' is not defined",
        "timed_out": False,
    }
    result = TestResult.from_dict(raw)
    assert result.verdict == "RE"
    assert result.error == "NameError: name 'x' is not defined"
    assert result.to_dict() == raw


def test_from_dict_tle_with_timed_out() -> None:
    raw = {
        "passed": False,
        "verdict": "TLE",
        "output": [],
        "expected": ["1"],
        "diff": "",
        "time": 0.1,
        "memory": 0.0,
        "error": "",
        "timed_out": True,
    }
    result = TestResult.from_dict(raw)
    assert result.verdict == "TLE"
    assert result.timed_out is True
    assert result.to_dict() == raw


def test_from_dict_infers_verdict_ac_when_missing() -> None:
    result = TestResult.from_dict({"passed": True, "error": ""})
    assert result.verdict == "AC"


def test_from_dict_infers_verdict_wa_when_missing() -> None:
    result = TestResult.from_dict({"passed": False, "error": ""})
    assert result.verdict == "WA"


def test_from_dict_defaults_optional_fields_when_missing() -> None:
    result = TestResult.from_dict({"passed": True, "error": ""})
    assert result.output == []
    assert result.expected == []
    assert result.diff == ""
    assert result.time == 0.0
    assert result.memory == 0.0
    assert result.timed_out is False


def test_result_is_frozen() -> None:
    result = TestResult.from_dict({"passed": True, "error": ""})
    try:
        result.passed = False  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("TestResult must be immutable (frozen dataclass)")


# ---------------------------------------------------------------------------
# Characterizing tests — real run_single_test() output through TestResult
# ---------------------------------------------------------------------------


def _make_solution(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    sol = tmp_path / "task1.py"
    sol.write_text(body, encoding="utf-8")
    return sol


def test_real_ac_case_maps_onto_test_result(tmp_path: pathlib.Path) -> None:
    sol = _make_solution(tmp_path, "print(int(input()) + 1)\n")
    case = TestCase(index=1, input_lines=["4"], expected_lines=["5"])
    raw = run_single_test(str(sol), case, measure_memory=False)

    result = TestResult.from_dict(raw)
    assert result.passed is True
    assert result.verdict == "AC"
    assert result.error == ""
    assert result.timed_out is False


def test_real_wa_case_maps_onto_test_result(tmp_path: pathlib.Path) -> None:
    sol = _make_solution(tmp_path, "print(int(input()) + 2)\n")  # 4 -> 6, ждём 5
    case = TestCase(index=1, input_lines=["4"], expected_lines=["5"])
    raw = run_single_test(str(sol), case, measure_memory=False)

    result = TestResult.from_dict(raw)
    assert result.passed is False
    assert result.verdict == "WA"
    assert result.diff  # непустой unified diff
    assert result.error == ""


def test_real_re_case_maps_onto_test_result(tmp_path: pathlib.Path) -> None:
    sol = _make_solution(tmp_path, "raise ValueError('boom')\n")
    case = TestCase(index=1, input_lines=["4"], expected_lines=["5"])
    raw = run_single_test(str(sol), case, measure_memory=False)

    result = TestResult.from_dict(raw)
    assert result.passed is False
    assert result.verdict == "RE"
    assert "ValueError" in result.error
    assert result.timed_out is False


def test_real_tle_case_maps_onto_test_result(tmp_path: pathlib.Path) -> None:
    sol = _make_solution(tmp_path, "import time; time.sleep(100)\n")
    case = TestCase(index=1, input_lines=["4"], expected_lines=["5"])
    raw = run_single_test(str(sol), case, timeout=0.1, measure_memory=False)

    result = TestResult.from_dict(raw)
    assert result.passed is False
    assert result.verdict == "TLE"
    assert result.timed_out is True
