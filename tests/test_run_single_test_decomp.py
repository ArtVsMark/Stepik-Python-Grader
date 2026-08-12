"""Изолированные тесты декомпозиции run_single_test (issue #406).

``_map_outcome_to_result`` — чистая функция: конструируем ``RunOutcome``
напрямую и проверяем КАЖДУЮ ветку verdict (launch_error / sandbox_violation /
cancelled / timed_out / RE / AC / WA) без реального subprocess-запуска.
``_prepare_run_spec`` — выбор стратегии stdin/function + prep-ошибки, без
исполнения раннера. Это и есть acceptance-критерий #406: «ветки verdict
тестируются изолированно».
"""

from __future__ import annotations

import pathlib
import threading

import pytest

from stepik_grader.core.grader_core import (
    TestCase,
    _map_outcome_to_result,
    _prepare_run_spec,
)
from stepik_grader.core.runner import RunOutcome


def _case(
    *,
    expected: list[str] | None = None,
    input_lines: list[str] | None = None,
    test_type: str = "stdin",
) -> TestCase:
    return TestCase(
        index=1,
        input_lines=input_lines if input_lines is not None else ["1"],
        expected_lines=expected if expected is not None else ["ok"],
        test_type=test_type,
    )


# ---------------------------------------------------------------------------
# _map_outcome_to_result — ветки verdict изолированно (чистая функция)
# ---------------------------------------------------------------------------


def test_map_launch_error_is_re() -> None:
    r = _map_outcome_to_result(RunOutcome(launch_error="No such file"), _case(), timeout=5.0)
    assert r["verdict"] == "RE"
    assert r["passed"] is False
    assert r["error"] == "No such file"
    assert r["exit_code"] is None
    assert r["time"] == 0.0


def test_map_sandbox_violation_is_distinct_verdict() -> None:
    out = RunOutcome(sandbox_violation="memory", elapsed=1.5, peak_memory_mb=42.0)
    r = _map_outcome_to_result(out, _case(), timeout=5.0)
    assert r["verdict"] == "SANDBOX_VIOLATION"
    assert "memory" in r["error"]
    assert r["time"] == 1.5
    assert r["memory"] == 42.0


def test_map_cancelled_is_not_tle() -> None:
    r = _map_outcome_to_result(RunOutcome(cancelled=True, elapsed=0.7), _case(), timeout=5.0)
    assert r["verdict"] == "CANCELLED"
    assert r["timed_out"] is False  # отмена ≠ таймаут
    assert r["time"] == 0.7


def test_map_timed_out_is_tle() -> None:
    r = _map_outcome_to_result(RunOutcome(timed_out=True, elapsed=5.0), _case(), timeout=5.0)
    assert r["verdict"] == "TLE"
    assert r["timed_out"] is True
    assert "5.0" in r["error"]
    assert r["exit_code"] is None


def test_map_nonzero_returncode_is_re_with_stderr() -> None:
    out = RunOutcome(returncode=1, stderr=b"ValueError: boom\n", elapsed=0.1, peak_memory_mb=3.0)
    r = _map_outcome_to_result(out, _case(), timeout=5.0)
    assert r["verdict"] == "RE"
    assert r["error"] == "ValueError: boom"
    assert r["exit_code"] == 1
    assert r["memory"] == 3.0


