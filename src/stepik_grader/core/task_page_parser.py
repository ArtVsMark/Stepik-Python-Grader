"""task_page_parser.py — парсинг HTML-текста задачи Stepik (issue #302).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Выделено из ``downloader.py`` (issue #302, SRP): чистый разбор HTML текста
шага — таблица тест-кейсов и внешние ссылки на тесты (ZIP/GitHub). Сеть,
запись на диск и оркестрация остаются в ``downloader.py``; здесь только
``str -> данные``, поэтому модуль тестируется без моков сети/ФС.
"""

from __future__ import annotations

import ast
import re
from html.parser import HTMLParser

__all__ = [
    "extract_tests_from_html",
    "extract_external_test_links",
    "is_function_style",
]

# Ссылки на внешние тесты в HTML тексте задачи (issue #302 — перенесены сюда
# вместе с extract_external_test_links; _GITHUB_TREE_RE/_GITHUB_CONTENTS_API
# остаются в downloader.py, т.к. используются на этапе скачивания, не парсинга).
_ZIP_URL_RE = re.compile(r'href=["\']([^"\']*\.zip)["\']', re.IGNORECASE)
_GITHUB_URL_RE = re.compile(r'href=["\']([^"\']*github\.com[^"\']*)["\']', re.IGNORECASE)


class _TableParser(HTMLParser):
    """Вытаскивает текст из <td> ячеек HTML-таблицы построчно."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_th: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag == "td":
            self._current_cell = []
        elif tag == "th":
            self._in_th = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._current_cell is not None:
            cell_text = "".join(self._current_cell).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell = None
        elif tag == "th":
            self._in_th = False
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None and not self._in_th:
            self._current_cell.append(data)

    @property
    def rows(self) -> list[list[str]]:
        return self._rows


def is_function_style(input_text: str) -> bool:
    """Возвращает True если входные данные — объявление переменных (function-mode).

    Использует AST-анализ вместо regex-эвристики:
      - парсит input_text через ast.parse
      - если на верхнем уровне есть вызов функции (print, input и т.п.) — это stdin-режим
      - если есть хотя бы одно присваивание и нет вызовов на верхнем уровне — function-mode

    Примеры function-mode (→ True):
        date1 = date(2021, 11, 1)
        date2 = date(2021, 11, 22)

    Примеры stdin-mode (→ False):
        date1 = date(2021, 11, 1)
        print(my_func(date1))          ← вызов на верхнем уровне

        n = int(input())               ← вызов input()
    """
    stripped = input_text.strip()
    if not stripped:
        return False
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        # Если не парсится — не можем определить режим, считаем stdin
        return False

    has_assignment = False
    for node in tree.body:
        # Вызов функции на верхнем уровне (print, input, my_func(...)) → stdin-режим
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return False
        if isinstance(node, ast.Assign | ast.AnnAssign):
            has_assignment = True

    return has_assignment


def extract_tests_from_html(html: str) -> list[tuple[str, str, str]]:
    """Парсит HTML-таблицу тест-кейсов Stepik.

    Возвращает список троек (input_data, expected_output, test_type).
    test_type: "stdin" | "function".
    Пустой список если таблица не найдена или в ней < 3 колонок.
    """
    parser = _TableParser()
    parser.feed(html)
    tests: list[tuple[str, str, str]] = []
    for row in parser.rows:
        if len(row) < 3:  # noqa: PLR2004
            continue
        input_data = row[1].strip()
        expected = row[2].strip()
        if not input_data or not expected:
            continue
        test_type = "function" if is_function_style(input_data) else "stdin"
        tests.append((input_data, expected, test_type))
    return tests


def extract_external_test_links(html: str) -> tuple[list[str], list[str]]:
    """Извлекает ZIP- и GitHub-ссылки из HTML текста задачи.

    Возвращает кортеж (zip_links, github_links) без дубликатов.
    """

    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    zip_links = _unique(_ZIP_URL_RE.findall(html))
    github_links = _unique(_GITHUB_URL_RE.findall(html))
    return zip_links, github_links
