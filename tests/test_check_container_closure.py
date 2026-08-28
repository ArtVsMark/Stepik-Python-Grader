"""Закрытие контейнера не закрывает работу (issue #1384, правило 121).

Закрытый эпик с открытыми дочерними врёт дважды: снаружи направление выглядит
законченным, изнутри работа идёт, а объясняющий её эпик закрыт. Тесты проверяют
обе стороны гейта — что он это находит и что не поднимает шум там, где всё
сходится, — и отдельно третий исход: «трекер не прочитан» отличается от
«нарушений нет».

В сеть не ходит ни один тест: состояние трекера подставляется.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_container_closure.py"
    spec = importlib.util.spec_from_file_location("check_container_closure", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_container_closure", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _epic(number: int, state: str, title: str = "Направление") -> dict[str, Any]:
    return {"number": number, "state": state, "title": title}


def _kid(number: int, state: str) -> dict[str, Any]:
    return {"number": number, "state": state}


class TestFindings:
    def test_closed_epic_with_open_work_is_found(self) -> None:
        """Главный случай: контейнер говорит «готово», работа идёт."""
        found = guard.closure_mismatches(
            parents=[_epic(915, "closed")],
            children={915: [_kid(1, "closed"), _kid(2, "open"), _kid(3, "open")]},
        )

        assert len(found) == 1
        assert found[0].severe is True
        assert found[0].open_children == [2, 3]
        assert "#2, #3" in found[0].line()

    def test_open_epic_with_everything_closed_is_named_softly(self) -> None:
        """Может остаться приёмка — машине это не различить, и она не решает."""
        found = guard.closure_mismatches(
            parents=[_epic(915, "open")], children={915: [_kid(1, "closed")]}
        )

        assert len(found) == 1
        assert found[0].severe is False
        assert "осталась только приёмка" in found[0].line()

    def test_consistent_states_are_silent(self) -> None:
        found = guard.closure_mismatches(
            parents=[_epic(1, "open"), _epic(2, "closed")],
            children={1: [_kid(10, "open")], 2: [_kid(20, "closed")]},
        )

        assert found == []

    def test_epic_without_children_is_not_a_container(self) -> None:
        """Эпик без дочерних — обычная задача, счётчику нечего считать."""
        assert guard.closure_mismatches(parents=[_epic(5, "closed")], children={5: []}) == []

    def test_severe_findings_come_first(self) -> None:
        """Тяжёлое читают первым: у мягкого случая может не быть работы вовсе."""
        found = guard.closure_mismatches(
            parents=[_epic(1, "open"), _epic(9, "closed")],
            children={1: [_kid(10, "closed")], 9: [_kid(90, "open")]},
        )

        assert [item.parent for item in found] == [9, 1]

    def test_title_is_carried_into_the_report(self) -> None:
        """Номер без заголовка не говорит ничего тому, кто читает утром."""
        found = guard.closure_mismatches(
            parents=[_epic(915, "closed", "Входной слой вердикта")],
            children={915: [_kid(2, "open")]},
        )

        assert "Входной слой вердикта" in found[0].line()


class TestOutcomes:
    def test_clean_tracker_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(guard, "closure_mismatches", lambda *a, **k: [])

        assert guard.main([]) == 0

    def test_finding_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mismatch = guard.Mismatch(
            915, "Эпик", parent_closed=True, open_children=[2], closed_children=1
        )
        monkeypatch.setattr(guard, "closure_mismatches", lambda *a, **k: [mismatch])

        assert guard.main([]) == 1

    def test_unreadable_tracker_is_a_third_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """«Прочитать не удалось» не равно «нарушений нет» (правило 039)."""

        def boom(*args: object, **kwargs: object) -> None:
            raise guard.gh_rest.GitHubError("403: прав нет")

        monkeypatch.setattr(guard, "closure_mismatches", boom)

        assert guard.main([]) == 2

    def test_exhausted_quota_says_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Исчерпанная квота — «ждать», а не «сломалось»: повтор её не лечит."""

        def boom(*args: object, **kwargs: object) -> None:
            raise guard.gh_rest.RateLimited("лимит", reset_at=0, resource="core")

        monkeypatch.setattr(guard, "closure_mismatches", boom)

        assert guard.main([]) == guard.gh_rest.EXIT_WAIT
