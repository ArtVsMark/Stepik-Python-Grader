"""Tests for the local glossary knowledge-module (issue #126).

Покрывает foundation модуля ``stepik_grader.glossary``:
- модели ``GlossaryCard`` / ``GlossaryMissingEntry`` (валидация, сериализация);
- ``JsonGlossaryProvider`` (загрузка из файла/директории, поиск, выборки,
  понятные ошибки на битом/отсутствующем JSON);
- очередь пополнения (load/save/append с дедупом);
- ``MissingConceptDetector`` (AST-детект без исполнения, подавление known).

Отдельно от ``tests/test_glossary.py``, который тестирует компактную карту
``core/glossary.py``.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader.glossary import (
    GlossaryCard,
    GlossaryError,
    GlossaryMissingEntry,
    JsonGlossaryProvider,
    MissingConceptDetector,
    append_missing_entries,
    load_missing_queue,
    save_missing_queue,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "docs" / "examples" / "glossary.sample.json"


# ---------------------------------------------------------------------------
# GlossaryCard
# ---------------------------------------------------------------------------


def test_card_from_dict_minimal() -> None:
    card = GlossaryCard.from_dict({"id": "keyerror", "title": "KeyError"})
    assert card.id == "keyerror"
    assert card.title == "KeyError"
    assert card.kind == "term"
    assert card.status == "draft"
    assert card.aliases == []


def test_card_from_dict_hint_alias_maps_to_summary() -> None:
    card = GlossaryCard.from_dict({"id": "x", "title": "X", "hint": "краткое"})
    assert card.summary == "краткое"


def test_card_requires_id_and_title() -> None:
    with pytest.raises(ValueError, match="id"):
        GlossaryCard.from_dict({"title": "no id"})
    with pytest.raises(ValueError, match="title"):
        GlossaryCard.from_dict({"id": "no-title"})


def test_card_rejects_invalid_kind_and_status() -> None:
    with pytest.raises(ValueError, match="kind"):
        GlossaryCard.from_dict({"id": "a", "title": "A", "kind": "nonsense"})
    with pytest.raises(ValueError, match="status"):
        GlossaryCard.from_dict({"id": "a", "title": "A", "status": "archived"})


def test_card_roundtrip_to_dict() -> None:
    raw = {
        "id": "recursionerror",
        "title": "RecursionError",
        "kind": "exception",
        "summary": "глубокая рекурсия",
        "status": "ready",
        "aliases": ["stack overflow"],
        "tags": ["recursion"],
    }
    card = GlossaryCard.from_dict(raw)
    again = GlossaryCard.from_dict(card.to_dict())
    assert again == card


def test_card_matches_by_alias_and_keyword() -> None:
    card = GlossaryCard.from_dict(
        {"id": "reduce", "title": "reduce", "aliases": ["свёртка"], "keywords": ["fold"]}
    )
    assert card.matches("СВЁРТКА")
    assert card.matches("fol")
    assert not card.matches("map")
    assert not card.matches("   ")


# ---------------------------------------------------------------------------
# JsonGlossaryProvider — загрузка
# ---------------------------------------------------------------------------


def test_provider_loads_sample_fixture() -> None:
    provider = JsonGlossaryProvider.from_file(SAMPLE_FIXTURE)
    assert len(provider) == 3
    assert provider.get("recursionerror") is not None
    assert provider.get("nonexistent") is None


def test_provider_loads_list_root(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(json.dumps([{"id": "a", "title": "A"}]), encoding="utf-8")
    provider = JsonGlossaryProvider.from_file(path)
    assert len(provider) == 1


def test_provider_loads_object_root_with_cards_key(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"cards": [{"id": "a", "title": "A"}]}), encoding="utf-8")
    provider = JsonGlossaryProvider.from_file(path)
    assert len(provider) == 1


def test_provider_loads_directory(tmp_path: pathlib.Path) -> None:
    (tmp_path / "one.json").write_text(json.dumps([{"id": "a", "title": "A"}]), encoding="utf-8")
    (tmp_path / "two.json").write_text(json.dumps([{"id": "b", "title": "B"}]), encoding="utf-8")
    provider = JsonGlossaryProvider.from_directory(tmp_path)
    assert len(provider) == 2
    assert provider.get("a") and provider.get("b")


def test_provider_load_autodetects_dir_vs_file(tmp_path: pathlib.Path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([{"id": "a", "title": "A"}]), encoding="utf-8")
    assert len(JsonGlossaryProvider.load(tmp_path)) == 1
    assert len(JsonGlossaryProvider.load(tmp_path / "cards.json")) == 1


# ---------------------------------------------------------------------------
# JsonGlossaryProvider — понятные ошибки
# ---------------------------------------------------------------------------


def test_provider_missing_file_raises_clear_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(GlossaryError, match="не найден"):
        JsonGlossaryProvider.from_file(tmp_path / "nope.json")


def test_provider_invalid_json_raises_clear_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GlossaryError, match="невалидный JSON"):
        JsonGlossaryProvider.from_file(path)


def test_provider_invalid_card_reports_field(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(json.dumps([{"title": "no id"}]), encoding="utf-8")
    with pytest.raises(GlossaryError, match="id"):
        JsonGlossaryProvider.from_file(path)


def test_provider_rejects_duplicate_ids(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(
        json.dumps([{"id": "a", "title": "A"}, {"id": "a", "title": "A2"}]), encoding="utf-8"
    )
    with pytest.raises(GlossaryError, match="Дублирующийся"):
        JsonGlossaryProvider.from_file(path)


def test_provider_rejects_non_list_cards(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"cards": {"id": "a"}}), encoding="utf-8")
    with pytest.raises(GlossaryError, match="списком"):
        JsonGlossaryProvider.from_file(path)


# ---------------------------------------------------------------------------
# JsonGlossaryProvider — поиск и выборки
# ---------------------------------------------------------------------------


def test_provider_search_by_title_alias_tag() -> None:
    provider = JsonGlossaryProvider.from_file(SAMPLE_FIXTURE)
    assert [c.id for c in provider.search("recursion")] == ["recursionerror"]
    assert [c.id for c in provider.search("reduce")] == ["functools.reduce"]  # alias
    assert {c.id for c in provider.search("syntax")} == {"match-case"}  # tag


def test_provider_list_by_status_and_tag() -> None:
    provider = JsonGlossaryProvider.from_file(SAMPLE_FIXTURE)
    ready = {c.id for c in provider.list_by_status("ready")}
    assert ready == {"recursionerror", "match-case"}
    assert [c.id for c in provider.list_by_status("draft")] == ["functools.reduce"]
    assert {c.id for c in provider.list_by_tag("function")} == {"functools.reduce"}


def test_provider_known_terms_includes_aliases() -> None:
    provider = JsonGlossaryProvider.from_file(SAMPLE_FIXTURE)
    terms = provider.known_terms()
    assert "reduce" in terms  # alias of functools.reduce
    assert "recursionerror" in terms


# ---------------------------------------------------------------------------
# Очередь пополнения
# ---------------------------------------------------------------------------


def test_missing_queue_load_empty_when_absent(tmp_path: pathlib.Path) -> None:
    assert load_missing_queue(tmp_path / "queue.json") == []


def test_missing_queue_save_and_load_roundtrip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "queue.json"
    entry = GlossaryMissingEntry(concept="functools.reduce", kind="function", seen_in=["t.py"])
    save_missing_queue(path, [entry])
    loaded = load_missing_queue(path)
    assert loaded == [entry]


def test_missing_queue_append_dedupes_and_merges_sources(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "queue.json"
    append_missing_entries(path, [GlossaryMissingEntry(concept="x", seen_in=["a.py"])])
    result = append_missing_entries(path, [GlossaryMissingEntry(concept="x", seen_in=["b.py"])])
    assert len(result) == 1
    assert set(result[0].seen_in) == {"a.py", "b.py"}


def test_missing_queue_invalid_json_raises(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "queue.json"
    path.write_text("nope", encoding="utf-8")
    with pytest.raises(GlossaryError, match="невалидный JSON"):
        load_missing_queue(path)


# ---------------------------------------------------------------------------
# MissingConceptDetector
# ---------------------------------------------------------------------------


def test_detector_finds_stdlib_dotted_call() -> None:
    code = "import functools\nfunctools.reduce(lambda a, b: a + b, [1, 2])\n"
    entries = MissingConceptDetector().detect_from_code(code, source="sol.py", today="2026-07-06")
    concepts = {e.concept for e in entries}
    assert "functools.reduce" in concepts
    entry = next(e for e in entries if e.concept == "functools.reduce")
    assert entry.kind == "function"
    assert entry.status == "new"
    assert entry.seen_in == ["sol.py"]
    assert entry.first_seen == "2026-07-06"


def test_detector_finds_from_import_call() -> None:
    code = "from functools import reduce\nreduce(lambda a, b: a + b, [1])\n"
    concepts = {e.concept for e in MissingConceptDetector().detect_from_code(code)}
    assert "functools.reduce" in concepts


def test_detector_finds_notable_builtin() -> None:
    code = "xs = enumerate([1, 2, 3])\n"
    concepts = {e.concept for e in MissingConceptDetector().detect_from_code(code)}
    assert "enumerate" in concepts


def test_detector_finds_match_construct() -> None:
    code = "x = 1\nmatch x:\n    case 1:\n        pass\n"
    entries = MissingConceptDetector().detect_from_code(code)
    match = next(e for e in entries if e.concept == "match/case")
    assert match.kind == "construct"


def test_detector_ignores_user_defined_functions() -> None:
    code = "def helper():\n    return 1\nhelper()\n"
    assert MissingConceptDetector().detect_from_code(code) == []


def test_detector_ignores_ordinary_builtins() -> None:
    code = "print(len('abc'))\n"
    assert MissingConceptDetector().detect_from_code(code) == []


def test_detector_suppresses_known_aliases() -> None:
    code = "import functools\nfunctools.reduce(f, xs)\nys = enumerate(xs)\n"
    # known contains alias 'reduce' → functools.reduce suppressed; enumerate stays.
    entries = MissingConceptDetector().detect_from_code(code, known={"reduce"})
    concepts = {e.concept for e in entries}
    assert "functools.reduce" not in concepts
    assert "enumerate" in concepts


def test_detector_suppresses_via_provider_known_terms() -> None:
    provider = JsonGlossaryProvider.from_file(SAMPLE_FIXTURE)
    code = "import functools\nfunctools.reduce(f, xs)\n"
    entries = MissingConceptDetector().detect_from_code(code, known=provider.known_terms())
    assert entries == []  # 'reduce' is an alias in the sample fixture


def test_detector_deterministic_sorted_output() -> None:
    code = "import functools, itertools\nfunctools.reduce(f, xs)\nitertools.chain(a, b)\n"
    concepts = [e.concept for e in MissingConceptDetector().detect_from_code(code)]
    assert concepts == sorted(concepts)


def test_detector_syntax_error_returns_empty() -> None:
    assert MissingConceptDetector().detect_from_code("def (:") == []


def test_detector_does_not_execute_code() -> None:
    # Побочный эффект не должен произойти: детектор только парсит AST.
    code = "import pathlib\npathlib.Path('SIDE_EFFECT_MARKER').write_text('x')\n"
    MissingConceptDetector().detect_from_code(code)
    assert not pathlib.Path("SIDE_EFFECT_MARKER").exists()


def test_detector_from_error_finds_uncovered_exception() -> None:
    tb = "Traceback (most recent call last):\nSomeCustomError: boom"
    entry = MissingConceptDetector().detect_from_error(tb, source="sol.py", verdict="RE")
    assert entry is not None
    assert entry.concept == "SomeCustomError"
    assert entry.kind == "exception"
    assert entry.verdict == "RE"
    assert entry.seen_in == ["sol.py"]


def test_detector_from_error_suppresses_known() -> None:
    tb = "RecursionError: maximum recursion depth exceeded"
    assert MissingConceptDetector().detect_from_error(tb, known={"recursionerror"}) is None


def test_detector_from_error_no_exception_returns_none() -> None:
    assert MissingConceptDetector().detect_from_error("just a plain message") is None
    assert MissingConceptDetector().detect_from_error("") is None
