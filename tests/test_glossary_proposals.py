"""Предложения к содержанию Glossary-Python (scripts/glossary_proposals.py, #1623).

Две половины, как у любого гейта дерева: на подделках закреплено, что каждый
вид предложения выводится из данных и молчит там, где пробела нет; живая
половина сверяет лежащий в дереве файл со сборкой по встроенной копии.
"""

import importlib.util
import json
import pathlib
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from stepik_grader.core import stepik_compat

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "glossary_proposals.py"


@pytest.fixture(scope="module")
def gp() -> ModuleType:
    """Скрипт, загруженный по пути (``scripts/`` — не пакет)."""
    spec = importlib.util.spec_from_file_location("_glossary_proposals", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _card(card_id: str, added: str = "<3.0", title: str = "", aliases: tuple[str, ...] = ()) -> Any:
    return SimpleNamespace(
        id=card_id, added=added, title=title or card_id, title_en="", aliases=list(aliases)
    )


def _cards(*cards: Any) -> dict[str, Any]:
    return {card.id: card for card in cards}


def test_every_syntax_feature_has_a_card_spec(gp: ModuleType) -> None:
    """Таблица карточек признаков покрывает признаки проверки совместимости ровно."""
    keys = {key for _, key, _ in stepik_compat.SYNTAX_FEATURES}
    assert set(gp.SYNTAX_CARDS) == keys


def test_syntax_card_present_with_right_version_is_silent(gp: ModuleType) -> None:
    """Карточка признака есть и версия верна — предложения нет; нет карточки — есть."""
    found = {p["subject"]: p for p in gp._syntax(_cards(_card("match-case", "3.10")))}

    assert "match" not in found
    assert found["walrus"]["suggested"]["added"] == "3.8"


def test_syntax_card_with_wrong_version_is_named(gp: ModuleType) -> None:
    """Карточка есть, а added расходится с версией признака — предложение поправить."""
    found = {p["subject"]: p for p in gp._syntax(_cards(_card("match-case", "3.9")))}

    assert found["match"]["card_id"] == "match-case"
    assert found["match"]["suggested"] == {"added": "3.10"}


def test_module_without_own_card_is_proposed(gp: ModuleType) -> None:
    """Модуль stdlib с карточками членов и без своей — предложение; со своей — нет."""
    cards = _cards(
        _card("tomllib.load", "3.11"),
        _card("tomllib.loads", "3.11"),
        _card("csv"),
        _card("csv.reader"),
        _card("notamodule.thing"),
    )

    found = {p["subject"]: p for p in gp._modules(cards)}

    assert set(found) == {"tomllib"}
    assert found["tomllib"]["evidence"] == {"member_cards": 2, "earliest_member_added": "3.11"}


def test_builtin_covered_by_title_or_alias_is_not_proposed(gp: ModuleType) -> None:
    """Имя в первом слове заголовка или целым алиасом — описано; слово алиаса — нет."""
    cards = _cards(
        _card("ellipsis-...", title="Ellipsis ..."),
        _card("oserror", title="OSError", aliases=("IOError",)),
        _card("match-singleton", aliases=("case True в match",)),
    )

    missing = {p["subject"] for p in gp._builtins(cards)}

    assert {"Ellipsis", "OSError", "IOError"}.isdisjoint(missing)
    assert "True" in missing


def test_id_convention_lists_ids_that_differ_from_the_name(gp: ModuleType) -> None:
    """id исключения в нижнем регистре — в одном предложении; совпадающий id — нет."""
    [proposal] = gp._id_convention(_cards(_card("exceptiongroup"), _card("anext")))

    assert proposal["evidence"]["cards"] == [{"id": "exceptiongroup", "name": "ExceptionGroup"}]


def test_published_file_matches_the_build(gp: ModuleType) -> None:
    """Файл в дереве совпадает со сборкой по встроенной копии глоссария."""
    payload = json.loads(gp.OUTPUT.read_text(encoding="utf-8"))
    slugs = [proposal["slug"] for proposal in payload["proposals"]]

    assert gp.main(["--check"]) == 0
    assert len(slugs) == len(set(slugs))
    assert payload["schema"] and payload["producer"] and payload["source"]
