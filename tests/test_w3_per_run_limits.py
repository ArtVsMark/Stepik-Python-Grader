"""issue #641: per-run лимиты ``{timeout_s, memory_mb}`` из POST /api/v1/runs.

Раньше ``RunSpec.timeout``/``max_memory_mb`` брались только из глобального
``CONFIG`` — на сервере нельзя было дать разным задачам/тарифам разные лимиты без
перезапуска процесса. Теперь тело запроса несёт опц. ``limits``: они
валидируются в серверных границах и кладутся в ``RunSpec`` per-run.

Три уровня: парсинг/кламп (``server._parse_run_limits``) → сборка спеки
(``grader_core._prepare_run_spec``) → сквозной путь ``grade_path(timeout=…)``.
HTTP end-to-end (server→runs→grade) — в ``tests/test_web.py``.
"""

from __future__ import annotations

import pathlib

from stepik_grader import grader, web
from stepik_grader.config import CONFIG
from stepik_grader.core.grader_core import _prepare_run_spec
from stepik_grader.web.api_routes import _MEMORY_MB_RANGE, _TIMEOUT_S_RANGE, _parse_run_limits

# ---------------------------------------------------------------------------
# Парсинг и кламп лимитов из тела запроса (server._parse_run_limits)
# ---------------------------------------------------------------------------


def test_limits_absent_or_non_dict_yields_nothing() -> None:
    assert _parse_run_limits({}) == {}
    assert _parse_run_limits({"limits": None}) == {}
    assert _parse_run_limits({"limits": "nope"}) == {}
    assert _parse_run_limits({"limits": {}}) == {}


def test_limits_valid_pass_through() -> None:
    assert _parse_run_limits({"limits": {"timeout_s": 5, "memory_mb": 128}}) == {
        "timeout_s": 5.0,
        "memory_mb": 128,
    }


def test_limits_partial_sets_only_given_key() -> None:
    assert _parse_run_limits({"limits": {"timeout_s": 3}}) == {"timeout_s": 3.0}
    assert _parse_run_limits({"limits": {"memory_mb": 64}}) == {"memory_mb": 64}


def test_limits_clamped_to_upper_bound() -> None:
    out = _parse_run_limits({"limits": {"timeout_s": 9999, "memory_mb": 10**9}})
    assert out["timeout_s"] == _TIMEOUT_S_RANGE[1]
    assert out["memory_mb"] == _MEMORY_MB_RANGE[1]


def test_limits_clamped_to_lower_bound() -> None:
    out = _parse_run_limits({"limits": {"timeout_s": 0, "memory_mb": 1}})
    assert out["timeout_s"] == _TIMEOUT_S_RANGE[0]
    assert out["memory_mb"] == _MEMORY_MB_RANGE[0]


def test_limits_garbage_values_fall_back_to_config() -> None:
    """Строка/список/bool → ключ опускается, grade-слой берёт дефолт CONFIG."""
    assert _parse_run_limits({"limits": {"timeout_s": "abc", "memory_mb": [1]}}) == {}
    assert _parse_run_limits({"limits": {"timeout_s": True, "memory_mb": False}}) == {}


# ---------------------------------------------------------------------------
# Сборка RunSpec с per-run override (grader_core._prepare_run_spec)
# ---------------------------------------------------------------------------


def _stdin_case() -> grader.TestCase:
    return grader.TestCase(index=1, input_lines=["1"], expected_lines=["1"], test_type="stdin")


def test_prepare_run_spec_threads_memory_override(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "s.py"
    sol.write_text("print(1)\n", encoding="utf-8")

    plan = _prepare_run_spec(
        sol, _stdin_case(), timeout=5.0, measure_memory=False, cancel_event=None, max_memory_mb=64
    )

    assert plan.spec is not None
    assert plan.spec.max_memory_mb == 64
    assert plan.spec.timeout == 5.0


def test_prepare_run_spec_defaults_memory_to_config(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "s.py"
    sol.write_text("print(1)\n", encoding="utf-8")

    plan = _prepare_run_spec(
        sol, _stdin_case(), timeout=5.0, measure_memory=False, cancel_event=None
    )

    assert plan.spec is not None
    assert plan.spec.max_memory_mb == CONFIG.max_memory_mb


# ---------------------------------------------------------------------------
# Сквозной путь: grade_path прокидывает per-run timeout до раннера
# ---------------------------------------------------------------------------


def test_grade_path_applies_per_run_timeout(tmp_path: pathlib.Path) -> None:
    """grade_path(timeout=0.5) режет бесконечный цикл по per-run дедлайну."""
    sol = tmp_path / "loop.py"
    sol.write_text("while True:\n    pass\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")

    data = web.grade_path(sol, timeout=0.5)

    assert data["rows"][0]["cases"][0]["verdict"] == "TLE"
