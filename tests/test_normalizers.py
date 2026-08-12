"""Unit-тесты для normalizers.py."""

from __future__ import annotations

import pytest

from stepik_grader.core.normalizers import (
    floats_equal_with_precision,
    normalize_floats,
    normalize_whitespace,
    sort_lines,
    split_output_lines,
)

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
    assert normalize_floats("-3.14159265358979") == "-3.141592654"


def test_normalize_floats_integer_unchanged() -> None:
    """Целые числа (без точки) не затрагиваются."""
    assert normalize_floats("42") == "42"


def test_normalize_floats_multiline() -> None:
    """Нормализация применяется построчно."""
    text = "3.14159265358979\n2.71828182845904"
    assert normalize_floats(text) == "3.141592654\n2.718281828"


def test_normalize_floats_mixed_line() -> None:
    """Float среди текста нормализуется, текст сохраняется.

    round(1.23456789012345, 9) == 1.23456789 — Python убирает trailing zero.
    """
    result = normalize_floats("result = 1.23456789012345 ok")
    assert "1.23456789" in result
    assert "result" in result
    assert "ok" in result


def test_normalize_floats_scientific_input() -> None:
    """Научная нотация на входе обрабатывается корректно."""
    assert normalize_floats("1.5e+10") == "15000000000.0"


def test_normalize_floats_empty_string() -> None:
    """Пустая строка возвращается без изменений."""
    assert normalize_floats("") == ""


# ---------------------------------------------------------------------------
# normalize_floats — экстремальные РЕАЛЬНЫЕ входы (issue #405)
#
# Прежний тест здесь монкипатчил ``builtins.float``, чтобы искусственно войти в
# защитную ветку ``except ValueError``. Проверялось поведение самого монкипатча,
# а не функции — тавтология. Ветка недостижима реальным входом (``_FLOAT_RE``
# матчит только валидный float-синтаксис, а overflow ``float()`` даёт ``inf``,
# не ``ValueError``), поэтому помечена ``# pragma: no cover``, а тест заменён
# проверками фактического поведения на краевых входах.
# ---------------------------------------------------------------------------


def test_normalize_floats_overflow_to_inf() -> None:
    """Оверфлоу float() даёт 'inf' (не ValueError) — round/str проносят его насквозь."""
    assert normalize_floats("1.0e999") == "inf"


def test_normalize_floats_overflow_to_negative_inf() -> None:
    """Отрицательный оверфлоу → '-inf' (знак сохраняется, ветка ValueError не нужна)."""
    assert normalize_floats("-1.0e999") == "-inf"


def test_normalize_floats_idempotent_on_extremes() -> None:
    """Повторная нормализация — тождество (round до 9 знаков — стабильная точка)."""
    for text in ("3.14159265358979", "0.0000001", "-2.71828182845904", "1.5e+10", "1.0e999"):
        once = normalize_floats(text)
        assert normalize_floats(once) == once


# ---------------------------------------------------------------------------
# normalize_floats — dotted-числа НЕ корёжатся (issue #410, B5)
#
# Прежний ``_FLOAT_RE`` матчил float ВНУТРИ версий/IP: ``3.10.5`` → ``3.1.5``
# (первые два сегмента как один float ``3.10`` → ``3.1``), а ``1.2.3.4`` — рвался
# по-сегментно. lookbehind/lookahead (`(?<![\d.])`/`(?!\.\d)`) защищают именно
# dotted-последовательности, не трогая обычные float'ы.
# ---------------------------------------------------------------------------


def test_normalize_floats_preserves_semver_like_version() -> None:
    """Версия ``3.10.5`` не должна схлопываться до ``3.1.5``."""
    assert normalize_floats("3.10.5") == "3.10.5"


def test_normalize_floats_preserves_ipv4() -> None:
    """IPv4-адрес не должен нормализоваться по сегментам."""
    assert normalize_floats("192.168.1.1") == "192.168.1.1"
    assert normalize_floats("1.2.3.4") == "1.2.3.4"


def test_normalize_floats_preserves_version_in_text() -> None:
    """Версия внутри текста остаётся дословной."""
    assert normalize_floats("Python 3.10.5") == "Python 3.10.5"
    assert normalize_floats("v2.7.18") == "v2.7.18"


def test_normalize_floats_still_rounds_plain_floats_after_fix() -> None:
    """B5-защита не должна ломать нормализацию обычных (не dotted) float'ов."""
    assert normalize_floats("5.000000000000001") == "5.0"
    assert normalize_floats("3.14159265358979") == "3.141592654"
    assert normalize_floats("a 1.0 b 2.5 c") == "a 1.0 b 2.5 c"


# ---------------------------------------------------------------------------
# sort_lines
# ---------------------------------------------------------------------------