def test_map_exact_match_is_ac() -> None:
    out = RunOutcome(returncode=0, stdout=b"ok\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["ok"]), timeout=5.0)
    assert r["verdict"] == "AC"
    assert r["passed"] is True
    assert r["output"] == ["ok"]
    assert r["diff"] == ""
    assert r["exit_code"] == 0


def test_map_mismatch_is_wa_with_diff() -> None:
    out = RunOutcome(returncode=0, stdout=b"42\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["7"]), timeout=5.0)
    assert r["verdict"] == "WA"
    assert r["passed"] is False
    assert r["diff"] != ""


def test_map_float_normalization_counts_as_ac() -> None:
    # "0.30000000000000004" ≠ "0.3" как строки, но нормализованные float равны.
    out = RunOutcome(returncode=0, stdout=b"0.30000000000000004\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["0.3"]), timeout=5.0)
    assert r["verdict"] == "AC"
    assert r["passed"] is True


# ── вывод не в UTF-8: сравнение байтов, а не символов замены (issue #1031) ──


def test_undecodable_output_is_not_accepted() -> None:
    """Вывод из непредставимых байт больше не получает ``AC`` (issue #1031).

    Терпимый декод схлопывал ЛЮБОЙ такой байт в один и тот же ``�``, поэтому
    ожидание из одного символа замены принимало какой угодно мусор — ложное
    принятие неверного решения, тот же класс, что #932 и #940.
    """
    out = RunOutcome(returncode=0, stdout=b"\x80\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["�"]), timeout=5.0)

    assert r["verdict"] == "WA"
    assert r["passed"] is False


def test_different_undecodable_bytes_are_told_apart() -> None:
    """Разные битые байты различимы по тексту ошибки, а не сливаются в один.

    Вердикт у обоих ``WA`` — и это верно: ожидание всегда валидный UTF-8, так
    что совпасть с ним не может ни один такой вывод. Но диагностика обязана
    называть КОНКРЕТНЫЙ байт, иначе разбираться пользователю не с чем.
    """
    first = _map_outcome_to_result(
        RunOutcome(returncode=0, stdout=b"\x80\n", elapsed=0.05),
        _case(expected=["�"]),
        timeout=5.0,
    )
    second = _map_outcome_to_result(
        RunOutcome(returncode=0, stdout=b"\xff\n", elapsed=0.05),
        _case(expected=["�"]),
        timeout=5.0,
    )

    assert "0x80" in first["error"]
    assert "0xff" in second["error"]
    assert first["error"] != second["error"]


def test_undecodable_output_names_the_encoding() -> None:
    """В отчёте есть слово о кодировке, а не только квадратики (``FZZ-2-03``)."""
    out = RunOutcome(returncode=0, stdout=b"\x80\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["x"]), timeout=5.0)

    assert "utf-8" in r["error"].lower()
    # Вывод всё же показан: пустой output не дал бы понять, что напечатало решение.
    assert r["output"] == ["�"]


def test_runtime_error_keeps_its_verdict_despite_broken_bytes() -> None:
    """Упавшее решение остаётся ``RE``, даже если успело напечатать битый байт.

    Строгий декод стоит ПОСЛЕ проверки кода возврата намеренно: иначе диагноз
    подменялся бы на ходу — вместо честного «решение упало» пользователь читал
    бы про кодировку.
    """
    out = RunOutcome(returncode=1, stdout=b"\x80", stderr=b"ValueError: boom\n", elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=["x"]), timeout=5.0)

    assert r["verdict"] == "RE"
    assert r["error"] == "ValueError: boom"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("Привет, мир", ["Привет, мир"]),
        ("готово 🎉", ["готово 🎉"]),
        ("Ответ: 42 ✅", ["Ответ: 42 ✅"]),
        ("плюс-минус ±3°", ["плюс-минус ±3°"]),
    ],
)
def test_non_ascii_output_still_passes(payload: str, expected: list[str]) -> None:
    """ГРАНИЦА issue #1031: кириллица и эмодзи обязаны остаться ``AC``.

    Задачи курса на русском, вывод сплошь не-ASCII — регресс здесь означал бы,
    что верные решения массово получают ``WA``. Строгий декод их не задевает:
    это корректный UTF-8, ломается только то, что им не является.
    """
    out = RunOutcome(returncode=0, stdout=f"{payload}\n".encode(), elapsed=0.05)
    r = _map_outcome_to_result(out, _case(expected=expected), timeout=5.0)

    assert r["verdict"] == "AC"
    assert r["passed"] is True


# ---------------------------------------------------------------------------
# _prepare_run_spec — стратегия запуска + prep-ошибки
# ---------------------------------------------------------------------------


def test_prepare_stdin_mode_builds_spec_with_stdin() -> None:
    case = _case(input_lines=["3", "4"], test_type="stdin")
    plan = _prepare_run_spec(
        pathlib.Path("/tmp/sol.py"),
        case,
        timeout=5.0,
        measure_memory=False,
        cancel_event=None,
    )
    assert plan.error is None
    assert plan.tmp_wrapper_path is None
    assert plan.spec is not None
    assert plan.spec.path == pathlib.Path("/tmp/sol.py")
    assert plan.spec.stdin == b"3\n4\n"


def test_prepare_function_call_block_writes_wrapper(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "sol.py"
    sol.write_text("def solve(n):\n    return n * 2\n", encoding="utf-8")
    case = _case(input_lines=["print(solve(5))"], expected=["10"], test_type="function")
    plan = _prepare_run_spec(sol, case, timeout=5.0, measure_memory=False, cancel_event=None)
    try:
        assert plan.error is None
        assert plan.spec is not None
        assert plan.tmp_wrapper_path is not None
        # spec указывает на временный wrapper, не на файл решения
        assert plan.spec.path == plan.tmp_wrapper_path
        assert plan.spec.path != sol
        assert plan.spec.stdin is None  # wrapper не читает stdin
        assert plan.tmp_wrapper_path.exists()
    finally:
        if plan.tmp_wrapper_path is not None:
            plan.tmp_wrapper_path.unlink(missing_ok=True)


def test_prepare_function_missing_name_is_prep_error(tmp_path: pathlib.Path) -> None:
    # legacy function-mode (input — данные, не python-код), решение без def и
    # без meta.json → function_name не найден → prep-ошибка, запуска нет.
    sol = tmp_path / "sol.py"
    sol.write_text("x = 5\n", encoding="utf-8")
    case = _case(input_lines=["5"], expected=["25"], test_type="function")
    plan = _prepare_run_spec(sol, case, timeout=5.0, measure_memory=False, cancel_event=None)
    assert plan.spec is None
    assert plan.tmp_wrapper_path is None
    assert plan.error is not None
    assert "function_name not found" in plan.error


def test_prepare_passes_cancel_event_and_flags_into_spec() -> None:
    ev = threading.Event()
    plan = _prepare_run_spec(
        pathlib.Path("/tmp/sol.py"),
        _case(test_type="stdin"),
        timeout=1.0,
        measure_memory=True,
        cancel_event=ev,
    )
    assert plan.spec is not None
    assert plan.spec.cancel_event is ev
    assert plan.spec.measure_memory is True
    assert plan.spec.timeout == 1.0
