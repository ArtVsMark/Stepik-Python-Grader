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

Модуль вычисляет данные и (при прямом запуске) выводит краткую сводку —
запуск: ``python -m stepik_grader.glossary.coverage [--cards PATH]
[--missing-out PATH] [--modules a,b,c]`` (issue #198). Вывод — через
локальный rich-опциональный принтер с graceful fallback на ``print()`` (свой,
а не ``core/reporter._console`` — модуль остаётся leaf'ом и не тянет ``core/*``).
"""

from __future__ import annotations

import argparse
import pathlib
from dataclasses import dataclass
from datetime import date

from .json_provider import GlossaryError, JsonGlossaryProvider, append_missing_entries
from .models import GlossaryMissingEntry, MissingKind
from .stdlib_inventory import InventoryKind, StdlibItem, build_stdlib_inventory

__all__ = [
    "CATEGORIES",
    "CategoryCoverage",
    "CoverageReport",
    "build_coverage_report",
    "main",
    "missing_entries_from_inventory",
]

try:
    from rich.console import Console

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False

# Категории отчёта покрытия — не совпадают 1:1 с InventoryKind: "exceptions"
# группирует все kind="exception" независимо от модуля, "builtins" — только
# builtins-функции/классы (не исключения), "methods" — методы встроенных типов
# (kind="method", issue #327), "stdlib" — всё остальное.
CATEGORIES: tuple[str, ...] = ("builtins", "methods", "exceptions", "stdlib")

# MissingKind не знает "method" — метод для очереди пополнения function-подобен
# (callable), полный контекст несут поля module/qualname записи (issue #327).
_INVENTORY_TO_MISSING_KIND: dict[InventoryKind, MissingKind] = {
    "function": "function",
    "class": "class",
    "exception": "exception",
    "method": "function",
}


def _category_of(item: StdlibItem) -> str:
    if item.kind == "exception":
        return "exceptions"
    if item.kind == "method":
        return "methods"
    if item.module == "builtins":
        return "builtins"
    return "stdlib"


def _is_known(item: StdlibItem, known_norm: set[str]) -> bool:
    qualname_lc = item.qualname.lower()
    if qualname_lc in known_norm:
        return True
    if item.kind == "method":
        # str.split и bytes.split — разные методы/карточки: сверяем ТОЛЬКО полный
        # qualname, без «хвостовой» эвристики (issue #327), иначе одна карточка
        # "split" ложно закрыла бы методы всех типов.
        return False
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
        if _is_known(item, known_norm):
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


def _print(text: str) -> None:
    if _RICH and _console is not None:
        _console.print(text)
    else:
        print(text)


def format_report_summary(report: CoverageReport) -> str:
    """Отформатировать краткую human-readable сводку покрытия по категориям."""
    version = report.python_version or "unknown"
    lines = [f"Glossary stdlib coverage (Python {version})"]
    for category in CATEGORIES:
        cat = report.categories[category]
        lines.append(
            f"  {category:<10} {cat.covered}/{cat.total} covered "
            f"({cat.ratio:.0%}), {cat.missing_count} missing"
        )
    covered_total = report.total - report.total_missing
    lines.append(
        f"  {'total':<10} {covered_total}/{report.total} covered, {report.total_missing} missing"
    )
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stepik_grader.glossary.coverage",
        description=(
            "Офлайн coverage-скан локального глоссария относительно "
            "официального Python/stdlib (issue #197/#198). Не ходит в сеть и "
            "не исполняет пользовательский код."
        ),
    )
    parser.add_argument(
        "--cards",
        default=None,
        help="Путь к базе карточек глоссария (файл или директория с *.json); "
        "без флага покрытие считается относительно пустой базы.",
    )
    parser.add_argument(
        "--missing-out",
        default=None,
        help="Путь для JSON-очереди пробелов (origin=stdlib_scan); "
        "дозаписывается идемпотентно через append_missing_entries.",
    )
    parser.add_argument(
        "--modules",
        default=None,
        help="Через запятую — подмножество stdlib-модулей для скана "
        "(по умолчанию NOTABLE_STDLIB_MODULES).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа: ``python -m stepik_grader.glossary.coverage``.

    Печатает сводку покрытия по категориям и, если задан ``--missing-out``,
    дозаписывает недостающие сущности в очередь пополнения.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    known: set[str] = set()
    if args.cards:
        try:
            provider = JsonGlossaryProvider.load(pathlib.Path(args.cards))
        except GlossaryError as exc:
            parser.error(str(exc))
            return  # недостижимо (parser.error поднимает SystemExit); для mypy
        known = provider.known_terms()

    modules = (
        frozenset(name.strip() for name in args.modules.split(",") if name.strip())
        if args.modules
        else None
    )
    inventory = build_stdlib_inventory(modules)
    report = build_coverage_report(inventory, known=known)
    _print(format_report_summary(report))

    if args.missing_out:
        missing = missing_entries_from_inventory(inventory, known=known)
        missing_out = pathlib.Path(args.missing_out)
        append_missing_entries(missing_out, missing)
        _print(f"Missing entries written to {missing_out} ({len(missing)} stdlib_scan gaps)")


if __name__ == "__main__":
    main()
