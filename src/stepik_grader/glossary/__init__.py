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

from typing import TYPE_CHECKING, Any

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

# issue #896: символы `coverage` реэкспортируются ЛЕНИВО (PEP 562). Прежний
# импорт на уровне пакета делал `python -m stepik_grader.glossary.coverage`
# шумным: runpy сначала импортирует пакет, тот затягивает свой же подмодуль, и
# на каждый запуск команды из документации печаталось предупреждение
# «found in sys.modules after import of package … may result in unpredictable
# behaviour». Ленивый доступ убирает предзагрузку, не трогая `__all__`:
# `from stepik_grader.glossary import CoverageReport` работает как прежде.
if TYPE_CHECKING:  # для mypy и IDE — обычные имена, без рантайм-импорта
    from .coverage import (
        CATEGORIES,
        CategoryCoverage,
        CoverageReport,
        build_coverage_report,
        missing_entries_from_inventory,
    )

_COVERAGE_EXPORTS = frozenset(
    {
        "CATEGORIES",
        "CategoryCoverage",
        "CoverageReport",
        "build_coverage_report",
        "missing_entries_from_inventory",
    }
)


def __getattr__(name: str) -> Any:
    """Достать символ ``coverage`` при первом обращении (PEP 562, issue #896)."""
    if name in _COVERAGE_EXPORTS:
        from . import coverage

        return getattr(coverage, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
