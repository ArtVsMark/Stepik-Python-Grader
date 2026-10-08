"""Карточка-функция находится детектором пробелов (issue #684 → #1573).

Содержание глоссария теперь ведёт Glossary-Python, и его проверки (структура,
двуязычие, примеры, ссылки) живут там. Здесь остаётся одно свойство, которого
глоссарий не знает, потому что оно — про грейдер: имя, которое детектор
вынимает из кода студента (``os.getcwd``, ``exists``), обязано совпасть с
search-термом карточки. Иначе раздел «Функции в коде» и покрытие считают
описанное имя непокрытым, и студент не получает карточку, которая есть.

Раньше это держал ``scripts/audit_glossary_cards.py`` собственной копией логики
детектора с пометкой «держать в синхроне». Копия — ровно то место, где такие
проверки расходятся молча, поэтому здесь зовётся сам детектор.
"""

import pytest

from stepik_grader.glossary.detector import MissingConceptDetector
from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider
from stepik_grader.glossary.models import GlossaryCard


def _is_call_part(part: str) -> bool:
    """Часть заголовка, которую детектор производит как concept: ``f()`` или ``mod.attr``.

    Голые ключевые слова и операторы (``try``, ``in``), одиночные имена классов
    и выражения (``Optional[X]``, ``[::2]``) детектор как функцию не даёт.
    """
    part = part.strip()
    return part.endswith("()") or "." in part.lstrip(".")


def _concept(part: str) -> str:
    """Часть заголовка → concept детектора: ``.exists()`` → ``exists``."""
    return part.strip().lstrip(".").removesuffix("()")


def _unreachable(cards: list[GlossaryCard]) -> list[tuple[str, str]]:
    """``(id карточки, concept)``, которые детектор к карточке не приведёт."""
    detector = MissingConceptDetector()
    found: list[tuple[str, str]] = []
    for card in cards:
        if card.kind != "function":
            continue
        parts = card.title.split(" / ")
        if len(parts) == 1 and " " in card.title.strip():
            continue  # «repr() vs str()» — не один вызов, а сравнение
        terms = set(card.search_terms)
        for part in parts:
            if _is_call_part(part) and not detector._is_known(_concept(part), terms):
                found.append((card.id, _concept(part)))
    return found


@pytest.mark.parametrize(
    ("title", "keywords", "reachable"),
    [
        ("os.getcwd()", [], True),  # хвост «getcwd» не нужен — dotted-путь равен id
        ("logging.debug()", [], False),  # id «logging-debug» не даёт терма «logging.debug»
        ("logging.debug()", ["logging.debug"], True),
    ],
)
def test_the_check_tells_reachable_from_unreachable(
    title: str, keywords: list[str], reachable: bool
) -> None:
    """Сам разбор: недостижимое находится, достижимое — нет."""
    card_id = "os.getcwd" if title.startswith("os.") else "logging-debug"
    card = GlossaryCard.from_dict(
        {"id": card_id, "title": title, "kind": "function", "keywords": keywords}
    )

    assert (_unreachable([card]) == []) is reachable


def test_every_bundled_function_card_is_reachable_by_the_detector() -> None:
    """Живая половина: комплектная база, приехавшая выгрузкой."""
    cards = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()

    assert _unreachable(cards) == []
