"""Tests for core/ai_grounding.retrieve_grounding (issue #544, эпик E3).

Retrieval-заземление AI-подсказки: по концептам кода (``scan_code_concepts``)
достаём top-k карточек глоссария. Тесты используют контролируемый список карточек
(``cards=...``), чтобы не зависеть от содержимого комплектной базы. Ключевой
сценарий приёмки: для кода с известным концептом карточка попадает в grounding;
при отсутствии совпадений — пустая строка (промпт деградирует к плоскому).
"""

from __future__ import annotations

from stepik_grader.core.ai_grounding import retrieve_grounding
from stepik_grader.glossary.models import GlossaryCard


def _card(card_id: str, title: str, summary: str, *, status: str = "ready") -> GlossaryCard:
    return GlossaryCard.from_dict(
        {"id": card_id, "title": title, "kind": "function", "summary": summary, "status": status}
    )


_CARDS = [
    _card("sorted", "sorted", "Возвращает новый отсортированный список"),
    _card("list.append", "list.append", "Добавляет элемент в конец списка"),
    _card("range", "range", "Ленивая последовательность целых"),
]


def test_grounding_finds_card_for_function_concept() -> None:
    g = retrieve_grounding("sorted([3, 1])", cards=_CARDS)
    assert "sorted" in g
    assert "отсортированный" in g


def test_grounding_finds_method_card_by_qualname_tail() -> None:
    """Детектор даёт голое ``append``; карточка — под qualname ``list.append``."""
    g = retrieve_grounding("a = []\na.append(1)", cards=_CARDS)
    assert "list.append" in g
    assert "конец списка" in g


def test_grounding_empty_when_concept_has_no_card() -> None:
    assert retrieve_grounding("zip([1], [2])", cards=_CARDS) == ""  # zip не в _CARDS


def test_grounding_empty_when_no_concepts() -> None:
    assert retrieve_grounding("x = 1 + 2", cards=_CARDS) == ""


def test_grounding_degrades_on_syntax_error() -> None:
    assert retrieve_grounding("def (", cards=_CARDS) == ""


def test_grounding_topk_limits_and_dedup() -> None:
    code = "sorted([3, 1])\nx = range(3)\na = []\na.append(1)"  # 3 концепта
    g = retrieve_grounding(code, k=2, cards=_CARDS)
    assert len(g.splitlines()) == 2  # усечено до top-k=2


def test_grounding_skips_non_ready_cards() -> None:
    draft = [_card("sorted", "sorted", "черновик", status="draft")]
    assert retrieve_grounding("sorted([1])", cards=draft) == ""


def test_grounding_empty_cards_list() -> None:
    assert retrieve_grounding("sorted([1])", cards=[]) == ""


def test_grounding_bundled_base_smoke() -> None:
    """Без ``cards`` берётся комплектная база: концепт ``sorted`` заземляется."""
    g = retrieve_grounding("sorted([3, 1])")
    assert "sorted" in g  # bundled-карточка sorted существует и ready