def test_sort_lines_basic() -> None:
    """Строки сортируются лексикографически."""
    assert sort_lines("banana\napple\ncherry") == "apple\nbanana\ncherry"


def test_sort_lines_already_sorted() -> None:
    """Уже отсортированный вывод остаётся без изменений."""
    assert sort_lines("a\nb\nc") == "a\nb\nc"


def test_sort_lines_strips_outer_newlines() -> None:
    """strip() удаляет внешние переносы вокруг всего текста."""
    assert sort_lines("\nb\na\n") == "a\nb"


def test_sort_lines_single_line() -> None:
    """Одна строка возвращается без изменений."""
    assert sort_lines("hello") == "hello"


def test_sort_lines_numbers() -> None:
    """Числа сортируются лексикографически (не численно)."""
    assert sort_lines("10\n2\n1") == "1\n10\n2"


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
    assert normalize_whitespace("  a  b  \n  c  d  ") == "a b\nc d"


# ---------------------------------------------------------------------------
# split_output_lines — разбор строк для сравнения вывода (issue #843)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected,why",
    [
        ("", [], "пустой вывод — ноль строк"),
        ("a\n", ["a"], "завершающий перевод не создаёт пустую строку"),
        ("a", ["a"], "строка без завершающего перевода"),
        ("a\nb", ["a", "b"], "две строки"),
        ("a\n\n", ["a", ""], "пустая строка в конце значима"),
        ("\na", ["", "a"], "пустая строка в начале значима"),
        ("a\r\nb\r\n", ["a", "b"], "CRLF — перевод строки Windows"),
        ("a\rb", ["a", "b"], "одиночный CR — тоже разделитель (universal newlines)"),
        ("a\n\nb\n", ["a", "", "b"], "пустая строка внутри сохраняется"),
    ],
)
def test_split_output_lines_matches_real_newlines(text: str, expected: list[str], why: str) -> None:
    """По настоящим переводам строки поведение совпадает со splitlines()."""
    assert split_output_lines(text) == expected, why
    assert split_output_lines(text) == text.splitlines(), why


@pytest.mark.parametrize(
    "code_point,name",
    [
        (0x0B, "VT"),
        (0x0C, "FF"),
        (0x1C, "FS"),
        (0x1D, "GS"),
        (0x1E, "RS"),
        (0x85, "NEL"),
        (0x2028, "U+2028"),
        (0x2029, "U+2029"),
    ],
)
def test_split_output_lines_keeps_exotic_controls_as_data(code_point: int, name: str) -> None:
    """Ровно предмет #843: эти символы — данные внутри строки, а не разделители.

    `str.splitlines()` разрезал бы строку по каждому из них, из-за чего вывод
    `a<VT>b` признавался равным двум настоящим строкам — AC на неверном решении.
    """
    text = f"a{chr(code_point)}b"
    assert split_output_lines(text) == [text], name
    assert len(text.splitlines()) == 2, f"{name}: предпосылка теста устарела"


def test_split_output_lines_is_symmetric_for_file_and_stream() -> None:
    """Файл (universal newlines) и поток дают одинаковое разбиение."""
    assert split_output_lines("a\r\nb") == split_output_lines("a\nb") == ["a", "b"]


# ---------------------------------------------------------------------------
# floats_equal_with_precision — толерантность без потери требования разрядности
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "expected", "equal", "why"),
    [
        # issue #940: незначащие нули ожидания значимы — это требование формата.
        ("12.3", "12.30", False, "решение дало меньше знаков, чем требует ожидание"),
        ("1.5", "1.50", False, "то же на одном знаке"),
        ("100.0", "100.00", False, "нули после точки не «лишние», если их ждут"),
        # Прежняя толерантность цела: хвост двоичного представления прощается.
        ("0.30000000000000004", "0.3", True, "знаков не меньше — мусор за 9-м прощается"),
        ("3.14159265358979", "3.141592654", True, "округление до 9 знаков"),
        ("12.30", "12.30", True, "совпадение как есть"),
        ("12.300", "12.30", True, "знаков больше требуемого — требование выполнено"),
        # Разные формы записи: сравнивать разрядность нечего, работает округление.
        ("1e-07", "0.0000001", True, "экспонента против десятичной записи"),
        ("0.0000001", "1e-07", True, "то же в обратную сторону"),
        # Не-числовые расхождения нормализация не спасает.
        ("12.3", "12.4", False, "разные величины"),
        ("abc", "abd", False, "текст сравнивается дословно"),
    ],
)
def test_floats_equal_with_precision(actual: str, expected: str, equal: bool, why: str) -> None:
    """Толерантность к записи float не стирает требование числа знаков (issue #940)."""
    assert floats_equal_with_precision(actual, expected) is equal, why
