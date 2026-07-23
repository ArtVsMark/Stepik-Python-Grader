"""Tests: карточки-функции комплектной базы достижимы детектором пробелов.

Регресс на баг matcher-safety (родня issue #684, там — мультифункциональные
карточки): у одиночной карточки ``kind="function"`` title вида
``logging.debug()``/``.iterdir()`` не давал ни одного search-терма, равного
concept'у детектора (``logging.debug``/``iterdir``), поэтому
``detector._is_known`` считал вызов непокрытым — вызов уходил в очередь
«Недостающее», а coverage не засчитывал сущность. Фикс — чистое имя concept'а
в ``keywords`` карточки.

Инвариант держится на данных ``glossary/data/*.json``, поэтому проверяется
по комплектной базе, а не по фикстуре.
"""

from __future__ import annotations

from stepik_grader.glossary.detector import MissingConceptDetector
from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider
from stepik_grader.glossary.models import GlossaryCard


def _is_detector_part(part: str) -> bool:
    """True, если часть title — вызов/dotted-путь, который эмитит сканер кода."""
    part = part.strip()
    if part.endswith("()"):
        return True
    return "." in part.lstrip(".")


def _part_to_concept(part: str) -> str:
    """Свести часть title к concept'у детектора: снять ведущую ``.`` и хвост ``()``."""
    return part.strip().lstrip(".").removesuffix("()")


def _is_matcher_safe(card: GlossaryCard, concept: str) -> bool:
    """Повторяет ``detector._is_known``: точное равенство или совпадение по хвосту."""
    terms = set(card.search_terms)  # уже lower-case
    concept_lc = concept.lower()
    return concept_lc in terms or concept_lc.rsplit(".", 1)[-1] in terms


def test_single_function_cards_are_matcher_safe() -> None:
    cards = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()
    unsafe = [
        (card.id, _part_to_concept(card.title))
        for card in cards
        if card.kind == "function"
        and " / " not in card.title  # бандлы — инвариант issue #684
        and _is_detector_part(card.title)
        and not _is_matcher_safe(card, _part_to_concept(card.title))
    ]
    assert not unsafe, (
        "карточки-функции недостижимы детектором (добавь чистое имя в keywords "
        f"или поправь kind): {unsafe}"
    )


def test_module_calls_do_not_land_in_missing_queue() -> None:
    # Поведенческая половина инварианта: вызовы, у которых карточка есть,
    # не должны попадать в очередь «Недостающее».
    known = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).known_terms()
    code = (
        "import copy, hashlib, logging, os, re, subprocess\n"
        "from concurrent.futures import as_completed\n"
        "from unittest.mock import patch\n"
        'logging.basicConfig(); logging.debug("x"); logging.getLogger("a")\n'
        'os.getenv("HOME"); os.scandir("."); re.escape("a"); copy.deepcopy([1])\n'
        'hashlib.md5(b"x"); hashlib.sha256(b"x")\n'
        'subprocess.run(["ls"]); subprocess.check_output(["ls"]); subprocess.Popen(["ls"])\n'
        'as_completed([]); patch("m.f")\n'
    )
    entries = MissingConceptDetector().detect_from_code(code, known=known)
    assert [e.concept for e in entries] == []
