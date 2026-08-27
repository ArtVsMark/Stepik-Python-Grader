"""Карточка TLE называет лимит, который был превышен (issue #962, TW-1-01).

Контракт `web-contracts.md` обещает для `timeout_s` именно превышенный лимит, а
в карточку уезжала константа конфига. Веб при этом принимает `timeout_s` в
запросе и честно грейдит с ним — то есть число врало ровно там, где оно нужно:
человек смотрит на карточку, чтобы понять, во сколько не уложился.
"""

from __future__ import annotations

from typing import Any

import pytest

from stepik_grader import config
from stepik_grader.web import viewmodels


def _tle_case() -> dict[str, Any]:
    return {
        "passed": False,
        "output": [],
        "expected": ["42"],
        "diff": "",
        "time": 5.0,
        "memory": 0.0,
        "error": "Timeout after 1.5s",
        "timed_out": True,
        "verdict": "TLE",
        "exit_code": None,
    }


def test_card_reports_the_limit_that_was_exceeded() -> None:
    """Прогон шёл с 1.5 с — карточка обязана назвать 1.5, а не конфиг."""
    view = viewmodels._case_view(1, _tle_case(), timeout_s=1.5)

    assert view["timeout_s"] == 1.5


def test_without_explicit_timeout_config_is_used() -> None:
    """Прогон по умолчанию — значение конфига, поведение прежнее."""
    view = viewmodels._case_view(1, _tle_case())

    assert view["timeout_s"] == config.get_config().timeout_seconds


def test_config_is_read_at_call_time() -> None:
    """`--timeout` и настройки лаунчера обязаны доходить сюда."""
    config.override_config(timeout_seconds=9.0)
    try:
        view = viewmodels._case_view(1, _tle_case())
        assert view["timeout_s"] == 9.0
    finally:
        config.reset_config_cache()


@pytest.mark.parametrize("verdict", ["WA", "RE", "OK"])
def test_field_appears_only_for_tle(verdict: str) -> None:
    """Поле контрактно только у TLE — лишнее там читалось бы как лимит прогона."""
    case = _tle_case() | {"verdict": verdict, "timed_out": verdict == "TLE"}

    view = viewmodels._case_view(1, case, timeout_s=1.5)

    assert "timeout_s" not in view


def test_zero_timeout_is_not_swallowed() -> None:
    """0 — валидный лимит, а `or` подменил бы его конфигом."""
    view = viewmodels._case_view(1, _tle_case(), timeout_s=0.0)

    assert view["timeout_s"] == 0.0
