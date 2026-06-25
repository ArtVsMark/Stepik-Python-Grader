"""Unit-тесты для normalizers.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import normalizers
from normalizers import normalize_floats, normalize_whitespace, sort_lines


# ---------------------------------------------------------------------------
# normalize_floats — основные сценарии
# ---------------------------------------------------------------------------


def test_normalize_floats_rounds_to_9() -> None:
    """Длинный float округляется до 9 знаков."""
    assert normalize_floats("5.000000000000001") == "5.0"


def test_normalize_floats_pi() -> None:
    """Число π обрезается до 9 знаков после запятой."""
    assert normalize_floats("3.14159265358979") == "3.141592654"


def test_normalize_floats_small_value_scientific() -> None:
    """Очень малое значение переводится в научную нотацию."""
    assert normalize_floats("0.0000001") == "1e-07"


def test_normalize_floats_negative() -> None:
    """Отрицательный float нормализуется корректно."""
    result = normalize_floats("-3.14159265358979")
    assert result == "-3.141592654"


def test_normalize_floats_integer_unchanged() -> None:
    """Целые числа (без точки) не затрагиваются."""
    assert normalize_floats("42") == "42"


def test_normalize_floats_multiline() -> None:
    """Нормализация применяется построчно."""
    text = "3.14159265358979\n2.71828182845904"
    result = normalize_floats(text)
    assert result == "3.141592654\n2.718281828"


def test_normalize_floats_mixed_line() -> None:
    """Float среди текста нормализуется, текст сохраняется.

    round(1.23456789012345, 9) == 1.23456789 — Python убирает trailing zero,
    поэтому проверяем 8 значащих знаков (без нуля).
    """
    result = normalize_floats("result = 1.23456789012345 ok")
    assert "1.23456789" in result
    assert "result" in result
    assert "ok" in result


def test_normalize_floats_scientific_input() -> None:
    """Научная нотация на входе обрабатывается корректно."""
    result = normalize_floats("1.5e+10")
    assert result == "15000000000.0"


def test_normalize_floats_empty_string() -> None:
    """Пустая строка возвращается без изменений."""
    assert normalize_floats("") == ""


# ---------------------------------------------------------------------------
# normalize_floats — ветка ValueError (строки 32-33)
# ---------------------------------------------------------------------------


def test_normalize_floats_value_error_branch(monkeypatch) -> None:
    """Ветка except ValueError: возвращает исходную группу без изменений.

    Патчим float() через monkeypatch так, чтобы он выбрасывал ValueError
    при вызове из _round_float — это покрывает строки 32-33.
    """
    original_float = float

    def _raising_float(x):
        # Бросаем ValueError только для строк (т.е. внутри _round_float),
        # но не для чисел — иначе сломаем весь интерпретатор.
        if isinstance(x, str):
            raise ValueError("mocked")
        return original_float(x)

    import builtins as _builtins_module

    original = _builtins_module.float
    try:
        _builtins_module.float = _raising_float  # type: ignore[assignment]
        result = normalize_floats("3.14")
        assert result == "3.14"  # исходная группа возвращена без изменений
    finally:
        _builtins_module.float = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# sort_lines
# ---------------------------------------------------------------------------


def test_sort_lines_basic() -> None:
    """Строки сортируются лексикографически."""
    assert sort_lines("banana\napple\ncherry") == "apple\nbanana\ncherry"


def test_sort_lines_already_sorted() -> None:
    """Уже отсортированный вывод остаётся без изменений."""
    assert sort_lines("a\nb\nc") == "a\nb\nc"


def test_sort_lines_outer_strip_only() -> None:
    """sort_lines делает strip() на всём тексте, но НЕ на каждой строке.

    '  b\na  '.strip() == 'b\na  ' → sorted → ['a  ', '  b']
    Но '  b\na  '.strip().splitlines() == ['  b', 'a  '],
    поэтому пробелы внутри строк СОХРАНЯЮТСЯ, но сам вход strip()
    удаляет внешние пробелы: '  b' становится первой строкой,
    'a  ' — второй. Сортировка: 'a  ' < '  b' (буква 'a' > ' ')... нет,
    ' ' (32) < 'a' (97) → сначала '  b', потом 'a  '.
    """
    result = sort_lines("  b\na  ")
    # strip() удаляет лишь внешние \n/пробелы вокруг всей строки:
    # '  b\na  ' → strip() → '  b\na' → splitlines() → ['  b', 'a'] → sorted → ['  b', 'a']
    # (' ' < 'a' ASCII), значит '  b' стоит раньше 'a'
    assert result == "  b\na"


# ---------------------------------------------------------------------------
# normalize_whitespace
# ---------------------------------------------------------------------------


def test_normalize_whitespace_collapses_spaces() -> None:
    """Множественные пробелы схлопываются в один."""
    assert normalize_whitespace("a   b   c") == "a b c"


def test_normalize_whitespace_strips_line() -> None:
    """Ведущие и завершающие пробелы в строке удаляются."""
    assert normalize_whitespace("  hello  ") == "hello"


def test_normalize_whitespace_multiline() -> None:
    """Нормализация применяется к каждой строке."""
    result = normalize_whitespace("  a  b  \n  c  d  ")
    assert result == "a b\nc d"
