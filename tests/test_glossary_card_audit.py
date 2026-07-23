"""Tests for scripts/audit_glossary_cards.py + инварианты карточек (issue #684).

Скрипт лежит в scripts/ (не на sys.path) — грузим по пути тем же приёмом, что
test_generate_glossary_badge.py. Инварианты закрепляют результат аудита #684:
структурную полноту ready-карточек, matcher-safety мультифункциональных карточек
и EN-ratchet. Отдельный интеграционный тест доказывает, что matcher-safety —
не «зеркало» логики, а совпадает с реальным ``detector._is_known``: concept'ы,
которые прежде терялись в бандлах, теперь подавляются известными терминами базы.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType

from stepik_grader.glossary.detector import MissingConceptDetector
from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "audit_glossary_cards.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_audit_glossary_cards", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_AUDIT = _load_module()


# --- Структура (issue #684, ось 1) ----------------------------------------


def test_all_ready_cards_have_required_fields() -> None:
    """Каждая ready-карточка держит минимально обязательный набор полей."""
    missing = _AUDIT.cards_missing_required_fields(_AUDIT.load_cards())
    assert missing == [], "ready-карточки без обязательных полей (issue #684): " + "; ".join(
        f"{cid} → нет {', '.join(fields)}" for cid, fields in missing[:10]
    )


# --- Matcher-safety мультифункциональных карточек (issue #684, ось 2) ------


def test_no_matcher_unsafe_multifunction_cards() -> None:
    """Ни одна ' / '-карточка не прячет вызов, недостижимый детектором пробелов."""
    unsafe = _AUDIT.unsafe_multifunction_cards(_AUDIT.load_cards())
    assert unsafe == [], (
        "matcher-unsafe мультифункц. карточки (добавь keywords или разбей): "
        + "; ".join(f"{cid} → {', '.join(cs)}" for cid, cs in unsafe[:10])
    )


def test_no_matcher_unsafe_single_function_cards() -> None:
    """Одиночная карточка-вызов (`logging.debug()`, `.iterdir()`) достижима детектором."""
    unsafe = _AUDIT.unsafe_single_function_cards(_AUDIT.load_cards())
    assert unsafe == [], (
        "matcher-unsafe одиночные карточки-вызовы (добавь чистое имя в keywords "
        "или поправь kind): " + "; ".join(f"{cid} → {concept}" for cid, concept in unsafe[:10])
    )


def test_single_function_titles_skips_prose_and_bundles() -> None:
    """В одиночный инвариант не попадают бандлы и «не одна функция» (PR #702)."""
    ids = {c.id for c in _AUDIT.single_function_titles(_AUDIT.load_cards())}
    assert "logging-debug" in ids and ".iterdir" in ids
    # `repr() vs str()` и `for ... in reversed()` — не вызовы, а сравнение и
    # конструкция: их чинил kind (term/construct), а не keywords.
    assert {"repr-vs-str", "for-...-in-reversed"}.isdisjoint(ids)
    assert "os.getcwd-os.chdir" not in ids  # бандл — за unsafe_multifunction_cards


def test_bundled_single_function_concepts_are_detector_known() -> None:
    """Интеграция: вызовы одиночных карточек не уходят в очередь «Недостающее»."""
    known = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).known_terms()
    detector = MissingConceptDetector()
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
    emitted = {e.concept for e in detector.detect_from_code(code, known=set())}
    assert "logging.debug" in emitted, "детектор не эмитит logging.debug — обнови образец кода"
    assert [e.concept for e in detector.detect_from_code(code, known=known)] == []


def test_part_to_concept_normalizes_calls_and_dotted_paths() -> None:
    """part_to_concept снимает ведущую точку и хвостовые скобки, хранит dotted-путь."""
    assert _AUDIT.part_to_concept(".is_file()") == "is_file"
    assert _AUDIT.part_to_concept("os.getcwd()") == "os.getcwd"
    assert _AUDIT.part_to_concept("functools.lru_cache") == "functools.lru_cache"


def test_bundled_multifunction_concepts_are_detector_known() -> None:
    """Интеграция: concept'ы прежних бандлов теперь подавляются known_terms базы.

    Доказывает, что keyword-обогащение (#684) чинит именно рантайм-путь
    ``«Функции в коде»`` — детектор ЭМИТИТ эти concept'ы (проверка с пустым
    known), но с терминами комплектной базы больше не считает их пробелом.
    """
    known = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).known_terms()
    detector = MissingConceptDetector()
    code = (
        "import os, heapq, bisect\n"
        "os.getcwd(); os.chdir('/')\n"
        "heapq.heappush(h, 1); heapq.heappop(h)\n"
        "bisect.bisect_left(a, 3)\n"
    )
    emitted = {e.concept for e in detector.detect_from_code(code, known=set())}
    still_missing = {e.concept for e in detector.detect_from_code(code, known=known)}
    bundled = ("os.getcwd", "os.chdir", "heapq.heappush", "heapq.heappop", "bisect.bisect_left")
    for concept in bundled:
        assert concept in emitted, f"детектор не эмитит {concept} — обнови образец кода"
        assert concept not in still_missing, f"{concept} не покрыт карточкой (matcher-unsafe)"


# --- EN-ratchet (issue #684, ось 3 — волна перевода завершена, планка 0) ---


def test_en_coverage_does_not_regress() -> None:
    """Число карточек без summary_en не превышает ratchet (новые — двуязычны)."""
    missing_en = _AUDIT.cards_missing_en(_AUDIT.load_cards())
    assert len(missing_en) <= _AUDIT.MAX_CARDS_WITHOUT_EN, (
        f"{len(missing_en)} карточек без summary_en > ratchet "
        f"{_AUDIT.MAX_CARDS_WITHOUT_EN}: новые карточки должны быть двуязычными (issue #684)."
    )
