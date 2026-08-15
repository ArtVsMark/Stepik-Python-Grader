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

import pytest

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
        "exit_code": 0,
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
        exit_code=0,
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
        "exit_code": 0,
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
        "exit_code": 0,
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
        "exit_code": 0,
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


class TestRoundTripCoversTheWholeContract:
    """issue #996 (ARCH-1-06, QA-1-05): `to_dict()` терял контрактное поле.

    `CaseResult` объявляет `exit_code` обязательным, и его пишут обе ветки ядра
    (`_fail_result`, `_map_outcome_to_result`). `TestResult` этого поля не имел
    вовсе, поэтому `to_dict()` возвращал словарь, контракту НЕ удовлетворяющий.

    Прежний round-trip-тест был зелёным по построению: фикстура не содержала
    `exit_code`, и потеря поля физически не могла проявиться. Здесь round-trip
    идёт от ПОЛНОГО набора ключей контракта — тест обязан краснеть на любом
    следующем потерянном поле, а не только на этом.
    """

    def _full_case(self) -> dict[str, object]:
        return {
            "passed": False,
            "verdict": "RE",
            "output": [],
            "expected": ["8"],
            "diff": "",
            "time": 0.125,
            "memory": 4.2,
            "error": "IndexError: list index out of range",
            "timed_out": False,
            "exit_code": 1,
        }

    def test_round_trip_keeps_every_contract_key(self) -> None:
        source = self._full_case()

        restored = TestResult.from_dict(source).to_dict()

        assert set(restored) == set(source), "round-trip потерял ключ контракта"
        assert restored == source

    def test_exit_code_survives(self) -> None:
        """Именно по нему отличают «упало» от «отработало с ненулевым статусом»."""
        assert TestResult.from_dict(self._full_case()).exit_code == 1

    def test_missing_exit_code_becomes_none(self) -> None:
        """Кейс не запускался — «не измерялось», а не ноль: ноль означал бы успех."""
        source = self._full_case()
        del source["exit_code"]

        assert TestResult.from_dict(source).exit_code is None

    @pytest.mark.parametrize("value", ["1", 1.5, True, [], None])
    def test_garbage_exit_code_degrades_to_none(self, value: object) -> None:
        """Словарь приходит из JSON и от сторонних потребителей контракта.

        Одно испорченное значение не должно ронять разбор всего результата —
        та же терпимость, что у остальных полей `from_dict`. `True` отсекается
        отдельно: bool — подкласс int, и кодом возврата он не бывает.
        """
        source = self._full_case()
        source["exit_code"] = value

        assert TestResult.from_dict(source).exit_code is None
