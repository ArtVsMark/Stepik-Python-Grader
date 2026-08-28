"""У ночных находок есть адресат (issue #1384, правило 142).

Шесть проверок ночного обхода писали находки в summary прогона — то есть туда,
куда идут, уже зная, что там что-то есть. Скрипт ведёт вместо этого одну задачу
в трекере: пока находки есть, она открыта и обновляется; стало чисто — она
закрывается со словами о том, что стало чисто.

В сеть не ходит ни один тест: предмет здесь — решение о задаче, а не GitHub.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_TODAY = datetime.date(2026, 8, 27)


def _load() -> Any:
    path = _ROOT / "scripts" / "nightly_checks.py"
    spec = importlib.util.spec_from_file_location("nightly_checks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("nightly_checks", module)
    spec.loader.exec_module(module)
    return module


nightly = _load()


def _outcome(name: str, code: int, output: str = "нашлось") -> Any:
    return nightly.Outcome(nightly.Check(name, ["scripts/x.py"], "о чём проверка"), code, output)


class TestOutcomes:
    def test_zero_is_clean(self) -> None:
        assert _outcome("а", 0).clean is True

    def test_one_is_a_finding_not_a_breakage(self) -> None:
        """Находка чинится в трекере; механизм при этом исправен."""
        item = _outcome("а", 1)

        assert item.clean is False
        assert item.broken is False

    def test_two_is_a_broken_mechanism(self) -> None:
        """Третий исход: проверка не отработала — чинить здесь (правило 039)."""
        assert _outcome("а", 2).broken is True

    def test_catalogue_token_is_substituted(self, tmp_path: pathlib.Path) -> None:
        seen: list[list[str]] = []

        def runner(argv: list[str]) -> tuple[int, str]:
            seen.append(argv)
            return 0, ""

        check = nightly.Check("а", ["scripts/x.py", "--catalogue", "<catalogue>"], "о чём")
        nightly.run_checks(tmp_path, checks=(check,), runner=runner)

        assert seen == [["scripts/x.py", "--catalogue", str(tmp_path)]]


class TestIssueBody:
    def test_clean_run_says_so_in_words(self) -> None:
        """Пустая задача читалась бы как «обход не отработал» (правило 027)."""
        body = nightly.issue_body([_outcome("а", 0), _outcome("б", 0)], _TODAY)

        assert "Находок нет" in body
        assert "2 проверки" in body
        assert _TODAY.isoformat() in body

    def test_marker_is_the_first_line(self) -> None:
        """По нему задача находится в следующий раз — не по номеру и не по имени."""
        assert nightly.issue_body([], _TODAY).splitlines()[0] == nightly.MARKER

    def test_findings_are_named_with_their_output(self) -> None:
        body = nightly.issue_body([_outcome("Реестр отстал", 1, "строка находки")], _TODAY)

        assert "Реестр отстал — находка" in body
        assert "строка находки" in body

    def test_broken_mechanism_is_named_apart(self) -> None:
        """«Нашли проблему» и «проверка не отработала» — разные действия."""
        body = nightly.issue_body([_outcome("Защита main", 2, "нет прав")], _TODAY)

        assert "механизм не отработал" in body
        assert "не отработало — 1" in body

    def test_silent_check_does_not_produce_an_empty_block(self) -> None:
        body = nightly.issue_body([_outcome("Тихая", 1, "")], _TODAY)

        assert "(проверка ничего не напечатала)" in body


class TestApply:
    """Решение о задаче: завести, обновить, закрыть."""

    @pytest.fixture
    def api(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        state: dict[str, Any] = {"created": [], "updated": [], "closed": [], "existing": []}
        monkeypatch.setattr(nightly.gh_rest, "issues_with_label", lambda *a, **k: state["existing"])
        monkeypatch.setattr(nightly.gh_rest, "ensure_label", lambda *a, **k: True)
        monkeypatch.setattr(
            nightly.gh_rest,
            "create_issue",
            lambda repo, **kwargs: state["created"].append(kwargs) or {"number": 500},
        )
        monkeypatch.setattr(
            nightly.gh_rest,
            "update_issue",
            lambda repo, number, **kwargs: state["updated"].append(number) or {},
        )
        monkeypatch.setattr(
            nightly.gh_rest,
            "close_issue",
            lambda repo, number, **kwargs: state["closed"].append(number) or {},
        )
        return state

    def _run(
        self, monkeypatch: pytest.MonkeyPatch, catalogue: pathlib.Path, codes: list[int]
    ) -> int:
        outcomes = [_outcome(f"проверка {index}", code) for index, code in enumerate(codes)]
        monkeypatch.setattr(nightly, "run_checks", lambda *a, **k: outcomes)
        return nightly.main(
            ["--catalogue", str(catalogue), "--apply", "--today", _TODAY.isoformat()]
        )

    def test_first_finding_opens_the_issue(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        assert self._run(monkeypatch, tmp_path, [0, 1]) == 0
        assert len(api["created"]) == 1
        assert nightly.MARKER in api["created"][0]["body"]

    def test_second_run_updates_the_same_issue(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Новая задача на каждый обход превратила бы трекер в ленту."""
        api["existing"] = [{"number": 77, "body": f"{nightly.MARKER}\nстарое тело"}]

        assert self._run(monkeypatch, tmp_path, [1]) == 0
        assert api["updated"] == [77]
        assert api["created"] == []

    def test_clean_run_closes_the_issue(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Открытая задача без находок врёт так же, как отсутствие задачи."""
        api["existing"] = [{"number": 77, "body": f"{nightly.MARKER}\nстарое тело"}]

        assert self._run(monkeypatch, tmp_path, [0, 0]) == 0
        assert api["updated"] == [77], "перед закрытием тело обновляется: иначе оно врёт"
        assert api["closed"] == [77]

    def test_clean_run_without_issue_creates_nothing(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        assert self._run(monkeypatch, tmp_path, [0]) == 0
        assert api["created"] == []
        assert api["closed"] == []

    def test_foreign_issue_with_the_label_is_not_taken(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Метку мог поставить человек: своя задача узнаётся маркером."""
        api["existing"] = [{"number": 88, "body": "чужая задача с той же меткой"}]

        self._run(monkeypatch, tmp_path, [1])

        assert api["updated"] == []
        assert len(api["created"]) == 1

    def test_broken_mechanism_does_not_redden_the_run(
        self, api: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Красный ночной прогон — снова сигнал без адресата."""
        assert self._run(monkeypatch, tmp_path, [2]) == 0
        assert len(api["created"]) == 1


def test_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Без --apply скрипт печатает и выходит."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("написали в трекер без --apply")

    monkeypatch.setattr(nightly.gh_rest, "issues_with_label", refuse)
    monkeypatch.setattr(nightly, "run_checks", lambda *a, **k: [_outcome("а", 1)])

    assert nightly.main(["--catalogue", str(tmp_path), "--today", _TODAY.isoformat()]) == 0


def test_every_check_names_its_subject() -> None:
    """Находку без предмета нельзя понять, не открыв скрипт."""
    for check in nightly.CHECKS:
        assert len(check.about) > 20, f"{check.name}: предмет проверки не назван"
        assert check.argv[0].startswith("scripts/"), check.name
