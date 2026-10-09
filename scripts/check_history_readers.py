#!/usr/bin/env python3
"""scripts/check_history_readers.py — у каждой таблицы истории есть читатель (issue #1587).

Разбор 08.10.2026 нашёл две таблицы базы истории, которые пополняются на
каждом действии пользователя и не читаются никем: ``stepik_submissions``
(вердикт Stepik рядом с нашим) и ``glossary_hits`` (переходы из ошибки в
карточку). Функции-читатели у них есть, но вызывает их только тест.

ПОЧЕМУ ГЕЙТ, А НЕ ЛИНТЕР. Ни линтер, ни ``vulture`` такого не ловят: писатель
вызывается, функция чтения существует, мёртвого кода нет. Мёртв результат —
данные копятся, а вопрос, ради которого их завели, никто не задаёт.

КАК ПРОВЕРЯЕТСЯ — ОБЪЯВЛЕНИЕМ, А НЕ ЭВРИСТИКОЙ ПО SQL. :data:`TABLE_READERS`
называет для каждой таблицы публичные функции ``core/history.py``, которые её
читают. Гейт сверяет три вещи:

* каждая ``CREATE TABLE`` из ``history.py`` объявлена, и каждое объявление
  указывает на существующую таблицу и функцию;
* хотя бы одна функция-читатель вызывается в ``src/stepik_grader`` вне
  ``history.py``. Тесты не считаются: им читатель нужен, продукту нет;
* таблица без читателя объявлена ЗАДЕЛОМ в :data:`RESERVED` с номером
  задачи. Задел, у которого читатель появился, — устаревшее объявление.

МЯГКОСТЬ — РЕШЕНИЕ ВЛАДЕЛЬЦА. По умолчанию находки печатаются
предупреждением, и код возврата ``0``: таблица без читателя — долг, а не
поломка, и держать из-за неё мерж незачем. ``--strict`` даёт ``1``: так гейт
прогоняется тестом на отвергающем предмете.

Исходы: ``0`` — чисто или только предупреждения · ``1`` — находки при
``--strict`` · ``2`` — проверка не отработала (нет ``history.py``).

Запуск::

    python scripts/check_history_readers.py
    python scripts/check_history_readers.py --strict
"""

import argparse
import ast
import contextlib
import os
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "RESERVED",
    "TABLE_READERS",
    "findings",
    "main",
    "readers_used",
    "schema_tables",
]

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "src" / "stepik_grader"
_HISTORY = _PACKAGE / "core" / "history.py"

#: Таблица → публичные функции ``core/history.py``, которые её читают.
#: Новая таблица без строки здесь — находка: читатель назначается при создании.
TABLE_READERS: dict[str, tuple[str, ...]] = {
    "runs": ("read_recent_runs",),
    "case_results": ("read_recent_runs",),
    "lint_violations": ("read_recent_runs",),
    "task_progress": ("read_task_progress",),
    "stepik_submissions": ("read_stepik_submissions",),
    "glossary_hits": ("read_glossary_hits",),
}

#: Таблица → номер задачи, которая подключит читателя. Объявленный задел —
#: не находка; задел, у которого читатель уже есть, — находка.
RESERVED: dict[str, int] = {
    "glossary_hits": 1608,
}

_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)


def schema_tables(history: Path) -> set[str]:
    """Имена таблиц из ``CREATE TABLE`` в исходнике истории."""
    return set(_CREATE_TABLE.findall(history.read_text(encoding="utf-8")))


def _defined_functions(history: Path) -> set[str]:
    """Функции верхнего уровня ``history.py``."""
    tree = ast.parse(history.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def readers_used(package: Path, history: Path, names: set[str]) -> set[str]:
    """Какие из функций ``names`` упомянуты в пакете вне ``history.py``."""
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names))) + r")\b")
    used: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        if path.resolve() == history.resolve():
            continue
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    return used


def findings(
    package: Path = _PACKAGE,
    history: Path = _HISTORY,
    *,
    readers: dict[str, tuple[str, ...]] | None = None,
    reserved: dict[str, int] | None = None,
) -> list[str]:
    """Находки гейта словами; пустой список — чисто."""
    readers = TABLE_READERS if readers is None else readers
    reserved = RESERVED if reserved is None else reserved
    tables = schema_tables(history)
    defined = _defined_functions(history)
    names = {name for funcs in readers.values() for name in funcs}
    used = readers_used(package, history, names) if names else set()

    found: list[str] = []
    for table in sorted(tables - readers.keys()):
        found.append(f"таблица {table}: читатель не объявлен в TABLE_READERS")
    for table in sorted(readers.keys() - tables):
        found.append(f"таблица {table}: объявлена, но в схеме истории её нет")
    for table in sorted(reserved.keys() - readers.keys()):
        found.append(f"таблица {table}: задел объявлен, а таблица — нет")
    for table, funcs in sorted(readers.items()):
        if table not in tables:
            continue
        for name in funcs:
            if name not in defined:
                found.append(f"таблица {table}: функции {name} в history.py нет")
        has_reader = any(name in used for name in funcs)
        if has_reader and table in reserved:
            found.append(
                f"таблица {table}: читатель появился — снимите задел под #{reserved[table]}"
            )
        elif not has_reader and table not in reserved:
            found.append(
                f"таблица {table}: пишется, но не читается вне history.py "
                f"({', '.join(funcs)}) — подключите читателя или объявите задел в RESERVED"
            )
    return found


def main(argv: list[str] | None = None) -> int:
    """Точка входа: предупреждение по умолчанию, отказ при ``--strict``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="находка — код 1")
    parser.add_argument("--history", type=Path, default=_HISTORY)
    parser.add_argument("--package", type=Path, default=_PACKAGE)
    args = parser.parse_args(argv)
    if not args.history.is_file():
        print(f"проверка не отработала: нет {args.history}", file=sys.stderr)
        return 2
    found = findings(args.package, args.history)
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else ""
    for line in found:
        print(f"{prefix}{line}")
    if not found:
        reserved = ", ".join(f"{t} (#{n})" for t, n in sorted(RESERVED.items()))
        print(f"у каждой таблицы истории есть читатель; задел: {reserved or 'нет'}")
        return 0
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
