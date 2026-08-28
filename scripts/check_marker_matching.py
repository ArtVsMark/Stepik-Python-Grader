#!/usr/bin/env python3
"""scripts/check_marker_matching.py — маркер сверяется целиком, а не началом.

Правило 141 каталога: структурный маркер — скрытый комментарий, имя метки,
ключ, префикс ветки — сверяется **целой строкой**, а не её началом. Сравнение
началом молча засчитывает соседний маркер, который с проверяемого начинается, и
ошибается в сторону «прошло»: `<!-- ci-failures -->` совпал бы с
`<!-- ci-failures-old -->`, а метка `needs-rebase` — с `needs-rebase-manual`.

Предмет узкий и потому проверяемый: **константы-маркеры** — имена вида
``MARKER``/``*_MARKER`` и строки, начинающиеся с ``<!--``. Такую константу
нельзя подставлять в ``startswith``: у неё нет продолжения, которое имело бы
смысл, — она либо есть целиком, либо её нет.

Что НЕ проверяется и почему: обычные префиксы (``agent/`` у веток,
``test-results-`` у отчётов, ``area/`` у меток) — там начало строки и есть
предмет, и требовать целого совпадения значило бы ломать рабочий код ради
буквы правила.

Запуск::

    python scripts/check_marker_matching.py
"""

from __future__ import annotations

import ast
import pathlib
import sys

__all__ = ["main", "marker_names", "markers_matched_by_prefix"]

_ROOT = pathlib.Path(__file__).parent.parent
_PLACES = ("scripts", "src", "tests", ".claude/hooks")


def marker_names(tree: ast.Module) -> set[str]:
    """Имена констант-маркеров модуля: по имени и по форме значения."""
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id.rstrip("_")
            # Константа, названная префиксом, префиксом и является: за ней идёт
            # продолжение, у которого есть смысл (путь к генератору, имя ветки).
            # Правило про маркеры, и различает их имя — оно же и объясняет
            # читателю, почему здесь `startswith` законен.
            if name.endswith(("PREFIX", "PREFIXES")):
                continue
            if name.endswith("MARKER") or value.value.startswith("<!--"):
                found.add(target.id)
    return found


def markers_matched_by_prefix(sources: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Места, где константа-маркер сверяется началом строки.

    Args:
        sources: подмена содержимого для тестов: путь → текст.

    Returns:
        Пары (путь, имя маркера).
    """
    if sources is None:
        sources = {}
        for place in _PLACES:
            for path in sorted((_ROOT / place).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                sources[str(path.relative_to(_ROOT))] = path.read_text(encoding="utf-8")

    problems: list[tuple[str, str]] = []
    for name, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        markers = marker_names(tree)
        if not markers:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"startswith", "endswith", "removeprefix", "removesuffix"}:
                continue
            for argument in node.args:
                if isinstance(argument, ast.Name) and argument.id in markers:
                    problems.append((name, argument.id))
    return problems


def main() -> int:
    """0 — маркеры сверяются целиком; 1 — нет."""
    problems = markers_matched_by_prefix()
    if problems:
        print("маркер сверяется началом, а не целиком:", file=sys.stderr)
        for name, marker in problems:
            print(f"  • {name}: {marker}", file=sys.stderr)
        print(
            "\nУ маркера нет осмысленного продолжения: он либо есть целиком, либо его "
            "нет. Сравнение началом молча засчитывает соседний маркер, который с "
            "проверяемого начинается, — и ошибается в сторону «прошло».",
            file=sys.stderr,
        )
        return 1

    print("маркеры сверяются целиком")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
