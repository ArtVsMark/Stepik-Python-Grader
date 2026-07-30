"""stepik_grader.glossary — локальный knowledge-модуль глоссария (issue #126).

Foundation полноценного локального глоссария WEB MVP (эпик #123): типизированные
модели карточек и очереди пополнения, JSON-провайдер для загрузки/поиска
локальной базы и консервативный детектор недостающих концепций.

Реализация — [`docs/use/web-interface.md § Глоссарий`](../../../docs/use/web-interface.md);
формат JSON — [`docs/dev/glossary.md`](../../../docs/dev/glossary.md). Внешний
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) остаётся целью
одностороннего экспорта, истина хранится локально.

Компактная карта встроенных исключений (``core/glossary.py``) не заменяется —
этот модуль её расширяет richer-карточками; связь через ``GlossaryCard.id`` =
``GlossaryEntry.anchor``.
"""

from __future__ import annotations

from .coverage import (
    CATEGORIES,
    CategoryCoverage,
    CoverageReport,
    build_coverage_report,
    missing_entries_from_inventory,
)
from .detector import DEFAULT_NOTABLE_BUILTINS, MissingConceptDetector
from .json_provider import (
    BUNDLED_GLOSSARY_DIR,
    GlossaryError,
    GlossaryProvider,
    JsonGlossaryProvider,
    append_missing_entries,
    load_missing_queue,
    save_missing_queue,
)
from .models import GlossaryCard, GlossaryMissingEntry
from .stdlib_inventory import NOTABLE_STDLIB_MODULES, StdlibItem, build_stdlib_inventory

__all__ = [
    "BUNDLED_GLOSSARY_DIR",
    "CATEGORIES",
    "DEFAULT_NOTABLE_BUILTINS",
    "NOTABLE_STDLIB_MODULES",
    "CategoryCoverage",
    "CoverageReport",
    "GlossaryCard",
    "GlossaryError",
    "GlossaryMissingEntry",
    "GlossaryProvider",
    "JsonGlossaryProvider",
    "MissingConceptDetector",
    "StdlibItem",
    "append_missing_entries",
    "build_coverage_report",
    "build_stdlib_inventory",
    "load_missing_queue",
    "missing_entries_from_inventory",
    "save_missing_queue",
]
