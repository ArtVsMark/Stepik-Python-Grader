"""Прогнан каждый объявленный исход, а не первый (issue #1384, правило 145).

Прогон одного пути подтверждает, что механизм запускается, — и ничего больше.
Ветка, которую никто не видел работающей, обычно и оказывается сломанной: у
неё нет ни одного свидетеля.

Бюджет здесь — храповик: он показывает долг числом и обязан уменьшаться. Тесты
стерегут именно это свойство, а не конкретное значение.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_declared_outcomes.py"
    spec = importlib.util.spec_from_file_location("check_declared_outcomes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_declared_outcomes", module)
    spec.loader.exec_module(module)
    return module


guard = _load()

_TWO_CODES = "def main() -> int:\n    if problems:\n        return 1\n    return 0\n"


class TestDeclaredCodes:
    def test_literal_codes_are_collected(self) -> None:
        assert guard.declared_codes(_TWO_CODES) == {0, 1}

    def test_named_codes_are_collected_too(self) -> None:
        """`EXIT_WAIT` и `2` — один исход, а не два: иначе счёт бы врал."""
        source = "def main() -> int:\n    return gh_rest.EXIT_WAIT\n"

        assert guard.declared_codes(source) == {2}

    def test_broken_source_gives_nothing(self) -> None:
        assert guard.declared_codes("def (") == set()


class TestFindings:
    def test_unrun_failure_branch_is_flagged(self) -> None:
        problems = guard.outcomes_never_run(
            {"страж.py": _TWO_CODES},
            {"test_страж.py": "def test_ok():\n    assert страж.main() == 0\n"},
        )

        assert problems == [("страж.py", 1)]

    def test_run_failure_branch_passes(self) -> None:
        problems = guard.outcomes_never_run(
            {"страж.py": _TWO_CODES},
            {"test_страж.py": "def test_bad():\n    assert страж.main() == 1\n"},
        )

        assert problems == []

    def test_named_code_in_tests_counts(self) -> None:
        """Форму не навязываем: `== gh_rest.EXIT_WAIT` — тот же прогон."""
        source = (
            "def main() -> int:\n    if wait:\n        return gh_rest.EXIT_WAIT\n    return 0\n"
        )
        problems = guard.outcomes_never_run(
            {"страж.py": source},
            {"test_страж.py": "def t():\n    assert страж.main() == gh_rest.EXIT_WAIT\n"},
        )

        assert problems == []

    def test_success_path_is_not_required(self) -> None:
        """Успешный путь прогоняет живой предмет; обряда ради обряда не нужно."""
        problems = guard.outcomes_never_run(
            {"страж.py": _TWO_CODES},
            {"test_страж.py": "def test_bad():\n    assert страж.main() == 1\n"},
        )

        assert all(code != 0 for _name, code in problems)

    def test_single_outcome_script_is_silent(self) -> None:
        """Один исход — прогонять «каждый» нечего, правило молчит."""
        problems = guard.outcomes_never_run(
            {"простой.py": "def main() -> int:\n    return 0\n"}, {"test_простой.py": "простой"}
        )

        assert problems == []

    def test_script_without_tests_is_not_this_gate_subject(self) -> None:
        """Об отсутствии тестов говорит check_gate_tests.py — не двое об одном."""
        assert guard.outcomes_never_run({"страж.py": _TWO_CODES}, {}) == []

    def test_declared_debt_is_skipped(self) -> None:
        known = next(iter(guard.KNOWN_DEBT))

        assert guard.outcomes_never_run({known: _TWO_CODES}, {f"test_{known}": known}) == []


class TestBudget:
    def test_budget_matches_reality(self) -> None:
        """Бюджет, оторвавшийся от факта, перестаёт быть храповиком."""
        actual = len(guard.outcomes_never_run())

        assert actual <= guard.BUDGET, (
            f"непрогнанных исходов {actual} при бюджете {guard.BUDGET}. "
            "Бюджет опускают починкой, а не правкой числа."
        )
        assert guard.BUDGET - actual <= 2, (
            f"бюджет {guard.BUDGET} против фактических {actual}: опустите бюджет, "
            "иначе он молча разрешает будущую поломку."
        )

    def test_main_is_green_within_budget(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert guard.main() == 0
        assert "при бюджете" in capsys.readouterr().out

    def test_main_is_red_above_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ветка отказа этого гейта — тоже прогон, а не только описание."""
        monkeypatch.setattr(guard, "BUDGET", 0)
        monkeypatch.setattr(guard, "outcomes_never_run", lambda: [("страж.py", 1)])

        assert guard.main() == 1
