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

import contextlib
import csv
import io
import pathlib
import sys
import warnings
from collections.abc import Iterator
from typing import Any

__all__ = ["_print_tabular", "_rows_to_csv", "_rows_to_markdown", "human_warnings"]

#: Корень пакета — по нему отличаем СВОИ предупреждения от чужих (issue #1466).
_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)


def _is_ours(filename: str) -> bool:
    """Предупреждение испущено нашим кодом, а не сторонней библиотекой."""
    try:
        return str(pathlib.Path(filename).resolve()).startswith(_PACKAGE_ROOT)
    except (OSError, ValueError):
        return False


@contextlib.contextmanager
def human_warnings() -> Iterator[None]:
    """Печатать НАШИ предупреждения как обращение к человеку (issue #1466).

    Загрузчик набора сообщает о неполном наборе через ``warnings.warn``, и это
    не недосмотр: ``warnings`` здесь ещё и транспорт — ядро ловит их и кладёт
    в машиночитаемый ключ ``warnings`` вывода ``--output json``, чтобы CI
    отличал полный прогон от урезанного (#935). Менять транспорт нельзя, не
    потеряв эту половину.

    Менять надо ПОКАЗ. Стандартный ``showwarning`` печатает четыре строки, из
    которых пользователю адресована одна::

        /…/src/stepik_grader/core/grader_core.py:789: UserWarning: <текст>
          test_cases = load_test_cases(test_dir)

    Остальное — про наши внутренности: путь в ``src/``, номер строки, имя
    категории, кусок нашего кода. На учебной поверхности это тот же дефект,
    что и весь подэпик #917: студент приходит сюда, уже не понимая, что
    происходит, видит путь в чужие исходники и решает, что сломался грейдер, а
    не его набор тестов.

    ЧУЖИЕ предупреждения печатаются как раньше. ``DeprecationWarning`` из
    сторонней библиотеки без файла и строки диагностировать нечем, а выдавать
    его за наше обращение к пользователю — прямая ложь об источнике.
    """
    previous = warnings.showwarning

    def show(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        if category is UserWarning and _is_ours(filename):
            # В stderr, а не в stdout: `--output json` пишет в stdout, и
            # человекочитаемая строка сделала бы его неразбираемым.
            print(f"⚠️  {message}", file=sys.stderr)
            return
        previous(message, category, filename, lineno, file, line)

    warnings.showwarning = show
    try:
        yield
    finally:
        # Возврат обязателен и в `finally`: перекрытие глобальное, и утечка из
        # CLI изменила бы поведение всего, что запускается следом в том же
        # процессе, — включая набор тестов.
        warnings.showwarning = previous


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
