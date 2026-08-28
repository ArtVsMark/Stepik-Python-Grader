#!/usr/bin/env python3
"""scripts/check_truncation_marks.py — обрезанное сообщает, что оно обрезано.

Правило 016 каталога: урезанный результат обязан говорить, что он урезан, и
насколько. Иначе он выглядит полным — и читатель, человек или машина, делает
вывод по половине данных, не зная, что это половина.

Предмет проверяемый: **функция, которая режет строку или список по пределу**.
Такую функцию узнаём по срезу с константой-пределом (``text[:_MAX_CHARS]``,
``items[:limit]``). Она обязана оставлять признак обрыва — вернуть флаг, дописать
многоточие, сказать «и ещё N».

Что НЕ считается обрезкой: срез по фиксированному малому числу (``parts[:2]``,
``lines[:3]``) — это разбор формата, а не усечение результата, и маркер там
означал бы шум в каждой второй строке.

Гейт смотрит на **функцию целиком**: маркер может стоять не в строке среза, а
рядом — в возвращаемом кортеже, в следующей строке, в сообщении. Требовать
конкретной формы значило бы ломать рабочий код ради буквы.

Запуск::

    python scripts/check_truncation_marks.py
"""

from __future__ import annotations

import ast
import pathlib
import sys

__all__ = ["MARK_NAMES", "main", "truncations_without_mark"]

_ROOT = pathlib.Path(__file__).parent.parent
_PLACES = ("src/stepik_grader", "scripts")

#: Слова, которыми в проекте обозначают обрыв. Если хоть одно есть в теле
#: функции — читатель узнает, что результат неполон.
MARK_NAMES: tuple[str, ...] = (
    "truncated",
    "clipped",
    "обрез",
    "обрыв",
    "…",
    "...",
    "и ещё",
    "more",
    "overflow",
    "остальные",
    "limit_hit",
    # Полная величина рядом с урезанным списком — тоже признак обрыва, и
    # честнее прочих: читатель видит, сколько было, а не только сколько дали
    # (`{"elems": elems, "n": len(seq)}` в трассировщике).
    "len(",
)

#: Имена, по которым видно, что предел — настоящий предел, а не индекс разбора.
_LIMIT_HINTS = ("max", "MAX", "limit", "LIMIT", "budget", "BUDGET", "cap", "CAP")


def _is_limited_slice(node: ast.Subscript) -> bool:
    """Срез ли это по пределу — или разбор формата по фиксированному индексу."""
    if not isinstance(node.slice, ast.Slice) or node.slice.upper is None:
        return False
    upper = node.slice.upper
    if isinstance(upper, ast.Name):
        return any(hint in upper.id for hint in _LIMIT_HINTS)
    if isinstance(upper, ast.Attribute):
        return any(hint in upper.attr for hint in _LIMIT_HINTS)
    # Выражение в границе (`text[: limit - 1]`) считается пределом, только если
    # предел назван по имени внутри него: `body[: end - start]` — это окно
    # разбора, а не усечение результата, и маркер там был бы шумом.
    if isinstance(upper, ast.BinOp):
        return any(
            hint in part.id
            for part in ast.walk(upper)
            if isinstance(part, ast.Name)
            for hint in _LIMIT_HINTS
        )
    return False


def _limit_is_a_parameter(node: ast.Subscript, parameters: set[str]) -> bool:
    """Предел среза задан аргументом функции — значит объявлен в контракте."""
    upper = node.slice.upper if isinstance(node.slice, ast.Slice) else None
    if upper is None:
        return False
    return any(part.id in parameters for part in ast.walk(upper) if isinstance(part, ast.Name))


def truncations_without_mark(sources: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Функции, которые режут по пределу и не говорят об этом.

    Args:
        sources: подмена содержимого для тестов: путь → текст.

    Returns:
        Пары (путь, имя функции).
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
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            # Предел, который вызывающий задал сам (параметр функции), — часть
            # контракта: `select(..., max_top=3)` возвращает лучшие три и
            # говорит об этом сигнатурой. Молчаливой обрезкой это не является,
            # в отличие от предела-константы, о котором вызывающий не знает.
            parameters = {
                argument.arg
                for group in (node.args.args, node.args.kwonlyargs, node.args.posonlyargs)
                for argument in group
            }
            slices = [
                child
                for child in ast.walk(node)
                if isinstance(child, ast.Subscript)
                and _is_limited_slice(child)
                and not _limit_is_a_parameter(child, parameters)
            ]
            if not slices:
                continue
            body = ast.unparse(node)
            if not any(mark in body for mark in MARK_NAMES):
                problems.append((name, node.name))
    return problems


def main() -> int:
    """0 — обрезанное себя называет; 1 — где-то режут молча."""
    problems = truncations_without_mark()
    if problems:
        print("обрезка без маркера обрыва:", file=sys.stderr)
        for name, function in problems:
            print(f"  • {name}: {function}()", file=sys.stderr)
        print(
            "\nУрезанный результат без признака обрыва выглядит полным, и вывод по нему "
            "делают как по целому. Верните флаг, допишите многоточие или скажите, "
            "сколько осталось.",
            file=sys.stderr,
        )
        return 1

    print("обрезка везде сопровождается признаком обрыва")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
