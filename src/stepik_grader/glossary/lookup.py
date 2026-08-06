"""lookup.py — производные индексы базы глоссария: матч концепций из кода.

Домен глоссария, а не web: «какие имена знает база» и «какая карточка отвечает
за концепцию» — свойства самой базы, а не HTTP-слоя. Раньше жило в
``web/glossary_adapter.py`` и было недоступно CLI (issue #831, ARCH-06).

Зависит только от ``glossary/models.py``/``glossary/taxonomy.py`` и stdlib —
ребра ``glossary → core`` здесь нет и быть не должно (ADR-0011).
"""

from __future__ import annotations

import keyword
import sys

from stepik_grader.glossary.models import GlossaryCard
from stepik_grader.glossary.taxonomy import type_priority

__all__ = [
    "card_index",
    "match_card",
    "method_names_from_cards",
    "name_concepts_from_cards",
]


def card_index(cards: list[GlossaryCard]) -> dict[str, GlossaryCard]:
    """Индекс ``id/alias/«хвост id» (lower) -> карточка`` для матча концепций из кода.

    Помимо ``id`` и ``aliases`` карточка индексируется по «хвосту» своего id
    после точки (``str.split`` → ключ ``split``): концепция-метод из сканера
    приходит голым именем (``split``), а карточка метода хранится как
    ``str.split`` (issue #322). При конфликте «хвоста» побеждает карточка
    основного типа (``str.split`` важнее ``bytearray.split``).
    """
    index: dict[str, GlossaryCard] = {}
    for card in sorted(cards, key=type_priority):  # приоритетные типы кладутся первыми
        keys = {card.id, card.id.rsplit(".", 1)[-1], *card.aliases}
        for key in keys:
            k = key.strip().lower()
            if k:
                index.setdefault(k, card)
    return index


def match_card(concept: str, index: dict[str, GlossaryCard]) -> GlossaryCard | None:
    """Найти карточку под концепцию: точное id/alias/«хвост», затем «хвост» концепции.

    ``functools.reduce`` матчится картой ``reduce``, а голый метод ``split`` —
    картой ``str.split`` (через индекс по «хвосту id», см. ``card_index``).
    """
    concept_lc = concept.lower()
    if concept_lc in index:
        return index[concept_lc]
    tail = concept_lc.rsplit(".", 1)[-1]
    return index.get(tail)


def name_concepts_from_cards(cards: list[GlossaryCard]) -> frozenset[str]:
    """Bare-имена (без точки), на которые в базе есть карточка, — правильный регистр.

    issue #686: сканер распознаёт голую ссылку на имя (``isinstance(x, int)``,
    ``x: Counter``, ``class Foo(Enum)``) только если оно в этом наборе, поэтому
    набор — это буквально «всё, на что есть карточка»: любой класс/функция
    модуля или встроенное имя, а не курируемый список. Регистр берём из
    ``title`` (``KeyError``, ``Counter``, ``NamedTuple``): id карточек
    нормализован в нижний, а в коде имя пишется как в Python. Ключевые слова
    (``for``, ``def``) исключены — они не ``Name``, их ловят visit-конструкции.
    """
    names: set[str] = set()
    for card in cards:
        if "." in card.id:
            continue
        title = card.title.replace("()", "").strip()
        name = title if title.isidentifier() else (card.id if card.id.isidentifier() else "")
        if name and not keyword.iskeyword(name):
            names.add(name)
    return frozenset(names)


def method_names_from_cards(cards: list[GlossaryCard]) -> frozenset[str]:
    """Имена методов, известные самой базе: «хвосты» id вида ``Класс.метод``.

    issue #686: stdlib-инвентарь знает методы только встроенных типов (204 имени
    из ``builtins``), поэтому ``Path.exists()``/``Path.read_text()`` панель не
    видела — хотя карточки ``path.exists``/``path.read_text`` в базе есть.
    Источником имён становится сама база: если первый сегмент id — НЕ имя
    stdlib-модуля, то это класс, а хвост — его метод. Проверка по
    ``sys.stdlib_module_names`` не даёт превратить ``math.sqrt`` в «метод
    ``sqrt``», иначе любой ``obj.sqrt()`` матчился бы на функцию модуля.
    """
    names: set[str] = set()
    for card in cards:
        head, _, tail = card.id.partition(".")
        if not tail or "." in tail:
            continue
        if head in sys.stdlib_module_names:
            continue
        names.add(tail)
    return frozenset(names)
