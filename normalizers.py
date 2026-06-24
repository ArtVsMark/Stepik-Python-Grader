"""normalizers.py — утилиты нормализации вывода для сравнения тест-кейсов.

NOTE: Этот модуль в настоящее время НЕ импортируется grader.py — grader
использует собственную inline-функцию _normalize_output_line.
Модуль сохранён для возможного будущего рефакторинга.
"""

from __future__ import annotations

import re


def normalize_floats(output: str, decimals: int = 9) -> str:
    """Нормализует числа с плавающей точкой в строке вывода.

    Примеры:
        '5.000000000000001' → '5.0' (при decimals=9 → '5.0')
        '3.14159265358979'  → '3.141592654' (при decimals=9)
    """

    def _round(m: re.Match) -> str:  # type: ignore[type-arg]
        try:
            val = round(float(m.group()), decimals)
            # Убираем хвостовые нули после точки, сохраняя хотя бы один знак
            s = f"{val:.{decimals}f}".rstrip("0")
            if s.endswith("."):
                s += "0"
            return s
        except ValueError:
            return m.group()

    return re.sub(r"-?\d+\.\d+(?:[eE][+-]?\d+)?", _round, output)


def sort_lines(output: str) -> str:
    """Сортирует строки вывода (для задач где порядок строк не важен)."""
    return "\n".join(sorted(output.strip().splitlines()))


def normalize_whitespace(output: str) -> str:
    """Нормализует пробелы: strip + схлопывает множественные пробелы."""
    return "\n".join(" ".join(line.split()) for line in output.splitlines())
