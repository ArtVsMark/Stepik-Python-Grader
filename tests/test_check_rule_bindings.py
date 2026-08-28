"""Tests for scripts/check_rule_bindings.py — ответ каталогу правил (#1351).

Каталог отдаёт правила машиночитаемо, проект отвечает, что с каждым сделал.
Ответ — это **декларация**, и проверять её надо на расхождение с фактом: путь,
названный в `where`, обязан существовать, а отрицательное решение — нести
причину, иначе через полгода оно неотличимо от «не дошли руки».

Отдельно проверяется метрика: `unreviewed` плюс `active` с `mechanism: none` —
это правила, принятые на словах. Она и есть предмет задачи, поэтому обязана
считаться честно, а не «в приятную сторону».
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_rule_bindings.py"
_BINDINGS = pathlib.Path(__file__).parent.parent / ".rules" / "bindings.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_rule_bindings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _data(rules: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "1.0", "project": "x/y", "catalogue": "https://example", "rules": rules}


# --- состояние репозитория ----------------------------------------------------


def test_repository_answer_is_valid() -> None:
    """Приёмка #1351: проект отвечает каталогу, и ответ сходится с фактом."""
    assert _MODULE.main([]) == 0


def test_answer_file_exists_and_parses() -> None:
    data = json.loads(_BINDINGS.read_text(encoding="utf-8"))
    assert data["schema"] == "1.0"
    assert data["project"] == "ArtVsMark/Stepik-Python-Grader"
    assert data["rules"], "пустой ответ — то же самое, что отсутствие ответа"


def test_metric_counts_rules_held_by_nothing() -> None:
    """Метрика — предмет задачи: она обязана быть ненулевой и честной."""
    data = json.loads(_BINDINGS.read_text(encoding="utf-8"))
    unheld, total = _MODULE.unheld_count(data)

    assert total > 100, "ответ нужен по каждому правилу каталога"
    assert unheld > 0, (
        "нулевая метрика на старте означала бы, что она считается в приятную "
        "сторону: правила, принятые на словах, у нас есть"
    )


# --- проверка контракта -------------------------------------------------------


def test_active_without_mechanism_is_a_violation() -> None:
    """«Принято» без ответа «чем держится» и есть фикция."""
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "where": "CLAUDE.md"}})
    )
    assert any("механизм" in problem for problem in problems), problems


def test_active_without_where_is_a_violation() -> None:
    problems = _MODULE.binding_violations(_data({"001": {"status": "active", "mechanism": "gate"}}))
    assert any("where" in problem for problem in problems), problems


def test_where_pointing_at_a_missing_file_is_a_violation(tmp_path: pathlib.Path) -> None:
    """Декларация обязана сходиться с фактом: предмет мог исчезнуть."""
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "gate", "where": "scripts/нет-такого.py"}}),
        root=tmp_path,
    )
    assert any("нет-такого.py" in problem for problem in problems), problems


def test_where_describing_a_step_is_not_treated_as_a_path() -> None:
    """`where` бывает разделом свода, а не файлом — это законно."""
    problems = _MODULE.binding_violations(
        _data(
            {
                "001": {
                    "status": "active",
                    "mechanism": "process-step",
                    "where": "ревью документации",
                }
            }
        )
    )
    assert problems == [], problems


@pytest.mark.parametrize("status", ["rejected", "not-applicable"])
def test_negative_decision_needs_a_reason(status: str) -> None:
    """Отрицательное решение без причины через полгода не отличить от забывчивости."""
    problems = _MODULE.binding_violations(_data({"001": {"status": status}}))
    assert any("причины" in problem for problem in problems), problems


def test_unknown_status_is_reported() -> None:
    problems = _MODULE.binding_violations(_data({"001": {"status": "почти-принято"}}))
    assert any("статус" in problem for problem in problems), problems


def test_empty_rules_is_a_failure() -> None:
    """Гейт без предмета проверки обязан падать, а не зеленеть на пустоте."""
    assert _MODULE.binding_violations(_data({})) != []


def test_wrong_schema_is_reported() -> None:
    """Версия контракта — не украшение: сломать формат значит сломать читателей."""
    data = _data({"001": {"status": "unreviewed"}})
    data["schema"] = "2.0"
    assert any("schema" in problem for problem in _MODULE.binding_violations(data)), data


# --- метрика ------------------------------------------------------------------


def test_unheld_counts_unreviewed_and_none() -> None:
    """Не обеспечено ничем — это и «не смотрели», и «принято без механизма»."""
    unheld, total = _MODULE.unheld_count(
        _data(
            {
                "001": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"},
                "002": {"status": "active", "mechanism": "none", "where": "CLAUDE.md"},
                "003": {"status": "unreviewed"},
                "004": {"status": "rejected", "why": "иначе решили"},
            }
        )
    )
    assert (unheld, total) == (2, 4)


class TestUnheldBudget:
    """Храповик: правило без механизма обязано быть записано документом.

    Бюджет — не «столько допустимо», а «столько осталось». Поэтому две стороны:
    гейт краснеет при превышении и — отдельным тестом — само число сверяется с
    реальностью, иначе бюджет тихо разойдётся с ответом и перестанет что-либо
    держать.
    """

    def test_budget_matches_reality(self) -> None:
        data = json.loads(_BINDINGS.read_text(encoding="utf-8"))

        unheld, _total = _MODULE.unheld_count(data)

        assert unheld <= _MODULE.UNHELD_BUDGET, (
            f"не обеспечено ничем {unheld} при бюджете {_MODULE.UNHELD_BUDGET}. "
            "Бюджет опускают починкой, а не правкой числа."
        )

    def test_live_answer_is_green(self) -> None:
        assert _MODULE.main([]) == 0

    def test_exceeding_the_budget_is_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_MODULE, "UNHELD_BUDGET", -1)

        assert _MODULE.main([]) == 1
