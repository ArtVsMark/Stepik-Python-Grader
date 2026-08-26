"""Гейт защиты ``main`` (issue #1296).

Проверка сверяет ruleset с тем, что проект утверждает публично: список обходов
пуст, обязательных проверок ровно одиннадцать, ветка обязана быть свежей.
Тесты гоняют разбор на фикстурах — в сеть не ходит ни один из них: предмет
проверки здесь логика сверки, а не доступность GitHub.
"""

from __future__ import annotations

import copy
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    """Загрузить скрипт как модуль: `scripts/` не пакет."""
    path = _ROOT / "scripts" / "check_branch_protection.py"
    spec = importlib.util.spec_from_file_location("check_branch_protection", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_branch_protection", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _ruleset() -> dict[str, Any]:
    """Здоровый ruleset — снимок настоящего, снятый 26.08.2026."""
    return {
        "name": "main: зелёный CI на актуальном состоянии",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": name} for name in guard.EXPECTED_CHECKS],
                },
            },
        ],
    }


def test_healthy_ruleset_has_no_problems() -> None:
    assert guard.check_ruleset(_ruleset()) == []


def test_non_empty_bypass_list_is_reported() -> None:
    """Ровно то утверждение, которое витрина профиля делает публично."""
    data = _ruleset()
    data["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole"}]

    problems = guard.check_ruleset(data)

    assert len(problems) == 1
    assert "список обходов НЕ пуст" in problems[0]


def test_missing_required_check_is_reported() -> None:
    """Проверку убрали из ruleset — мерж перестал её ждать."""
    data = _ruleset()
    checks = data["rules"][2]["parameters"]["required_status_checks"]
    dropped = checks.pop()["context"]

    problems = guard.check_ruleset(data)

    assert any(dropped in problem and "не хватает" in problem for problem in problems)


def test_extra_required_check_is_reported() -> None:
    """Лишняя проверка — либо ruleset правили молча, либо устарел скрипт."""
    data = _ruleset()
    data["rules"][2]["parameters"]["required_status_checks"].append(
        {"context": "test (ubuntu-latest, 3.14, true)"}
    )

    problems = guard.check_ruleset(data)

    assert any("больше заявленного" in problem for problem in problems)


def test_stale_branch_policy_is_reported() -> None:
    data = _ruleset()
    data["rules"][2]["parameters"]["strict_required_status_checks_policy"] = False

    assert any("свежей" in problem for problem in guard.check_ruleset(data))


@pytest.mark.parametrize("kind", guard.REQUIRED_RULES)
def test_missing_protection_rule_is_reported(kind: str) -> None:
    data = _ruleset()
    data["rules"] = [rule for rule in data["rules"] if rule.get("type") != kind]

    assert any(kind in problem for problem in guard.check_ruleset(data))


def test_disabled_ruleset_is_reported() -> None:
    """Выключенный набор правил отдаётся API целиком и выглядит здоровым."""
    data = _ruleset()
    data["enforcement"] = "disabled"

    assert any("не активен" in problem for problem in guard.check_ruleset(data))


def test_ruleset_without_status_checks_is_reported() -> None:
    data = _ruleset()
    data["rules"] = [rule for rule in data["rules"] if rule.get("type") != "required_status_checks"]

    problems = guard.check_ruleset(data)

    assert any("обязательных проверок нет вовсе" in problem for problem in problems)


def test_ci_jobs_are_checked_against_workflow() -> None:
    """Джоб переименовали в ci.yml, а в ruleset осталось старое имя."""
    text = "jobs:\n  static:\n  e2e:\n  docs-guardrails:\n  supply-chain:\n"

    problems = guard.check_ci_jobs(text)

    assert any("sandbox-linux" in problem for problem in problems)
    assert not any("static" in problem for problem in problems)


def test_real_workflow_declares_every_plain_job() -> None:
    """Не фикстура, а настоящий `ci.yml`: дрейф имён ловится здесь же."""
    text = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert guard.check_ci_jobs(text) == []


def test_expected_checks_match_documented_count() -> None:
    """Одиннадцать — число, которым свод и витрина оперируют вслух."""
    assert len(guard.EXPECTED_CHECKS) == 11
    assert len(set(guard.EXPECTED_CHECKS)) == 11


def test_fixture_is_not_mutated_between_cases() -> None:
    """Страховка от теста, зелёного из-за общего изменяемого состояния."""
    first = _ruleset()
    second = copy.deepcopy(first)
    guard.check_ruleset(first)

    assert first == second
