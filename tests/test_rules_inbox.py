"""Постоянный issue «входящие» для нерассмотренных правил каталога.

Зазор между появлением правила и решением по нему не был виден нигде:
`.rules/bindings.json` честно писал `unreviewed`, но по расписанию файл никто не
открывает. Тесты стерегут то, ради чего механизм заведён: issue **один**
(находится по маркеру, а не по номеру), метрика считает **возраст**, а не только
число, и запись идёт только по `--apply`.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    """Загрузить скрипт как модуль: `scripts/` не пакет."""
    path = _ROOT / "scripts" / "rules_inbox.py"
    spec = importlib.util.spec_from_file_location("rules_inbox", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("rules_inbox", module)
    spec.loader.exec_module(module)
    return module


inbox = _load()

_TODAY = dt.date(2026, 8, 26)

_EXPORT = {
    "rules": [
        {"id": "001", "slug": "a", "added": "2026-08-01", "title": {"ru": "Первое"}},
        {"id": "002", "slug": "b", "added": "2026-08-20", "title": {"ru": "Второе"}},
        {"id": "003", "slug": "c", "added": "2026-08-25", "title": {"ru": "Третье"}},
    ]
}
_BINDINGS = {
    "rules": {
        "001": {"status": "unreviewed"},
        "002": {"status": "active", "mechanism": "gate"},
        # 003 в ответе отсутствует вовсе
    }
}


def test_pending_covers_unreviewed_and_unanswered() -> None:
    pending = inbox.pending_rules(_BINDINGS, _EXPORT)

    assert [(rule["id"], rule["state"]) for rule in pending] == [
        ("001", "unreviewed"),
        ("003", "no-answer"),
    ]


def test_decided_rules_are_not_pending() -> None:
    """Принятое правило во входящих не висит — иначе список бессмыслен."""
    assert all(rule["id"] != "002" for rule in inbox.pending_rules(_BINDINGS, _EXPORT))


def test_oldest_first() -> None:
    pending = inbox.pending_rules(_BINDINGS, _EXPORT)

    assert pending[0]["added"] < pending[-1]["added"]


def test_body_reports_age_not_only_count() -> None:
    """Возраст растёт сам и показывает запущенность, число — только объём."""
    body = inbox.issue_body(inbox.pending_rules(_BINDINGS, _EXPORT), _TODAY)

    assert "Нерассмотренных: 2" in body
    assert "Самому старому: 25 дн" in body


def test_body_starts_with_marker() -> None:
    """По маркеру issue находится в следующем прогоне — номер хранить негде."""
    body = inbox.issue_body(inbox.pending_rules(_BINDINGS, _EXPORT), _TODAY)

    assert body.startswith(inbox.MARKER)


def test_empty_inbox_stays_open_and_says_so() -> None:
    body = inbox.issue_body([], _TODAY)

    assert "Нерассмотренных правил нет" in body
    assert "остаётся открытым" in body
    assert inbox.issue_title([]) == "🧭 Входящие каталога правил: разобрано всё"


def test_unanswered_rule_is_marked_apart() -> None:
    """«Ответа нет вовсе» и «не дошли руки» — разные состояния, и видно какое."""
    body = inbox.issue_body(inbox.pending_rules(_BINDINGS, _EXPORT), _TODAY)

    assert "**ответа нет вовсе**" in body
    assert "не рассмотрено" in body


def test_missing_dates_do_not_break_the_metric() -> None:
    pending = [{"id": "009", "slug": "x", "added": "", "title": "без даты", "state": "unreviewed"}]

    body = inbox.issue_body(pending, _TODAY)

    assert "Самому старому: неизвестно" in body


def test_show_mode_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Умолчание показывает тело; в трекер не ходит вовсе."""
    calls: list[str] = []
    monkeypatch.setattr(
        inbox.gh_rest, "request", lambda *a, **k: calls.append(str(a)) or pytest.fail("сеть")
    )
    export = tmp_path / "export"
    export.mkdir()
    (export / "rules.json").write_text('{"rules": []}', encoding="utf-8")

    code = inbox.main(["--catalogue", str(tmp_path)])

    assert code == inbox.EXIT_OK
    assert calls == []


def test_missing_catalogue_is_unknown(tmp_path: pathlib.Path) -> None:
    assert inbox.main(["--catalogue", str(tmp_path)]) == inbox.EXIT_UNKNOWN


def test_live_bindings_parse() -> None:
    """Настоящий ответ проекта: разбор не должен ломаться на его форме."""
    import json

    data = json.loads((_ROOT / ".rules" / "bindings.json").read_text(encoding="utf-8"))

    assert inbox.pending_rules(data, {"rules": []}) == []
    assert isinstance(data.get("rules"), dict)
