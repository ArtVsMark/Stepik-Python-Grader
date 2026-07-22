"""issue #646 (T1): ``_is_safe_constant`` покрыт по всем рекурсивным веткам.

Предикат (``core/mode_detector.py``) отличает безопасное константное выражение
— литералы, арифметику констант, вложенные контейнеры — от всего, что содержит
вызовы/имена/атрибуты. До аудита модуль-тест не касался его ни одной строкой,
хотя ``wrapper_builder`` полагается на него, решая, можно ли инъектировать
top-level присваивание решения без исполнения побочных эффектов.
"""

from __future__ import annotations

import ast

import pytest

from stepik_grader.core.mode_detector import _is_safe_constant


def _expr(src: str) -> ast.expr:
    """Единственное выражение исходника как AST-узел."""
    return ast.parse(src, mode="eval").body


@pytest.mark.parametrize(
    "src",
    [
        "5",
        "'text'",
        "3.14",
        "True",
        "None",
        "-5",  # UnaryOp USub над константой
        "+5",  # UnaryOp UAdd
        "~5",  # UnaryOp Invert
        "2 + 3",  # BinOp двух констант
        "10 ** 9 + 7",  # вложенный BinOp
        "-(2 + 3)",  # UnaryOp над BinOp
        "[1, 2, 3]",  # List
        "(1, 2)",  # Tuple
        "{1, 2}",  # Set
        "{'a': 1, 'b': 2}",  # Dict
        "[-1, [2, 3], {4}]",  # вложенные контейнеры
    ],
)
def test_accepts_constant_expressions(src: str) -> None:
    assert _is_safe_constant(_expr(src)) is True


@pytest.mark.parametrize(
    "src",
    [
        "x",  # Name
        "foo()",  # Call
        "date(2020, 1, 1)",  # Call с константными аргументами — всё равно вызов
        "a + 1",  # BinOp с именем в операнде
        "-x",  # UnaryOp над именем
        "[1, f()]",  # List с вызовом
        "(1, x)",  # Tuple с именем
        "{g()}",  # Set с вызовом
        "{'k': v()}",  # Dict: значение — вызов
        "{k: 1}",  # Dict: ключ — имя
        "{**d}",  # Dict-unpack: key is None, значение — имя
        "obj.attr",  # Attribute
        "1 if x else 2",  # IfExp — узел без обработки → return False
    ],
)
def test_rejects_calls_names_and_attributes(src: str) -> None:
    assert _is_safe_constant(_expr(src)) is False
