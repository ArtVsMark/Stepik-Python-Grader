"""Tests for scripts/generate_draft_cards.py — генератор черновиков (issue #328).

Скрипт в scripts/ (не на sys.path) — грузим по пути, как test_glossary_import.py.
Тесты интроспекции используют реальные stdlib-объекты (str.split, functools.reduce,
ValueError), без зависимости от внешних данных.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from stepik_grader.glossary.json_provider import (
    BUNDLED_GLOSSARY_DIR,
    JsonGlossaryProvider,
)
from stepik_grader.glossary.stdlib_inventory import StdlibItem

_SCRIPT = Path(__file__).parent.parent / "scripts" / "generate_draft_cards.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_draft_cards", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_script()

_METHOD = StdlibItem("str.split", "builtins", "method", "3.13")
_BUILTIN = StdlibItem("sorted", "builtins", "function", "3.13")
_MODFUNC = StdlibItem("functools.reduce", "functools", "function", "3.13")
_EXC = StdlibItem("ValueError", "builtins", "exception", "3.13")
_MODEXC = StdlibItem("json.JSONDecodeError", "json", "exception", "3.13")


def test_resolve_object_across_kinds() -> None:
    assert mod.resolve_object(_METHOD) is str.split
    assert mod.resolve_object(_BUILTIN) is sorted
    import functools

    assert mod.resolve_object(_MODFUNC) is functools.reduce
    # неразрешимое — None, без исключения
    assert mod.resolve_object(StdlibItem("nope.nope", "nope", "function", "3.13")) is None


def test_docs_url_templates() -> None:
    assert mod.docs_url_for(_METHOD).endswith("/stdtypes.html#str.split")
    assert mod.docs_url_for(_BUILTIN).endswith("/functions.html#sorted")
    assert mod.docs_url_for(_MODFUNC).endswith("/functools.html#functools.reduce")
    assert mod.docs_url_for(_EXC).endswith("/exceptions.html#ValueError")
    assert mod.docs_url_for(_MODEXC).endswith("/json.html#json.JSONDecodeError")


def test_section_for_mirrors_bundled_sections() -> None:
    assert mod.section_for(_METHOD) == "Строки (str)"
    assert mod.section_for(_BUILTIN) == "Встроенные функции"
    assert mod.section_for(_MODFUNC) == "Модуль functools"
    assert mod.section_for(_EXC) == "Исключения"


def test_draft_card_fields() -> None:
    card = mod.draft_card(_METHOD)
    assert card.id == "str.split"  # полный qualname → зачёт coverage (#327)
    assert card.status == "draft"
    assert card.kind == "function"  # method → function (у карточки нет "method")
    assert card.tags == ["autodraft"]
    assert card.summary == ""  # RU-однострочник заполняет редактор
    assert card.body  # первый абзац docstring
    assert card.syntax.startswith("split(")
    assert card.docs_url.endswith("#str.split")


def test_draft_card_exception_id_lowercased() -> None:
    card = mod.draft_card(_EXC)
    assert card.id == "valueerror"
    assert card.kind == "exception"


def test_run_generate_idempotent_and_skips_base_ids(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    # крошечная база: одна «ready» карточка на реальную сущность инвентаря
    (base / "seed.json").write_text(
        '[{"id": "functools.reduce", "title": "functools.reduce", "status": "ready"}]',
        encoding="utf-8",
    )
    out = base / "drafts.json"

    count1 = mod.run_generate(base, out)
    assert count1 > 200  # сгенерирована масса черновиков stdlib
    # база с черновиками грузится без дублей id (from_directory упал бы на дубле)
    provider = JsonGlossaryProvider.from_directory(base)
    assert len(provider) == count1 + 1
    # существующий id базы не задублирован черновиком
    assert provider.get("functools.reduce").status == "ready"

    snapshot = out.read_bytes()
    count2 = mod.run_generate(base, out)
    assert count2 == count1
    assert out.read_bytes() == snapshot  # идемпотентно


def test_bundled_drafts_present_and_marked() -> None:
    # Комплектные черновики в репозитории: помечены draft + tag autodraft.
    provider = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR)
    drafts = [c for c in provider.all() if c.status == "draft"]
    assert len(drafts) > 500
    assert all("autodraft" in c.tags for c in drafts)
    assert all(c.docs_url.startswith("https://docs.python.org/3/") for c in drafts)


def test_bundled_drafts_cover_type_methods() -> None:
    # #327 + #328: черновики закрывают пласт методов встроенных типов почти
    # полностью. Категория methods фиксирована (NOTABLE_BUILTIN_TYPES) и НЕ
    # зависит от состояния процесса — в отличие от exceptions (обход
    # BaseException видит исключения всех загруженных библиотек), поэтому
    # проверяем именно её. Порог мягкий — дрейф между версиями Python.
    from stepik_grader.glossary.coverage import build_coverage_report
    from stepik_grader.glossary.stdlib_inventory import build_stdlib_inventory

    provider = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR)
    report = build_coverage_report(build_stdlib_inventory(), known=provider.known_terms())
    methods = report.categories["methods"]
    assert methods.covered / methods.total >= 0.95
