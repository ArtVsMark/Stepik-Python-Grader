"""normalizers.py — утилиты нормализации вывода для сравнения тест-кейсов.

Используется grader.py: ``normalize_floats`` импортируется как
``_normalize_output_line`` и применяется построчно при сравнении фактического
вывода решения с ожидаемым (см. ``grader.run_single_test``).
"""

from __future__ import annotations

import re

_FLOAT_RE = re.compile(r"-?\d+\.\d+(?:[eE][+-]?\d+)?")


def normalize_floats(text: str) -> str:
    """Нормализует числа с плавающей точкой, округляя до 9 знаков.

    Применяет ``str(round(float(x), 9))`` к каждому совпадению регулярного
    выражения, построчно. Для очень малых значений это даёт научную нотацию
    (``0.0000001`` → ``1e-07``) — поведение совпадает с прежней inline-функцией
    grader._normalize_output_line, которая является источником истины.

    Примеры:
        '5.000000000000001' → '5.0'
        '3.14159265358979'  → '3.141592654'
        '0.0000001'         → '1e-07'
    """

    def _round_float(m: re.Match) -> str:  # type: ignore[type-arg]
        try:
            return str(round(float(m.group()), 9))
        except ValueError:
            return m.group()

    return "\n".join(_FLOAT_RE.sub(_round_float, line) for line in text.split("\n"))


# NOTE: utility, not called in production paths
def sort_lines(output: str) -> str:
    """Сортирует строки вывода (для задач где порядок строк не важен)."""
    return "\n".join(sorted(output.strip().splitlines()))


# NOTE: utility, not called in production paths
def normalize_whitespace(output: str) -> str:
    """Нормализует пробелы: strip + схлопывает множественные пробелы."""
    return "\n".join(" ".join(line.split()) for line in output.splitlines())
