"""Tests for scripts/audit_glossary_cards.py — EN-ratchet карточек глоссария (#684).

Скрипт лежит в scripts/ (не на sys.path) — грузим по пути, тем же приёмом, что
test_generate_glossary_badge.py / test_generate_coverage_badge.py.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "audit_glossary_cards.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_audit_glossary_cards", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _write_cards(tmp_path: pathlib.Path, summaries: list[tuple[str, str]]) -> pathlib.Path:
    """База из карточек с заданными парами (ru, en) в summary."""
    cards = [
        {
            "id": f"c{i}",
            "title": f"C{i}",
            "kind": "term",
            "status": "ready",
            "summary": {"ru": ru, "en": en},
        }
        for i, (ru, en) in enumerate(summaries)
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return data_dir


def test_cards_missing_en_finds_empty_translations(tmp_path: pathlib.Path) -> None:
    data_dir = _write_cards(tmp_path, [("есть", ""), ("есть", "has"), ("есть", "   ")])
    missing = _MODULE.cards_missing_en(_MODULE.load_cards(data_dir))
    assert [card.id for card in missing] == ["c0", "c2"]


def test_cards_missing_en_ignores_cards_without_ru(tmp_path: pathlib.Path) -> None:
    """Карточка без RU-текста — не долг перевода: переводить нечего."""
    data_dir = _write_cards(tmp_path, [("", ""), ("  ", "")])
    assert _MODULE.cards_missing_en(_MODULE.load_cards(data_dir)) == []


def test_load_cards_by_file_groups_by_json_name(tmp_path: pathlib.Path) -> None:
    data_dir = _write_cards(tmp_path, [("есть", "")])
    (data_dir / "extra.json").write_text(
        json.dumps([{"id": "x", "title": "X", "summary": {"ru": "текст", "en": ""}}]),
        encoding="utf-8",
    )
    by_file = _MODULE.load_cards_by_file(data_dir)
    assert sorted(by_file) == ["cards.json", "extra.json"]
    assert [card.id for card in by_file["extra.json"]] == ["x"]


def test_main_fails_when_ratchet_exceeded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _write_cards(tmp_path, [("есть", ""), ("есть", "")])
    monkeypatch.setattr(_MODULE, "MAX_CARDS_WITHOUT_EN", 1)
    assert _MODULE.main(["--cards", str(data_dir)]) == 1


def test_main_passes_when_within_ratchet(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _write_cards(tmp_path, [("есть", ""), ("есть", "has")])
    monkeypatch.setattr(_MODULE, "MAX_CARDS_WITHOUT_EN", 1)
    assert _MODULE.main(["--cards", str(data_dir)]) == 0


def test_en_coverage_does_not_regress() -> None:
    """Планка EN-покрытия комплектной базы: число карточек без EN только вниз (#684)."""
    missing = _MODULE.cards_missing_en(_MODULE.load_cards())
    assert len(missing) <= _MODULE.MAX_CARDS_WITHOUT_EN, (
        f"Карточек без summary_en стало {len(missing)} при планке "
        f"{_MODULE.MAX_CARDS_WITHOUT_EN}: заполни EN у новых карточек."
    )


def test_en_ratchet_is_not_stale() -> None:
    """Планка не должна отставать от факта — иначе регресс проскочит незамеченным."""
    missing = _MODULE.cards_missing_en(_MODULE.load_cards())
    assert len(missing) == _MODULE.MAX_CARDS_WITHOUT_EN, (
        f"Карточек без summary_en {len(missing)}, а планка {_MODULE.MAX_CARDS_WITHOUT_EN}: "
        "снизь MAX_CARDS_WITHOUT_EN в scripts/audit_glossary_cards.py тем же PR."
    )
