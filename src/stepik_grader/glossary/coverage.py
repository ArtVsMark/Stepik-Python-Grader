"""coverage.py — сопоставление stdlib inventory с локальной базой (issue #197).

Архитектурный слой: Domain (leaf — только stdlib + другие leaf-модули пакета
``glossary``; не тянет ``core/*``, DAG остаётся ацикличным).

Сравнивает офлайн-инвентарь официального Python/stdlib
(``stdlib_inventory.build_stdlib_inventory()``) с известными терминами
локальной базы карточек (``JsonGlossaryProvider.known_terms()``) и строит:

- **coverage report** — сколько сущностей `builtins`/`exceptions`/`stdlib`
  уже описано карточками, а сколько нет (``CoverageReport``);
- **missing entries** — недостающие сущности как ``GlossaryMissingEntry``
  с ``origin="stdlib_scan"`` для очереди пополнения (дальше пишутся через
  ``json_provider.append_missing_entries``, который уже дедуплицирует по
  ``concept`` и идемпотентен при повторном запуске).

Модуль только вычисляет данные — не печатает и не решает, показывать ли отчёт
(это делает точка входа CLI/меню, issue #198).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import GlossaryMissingEntry, MissingKind
from .stdlib_inventory import InventoryKind, StdlibItem

__all__ = [
    "CATEGORIES",
    "CategoryCoverage",
    "CoverageReport",
    "build_coverage_report",
    "missing_entries_from_inventory",
]

# Категории отчёта покрытия — не совпадают 1:1 с InventoryKind: "exceptions"
# группирует все kind="exception" независимо от модуля, "builtins" — только
# builtins-функции/классы (не исключения), "stdlib" — всё остальное.
CATEGORIES: tuple[str, ...] = ("builtins", "exceptions", "stdlib")

_INVENTORY_TO_MISSING_KIND: dict[InventoryKind, MissingKind] = {
    "function": "function",
    "class": "class",
    "exception": "exception",
}


def _category_of(item: StdlibItem) -> str:
    if item.kind == "exception":
        return "exceptions"
    if item.module == "builtins":
        return "builtins"
    return "stdlib"


def _is_known(qualname: str, known_norm: set[str]) -> bool:
    qualname_lc = qualname.lower()
    if qualname_lc in known_norm:
        return True
    tail = qualname_lc.rsplit(".", 1)[-1]  # "functools.reduce" -> "reduce"
    return tail in known_norm


def _normalize_known(known: set[str] | None) -> set[str]:
    return {k.strip().lower() for k in known if k.strip()} if known else set()


@dataclass(frozen=True)
class CategoryCoverage:
    """Покрытие одной категории (``builtins``/``exceptions``/``stdlib``)."""

    category: str
    total: int
    covered: int
    missing: tuple[str, ...]  # qualnames без карточки, отсортированы

    @property
    def missing_count(self) -> int:
        """Число сущностей категории без карточки."""
        return len(self.missing)

    @property
    def ratio(self) -> float:
        """Доля покрытых сущностей (0.0..1.0); 1.0, если категория пуста."""
        return self.covered / self.total if self.total else 1.0


@dataclass(frozen=True)
class CoverageReport:
    """Отчёт покрытия по всем категориям + python-версия инвентаря."""

    categories: dict[str, CategoryCoverage]
    python_version: str

    @property
    def total(self) -> int:
        """Суммарное число сущностей инвентаря по всем категориям."""
        return sum(cat.total for cat in self.categories.values())

    @property
    def total_missing(self) -> int:
        """Суммарное число недостающих сущностей по всем категориям."""
        return sum(cat.missing_count for cat in self.categories.values())


def missing_entries_from_inventory(
    inventory: list[StdlibItem],
    known: set[str] | None = None,
    *,
    today: str | None = None,
) -> list[GlossaryMissingEntry]:
    """Построить список пробелов (``origin="stdlib_scan"``) из инвентаря.

    Args:
        inventory: результат ``build_stdlib_inventory()``.
        known: покрытые термины (``JsonGlossaryProvider.known_terms()``),
            любой регистр; сущности с известным именем (или его "хвостом"
            после точки) в результат не попадают.
        today: ISO-дата для ``first_seen`` (по умолчанию — сегодня).

    Returns:
        Список ``GlossaryMissingEntry``, отсортированный по ``concept``
        (инвентарь уже отсортирован и дедуплицирован по ``qualname`` —
        порядок и уникальность наследуются без дополнительной сортировки).
    """
    known_norm = _normalize_known(known)
    first_seen = today or date.today().isoformat()
    entries: list[GlossaryMissingEntry] = []
    for item in inventory:
        if _is_known(item.qualname, known_norm):
            continue
        entries.append(
            GlossaryMissingEntry(
                concept=item.qualname,
                kind=_INVENTORY_TO_MISSING_KIND[item.kind],
                status="new",
                reason="Обнаружено сканированием официального Python/stdlib; "
                "нет карточки в глоссарии.",
                first_seen=first_seen,
                origin="stdlib_scan",
                module=item.module,
                qualname=item.qualname,
            )
        )
    return entries


def build_coverage_report(
    inventory: list[StdlibItem],
    known: set[str] | None = None,
    *,
    today: str | None = None,
) -> CoverageReport:
    """Построить отчёт покрытия глоссария относительно официального stdlib.

    Args:
        inventory: результат ``build_stdlib_inventory()``.
        known: покрытые термины (``JsonGlossaryProvider.known_terms()``).
        today: ISO-дата для расчёта пробелов (передаётся в
            ``missing_entries_from_inventory``; на сами счётчики не влияет).

    Returns:
        ``CoverageReport`` с разбивкой по категориям ``CATEGORIES``.
    """
    missing_qualnames = {
        entry.concept for entry in missing_entries_from_inventory(inventory, known, today=today)
    }

    totals: dict[str, int] = {category: 0 for category in CATEGORIES}
    missing_by_category: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for item in inventory:
        category = _category_of(item)
        totals[category] += 1
        if item.qualname in missing_qualnames:
            missing_by_category[category].append(item.qualname)

    categories = {
        category: CategoryCoverage(
            category=category,
            total=totals[category],
            covered=totals[category] - len(missing_by_category[category]),
            missing=tuple(sorted(missing_by_category[category])),
        )
        for category in CATEGORIES
    }
    python_version = inventory[0].python_version if inventory else ""
    return CoverageReport(categories=categories, python_version=python_version)
