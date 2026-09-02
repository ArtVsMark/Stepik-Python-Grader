"""Гейт «комплексный issue ведёт чек-лист» — разбор тела без сети.

Проверяется чистая логика ``scripts/check_issue_checklists.py``: что считается
комплексным issue, как выделяются строки чек-листа, что признаётся исходом
галочки. Сеть не трогается — ``fetch_open_issues`` принимает подменяемый
``opener``, и тесты пользуются им (тот же приём, что у двуязычного гейта).
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import check_issue_checklists as gate


def test_labels_mark_an_issue_as_complex() -> None:
    assert gate.is_complex("что угодно", ["epic"]) is True
    assert gate.is_complex("что угодно", ["audit", "bug"]) is True
    assert gate.is_complex("обычный баг", ["bug"]) is False


def test_title_prefix_marks_an_issue_as_complex() -> None:
    """Метка иногда отстаёт от факта — заголовок эпика распознаётся сам по себе."""
    assert gate.is_complex("🎯 [Подэпик] Молчание вместо ошибки", []) is True
    assert gate.is_complex("🎯 [Хвост] Ядро проверки — 282 находки", []) is True
    assert gate.is_complex("fix(core): вывод без границ", []) is False


def test_checklist_items_read_marks_and_text() -> None:
    body = "- [ ] `RUN-1-01` — пара кейса\n- [x] `RUN-4-02` — границы вывода (#1182)\n"

    items = gate.checklist_items(body)

    assert [item.done for item in items] == [False, True]
    assert "RUN-4-02" in items[1].text


def test_bulk_listed_ids_finds_findings_outside_a_checklist() -> None:
    """Таблица находок — ровно та форма, из-за которой состояние не видно."""
    body = (
        "| Находка | file:line | Что осталось |\n"
        "|---|---|---|\n"
        "| REV-1-01 | `test_loader.py:187` | вернулась целиком |\n"
        "| REV-1-02 | `test_loader.py:228` | тот же файл читается без errors |\n"
        "| REV-3-02 | `api_routes.py:845` | веб не проверяет получателя |\n"
    )

    assert gate.bulk_listed_ids(body) == ["REV-1-01", "REV-1-02", "REV-3-02"]


def test_bulk_listed_ids_ignores_checklist_lines() -> None:
    body = "- [ ] `REV-1-01` — вернулась целиком\n- [x] `REV-1-02` — (#1128)\n"

    assert gate.bulk_listed_ids(body) == []


def test_bulk_list_of_three_findings_is_reported() -> None:
    body = "\n".join(f"| ZONE-1-0{i} | `file.py:{i}` | что-то |" for i in range(1, 4))

    problem = gate.check_issue(996, "🎯 [Хвост] Ядро проверки", ["audit"], body)

    assert problem is not None
    assert "чек-листа нет" in problem


def test_two_findings_do_not_require_a_checklist() -> None:
    """Порог не случаен: две задачи в теле ещё читаются глазами."""
    body = "| ZONE-1-01 | `a.py:1` | раз |\n| ZONE-1-02 | `b.py:2` | два |"

    assert gate.check_issue(1, "🎯 [Подэпик] Мелочи", ["audit"], body) is None


def test_checked_item_without_an_outcome_is_reported() -> None:
    """Галочка без номера PR — «сделано» без доказательства."""
    body = "- [x] `RUN-4-02` — границы вывода\n- [ ] `RUN-1-01` — пара кейса\n"

    problem = gate.check_issue(987, "🎯 [Подэпик] Вернувшиеся дефекты", [], body)

    assert problem is not None
    assert "без исхода" in problem


def test_checked_item_with_pr_or_rejection_passes() -> None:
    body = (
        "- [x] `RUN-4-02` — границы вывода (#1182)\n"
        "- [x] ~~`STR-5-06`~~ — отклонено: репро недостижим\n"
        "- [ ] `RUN-1-01` — пара кейса\n"
    )

    assert gate.check_issue(987, "🎯 [Подэпик] Вернувшиеся дефекты", [], body) is None


def test_ordinary_issue_is_not_checked() -> None:
    """Гейт не лезет в обычные задачи: там список находок и не нужен."""
    body = "| ZONE-1-01 | a | b |\n| ZONE-1-02 | a | b |\n| ZONE-1-03 | a | b |"

    assert gate.check_issue(5, "fix(core): что-то", ["bug"], body) is None


def test_fetch_parses_number_title_labels_and_body() -> None:
    payload = {
        "items": [
            {
                "number": 987,
                "title": "🎯 [Подэпик] Вернувшиеся дефекты",
                "labels": [{"name": "audit"}, {"name": "bug"}],
                "body": "- [ ] REV-1-01",
            },
            {"number": None, "title": "мусор", "labels": [], "body": ""},
        ]
    }

    def fake_opener(request: Any) -> Any:
        del request
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    found = gate.fetch_open_issues("owner/repo", opener=fake_opener)

    assert found == [(987, "🎯 [Подэпик] Вернувшиеся дефекты", ["audit", "bug"], "- [ ] REV-1-01")]


def test_a_finding_returns_one_so_the_nightly_walk_sees_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Находка возвращает 1, а не 0.

    Ночной обход читает исход ПО КОДУ ВОЗВРАТА: 0 — чисто, 1 — находка,
        2 — проверка не отработала, и на находке прогон НЕ краснеет — она
        уезжает в задачу-адресата. Прежний 0 означал, что находка не доезжает
        никуда, кроме `::warning::` в логе прогона, который читает только тот,
        кто его открыл.

        Прежнее обоснование — «посторонний PR из-за этого не падает» — верно по
        сути и неверно по коду: скрипт запускается ТОЛЬКО ночным обходом, ни в
        одном PR-прогоне его нет, и краснеть от него нечему.

    Цена измерена: #1001 перечислял 74 находки скопом без чек-листа, сторож
    видел это дословно — и молчал.
    """
    body = "| RUN-1-01 | | |\n| RUN-1-02 | | |\n| RUN-1-03 | | |"
    monkeypatch.setattr(
        gate,
        "fetch_open_issues",
        lambda repo: [(996, "🎯 [Хвост] Ядро", ["audit"], body)],
    )

    code = gate.main([])

    assert code == gate.EXIT_FINDING
    assert "::warning::" in capsys.readouterr().out


def test_a_clean_tracker_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чисто — 0: иначе обход держал бы задачу открытой всегда."""
    monkeypatch.setattr(gate, "fetch_open_issues", lambda repo: [])

    assert gate.main([]) == gate.EXIT_OK


def test_an_unreadable_tracker_is_the_third_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не прочитали трекер — 2, а не 1: это сломанный механизм, а не находка.

    Коды были перевёрнуты: настоящая находка исчезала, а отказ чтения приезжал
    в задачу как находка — и чинить предлагалось не то.
    """

    def _boom(_repo: str) -> None:
        raise OSError("сеть недоступна")

    monkeypatch.setattr(gate, "fetch_open_issues", _boom)

    assert gate.main([]) == gate.EXIT_BROKEN
