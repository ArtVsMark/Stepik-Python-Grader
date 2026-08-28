"""Работа без прикреплённого изменения (правило 147).

Переключатель здесь — префикс ветки: PR открывает `agent-pr.yml`, и только для
`agent/**`. Ветка с другим именем не получает PR **вовсе** — не отказ, а
отсутствие прогона: ни красного, ни лога, ни кода возврата. Заметить это может
только тот, кто смотрит на ветки, и вот он.

Двусторонний набор плюс граница отсрочки: только что запушенная ветка законно
ещё без PR, и находкой её называть нельзя — иначе ночной обход будет ругаться
на каждую нормальную работу.

В сеть не ходит ни один тест: состояние подставляется.
"""

from __future__ import annotations

import datetime as _datetime
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_orphan_branches.py"
    spec = importlib.util.spec_from_file_location("check_orphan_branches", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_orphan_branches", module)
    spec.loader.exec_module(module)
    return module


guard = _load()

_NOW = _datetime.datetime(2026, 8, 28, 12, 0, tzinfo=_datetime.UTC)


def _branch(name: str, sha: str = "abc") -> dict[str, Any]:
    return {"name": name, "commit": {"sha": sha}}


def _ago(hours: float) -> _datetime.datetime:
    return _NOW - _datetime.timedelta(hours=hours)


class TestFindings:
    def test_branch_without_a_pull_request_is_found(self) -> None:
        """Главный случай: работа на ветке есть, изменения нет."""
        found = guard.orphans(
            [_branch("rule/144-context-window")],
            attached=set(),
            committed={"abc": _ago(30)},
            now=_NOW,
        )

        assert len(found) == 1
        assert "rule/144-context-window" in found[0]
        assert "30 ч назад" in found[0]

    def test_branch_with_a_pull_request_is_silent(self) -> None:
        found = guard.orphans(
            [_branch("agent/gate")],
            attached={"agent/gate"},
            committed={},
            now=_NOW,
        )

        assert found == []

    def test_fresh_branch_is_not_a_finding(self) -> None:
        """PR открывается расписанием: свежая ветка законно ещё без него."""
        found = guard.orphans(
            [_branch("agent/gate")],
            attached=set(),
            committed={"abc": _ago(1)},
            hours=6,
            now=_NOW,
        )

        assert found == []

    def test_the_agent_prefix_does_not_excuse_a_missing_pull_request(self) -> None:
        """Правильное имя — не гарантия: workflow мог не сработать вовсе."""
        found = guard.orphans(
            [_branch("agent/gate")],
            attached=set(),
            committed={"abc": _ago(24)},
            now=_NOW,
        )

        assert found != []

    def test_service_branches_are_skipped(self) -> None:
        """`main` и `badges` живут без PR по устройству."""
        found = guard.orphans(
            [_branch("main", "one"), _branch("badges", "two")],
            attached=set(),
            committed={"one": _ago(100), "two": _ago(100)},
            now=_NOW,
        )

        assert found == []

    def test_missing_date_is_reported_not_dropped(self) -> None:
        """Дату не прочитали — это находка, а не «ветка в порядке»."""
        found = guard.orphans(
            [_branch("rule/no-date")],
            attached=set(),
            committed={"abc": None},
            now=_NOW,
        )

        assert found and "даты последнего коммита нет" in found[0]


class TestBoundary:
    @pytest.mark.parametrize(("age", "expected"), [(5.9, 0), (6.0, 1), (6.1, 1)])
    def test_grace_is_inclusive_at_the_threshold(self, age: float, expected: int) -> None:
        found = guard.orphans(
            [_branch("rule/x")],
            attached=set(),
            committed={"abc": _ago(age)},
            hours=6,
            now=_NOW,
        )

        assert len(found) == expected


class TestCommitDate:
    def test_iso_date_with_zulu_is_parsed(self) -> None:
        stamp = guard._committed_at({"commit": {"committer": {"date": "2026-08-28T06:00:00Z"}}})

        assert stamp == _datetime.datetime(2026, 8, 28, 6, 0, tzinfo=_datetime.UTC)

    def test_garbage_shape_is_none_not_a_crash(self) -> None:
        """Форма ответа изменилась — это «даты нет», а не падение обхода."""
        assert guard._committed_at({"commit": {}}) is None
        assert guard._committed_at("не словарь") is None
        assert guard._committed_at({"commit": {"committer": {"date": "вчера"}}}) is None


class TestOutcomes:
    def test_finding_is_code_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(guard, "orphan_branches", lambda *a, **k: ["rule/x — PR не открыт"])

        assert guard.main([]) == 1

    def test_clean_is_code_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(guard, "orphan_branches", lambda *a, **k: [])

        assert guard.main([]) == 0

    def test_exhausted_quota_says_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """«Подожди» отличается от «сломалось»: повторять первое имеет смысл."""

        def refuse(*_args: object, **_kwargs: object) -> list[str]:
            raise guard.gh_rest.RateLimited("лимит исчерпан")

        monkeypatch.setattr(guard, "orphan_branches", refuse)

        assert guard.main([]) == guard.gh_rest.EXIT_WAIT

    def test_refused_github_is_the_third_outcome(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Трекер не прочитан — это не «веток без PR нет»."""

        def refuse(*_args: object, **_kwargs: object) -> list[str]:
            raise guard.gh_rest.GitHubError("403")

        monkeypatch.setattr(guard, "orphan_branches", refuse)

        assert guard.main([]) == 2
