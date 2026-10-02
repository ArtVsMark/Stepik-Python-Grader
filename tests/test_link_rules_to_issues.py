"""Обратная ссылка из задачи в правило каталога.

Связь была односторонней: у правила след ведёт на задачу, у задачи о правиле ни
слова. Тесты стерегут два свойства, без которых механизм вреден: комментарий
**один** (а не новый на каждый ночной прогон) и запись происходит **только** по
явному `--apply`.

Сеть подменяется целиком: предмет здесь — решение «писать или не писать».
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    """Загрузить скрипт как модуль: `scripts/` не пакет."""
    path = _ROOT / "scripts" / "link_rules_to_issues.py"
    spec = importlib.util.spec_from_file_location("link_rules_to_issues", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("link_rules_to_issues", module)
    spec.loader.exec_module(module)
    return module


linker = _load()

_EXPORT = {
    "rules": [
        {
            "id": "091",
            "slug": "a-closed-issue-is-not-a-plan",
            "title": {"ru": "Закрытая задача — не план", "en": "A closed issue is not a plan"},
            "trails": [
                {"repo": "ArtVsMark/Stepik-Python-Grader", "issue": "97"},
                {"repo": "ArtVsMark/Stepik-Python-Grader", "issue": "151"},
            ],
        },
        {
            "id": "104",
            "slug": "an-inbox-needs-an-owner",
            "title": {"ru": "У входящих должен быть хозяин", "en": "An inbox needs an owner"},
            "trails": [
                {"repo": "ArtVsMark/Stepik-Python-Grader", "issue": "988"},
                {"repo": "ArtVsMark/ArtVsMark", "issue": "6"},
            ],
        },
        {
            "id": "120",
            "slug": "how-to-run-a-rule-catalogue",
            "title": {"ru": "Как вести каталог", "en": "How to run a catalogue"},
            "trails": [{"repo": "ArtVsMark/Engineering-Incidents-Playbook", "issue": "12"}],
        },
    ]
}


class _Recorder:
    """Подмена транспорта: помнит вызовы, в сеть не ходит."""

    def __init__(self, comments: list[dict[str, Any]] | None = None) -> None:
        self.comments = comments or []
        self.calls: list[tuple[str, str]] = []

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("read", f"{repo}#{number}"))
        return self.comments

    def comment_issue(self, repo: str, number: int, body: str) -> dict[str, Any]:
        self.calls.append(("create", body))
        return {"id": 1}

    def add_labels(self, repo: str, number: int, labels: list[str]) -> list[str]:
        self.calls.append(("label", labels[0]))
        return labels

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method.lower(), path))
        return type("R", (), {"data": {}})()


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(linker.gh_rest, "issue_comments", recorder.issue_comments)
    monkeypatch.setattr(linker.gh_rest, "comment_issue", recorder.comment_issue)
    monkeypatch.setattr(linker.gh_rest, "add_labels", recorder.add_labels)
    monkeypatch.setattr(linker.gh_rest, "request", recorder.request)
    return recorder


def test_backlinks_group_rules_by_issue() -> None:
    found = linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")

    assert set(found) == {97, 151, 988}
    assert [rule["id"] for rule in found[97]] == ["091"]


def test_backlinks_ignore_other_repositories() -> None:
    """След на витрину профиля и на сам каталог — не наша задача."""
    found = linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")

    assert 6 not in found
    assert 12 not in found


def test_backlinks_survive_malformed_entries() -> None:
    broken = {
        "rules": [{"id": "1", "trails": [{"repo": "ArtVsMark/Stepik-Python-Grader"}]}, "мусор"]
    }

    assert linker.backlinks(broken, "ArtVsMark/Stepik-Python-Grader") == {}


def test_comment_body_carries_hidden_marker_and_links() -> None:
    body = linker.comment_body(linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")[97])

    assert body.startswith(linker.MARKER)
    assert "091-a-closed-issue-is-not-a-plan.md" in body
    assert "Закрытая задача — не план" in body


def test_second_run_updates_the_same_comment(transport: _Recorder) -> None:
    """Главное свойство: ночной прогон не плодит комментарии."""
    rules = linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")[97]
    transport.comments = [{"id": 55, "body": linker.MARKER + "\nстарый текст"}]

    action = linker._sync_issue("owner/repo", 97, rules, linker.DEFAULT_LABEL)

    assert action == "комментарий обновлён"
    assert not any(kind == "create" for kind, _ in transport.calls)
    assert ("patch", "repos/owner/repo/issues/comments/55") in transport.calls


def test_unchanged_comment_is_left_alone(transport: _Recorder) -> None:
    rules = linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")[97]
    transport.comments = [{"id": 55, "body": linker.comment_body(rules)}]

    action = linker._sync_issue("owner/repo", 97, rules, linker.DEFAULT_LABEL)

    assert action == "комментарий уже верен"
    assert not any(kind in {"create", "patch"} for kind, _ in transport.calls)


def test_first_run_creates_one_comment_and_labels(transport: _Recorder) -> None:
    rules = linker.backlinks(_EXPORT, "ArtVsMark/Stepik-Python-Grader")[97]

    action = linker._sync_issue("owner/repo", 97, rules, linker.DEFAULT_LABEL)

    assert action == "комментарий добавлен"
    assert [kind for kind, _ in transport.calls] == ["read", "create", "label"]


def test_dry_run_writes_nothing(tmp_path: pathlib.Path, transport: _Recorder) -> None:
    """Умолчание сухое: «случайно запустил» не равно «прошёлся по трекеру»."""
    export = tmp_path / "export"
    export.mkdir()
    (export / "rules.json").write_text(json.dumps(_EXPORT, ensure_ascii=False), encoding="utf-8")

    code = linker.main(["--catalogue", str(tmp_path), "--repo", "ArtVsMark/Stepik-Python-Grader"])

    assert code == linker.EXIT_OK
    assert transport.calls == []


def test_missing_catalogue_is_unknown_not_failure(tmp_path: pathlib.Path) -> None:
    """«Прочитать нечем» и «плохо» ведут к разным действиям."""
    assert linker.main(["--catalogue", str(tmp_path)]) == linker.EXIT_UNKNOWN
