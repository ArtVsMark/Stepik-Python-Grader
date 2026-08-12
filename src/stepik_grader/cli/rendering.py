"""cli/rendering.py — табличный вывод csv/markdown (issue #121, Phase 1).

Архитектурный слой: Application / CLI (leaf-модуль).

Выделено из cli/__init__.py без изменения поведения (issue #121, Stage 1
эпика #117): pure rendering helpers — не читают mutable module-level state
(`_LANG`/`_LOCALE_MESSAGES`) и не импортируют
`stepik_grader.cli`. Реэкспортированы фасадом (`cli/__init__.py`) как
`cli._rows_to_csv`/`cli._rows_to_markdown`/`cli._print_tabular` для
обратной совместимости с существующими monkeypatch-тестами; `CliContext`
(issue #120) получает `print_tabular` через `_build_cli_context()`,
которая резолвит это имя как facade-global в момент вызова.
"""

from __future__ import annotations

import csv
import io
from typing import Any

__all__ = ["_print_tabular", "_rows_to_csv", "_rows_to_markdown"]


def _rows_to_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """Отрендерить список flat-словарей в CSV-строку (заголовок + строки).

    Отсутствующие в конкретной строке ключи (например, "error" у успешных
    бенчмарк-строк) печатаются как пустая ячейка -- extrasaction="ignore"
    отбрасывает лишние ключи, не входящие в fieldnames.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _md_cell(value: object) -> str:
    """Значение как содержимое ячейки Markdown-таблицы (issue #997, DES-2-03).

    Экранируется ровно то, что ломает саму таблицу: вертикальная черта (иначе
    одна ячейка распадается на несколько и строка съезжает относительно шапки) и
    переводы строк (Markdown-таблица однострочна по определению — реальный текст
    после первого ``\\n`` просто исчезал из отчёта). Символ ``\\r`` схлопывается
    вместе с ``\\n``, чтобы вывод решения из Windows не оставлял хвостов.

    Это чинит `--output markdown` на самых обычных данных: сообщение об ошибке
    из трейсбека занимает несколько строк, а вывод решения с таблицей содержит
    ``|`` — оба случая разваливали отчёт, ради которого формат и существует.
    """
    text = str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("|", "\\|").replace("\n", "<br>")


def _rows_to_markdown(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    """Отрендерить список flat-словарей в Markdown-таблицу."""
    header = "| " + " | ".join(_md_cell(f) for f in fieldnames) + " |"
    separator = "| " + " | ".join("---" for _ in fieldnames) + " |"
    lines = [header, separator]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(row.get(f, "")) for f in fieldnames) + " |")
    return "\n".join(lines)


def _print_tabular(output: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Напечатать rows как csv или markdown (output уже проверен вызывающей стороной)."""
    if output == "csv":
        print(_rows_to_csv(rows, fieldnames), end="")
    else:
        print(_rows_to_markdown(rows, fieldnames))
