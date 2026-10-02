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
    "extract_attachment_links",
    "extract_external_test_links",
    "extract_tests_from_html",
    "is_function_style",
]

# Ссылки на внешние тесты в HTML тексте задачи (issue #302 — перенесены сюда
# вместе с extract_external_test_links; _GITHUB_TREE_RE/_GITHUB_CONTENTS_API
# остаются в downloader.py, т.к. используются на этапе скачивания, не парсинга).
_ZIP_URL_RE = re.compile(r'href=["\']([^"\']*\.zip)["\']', re.IGNORECASE)
_GITHUB_URL_RE = re.compile(r'href=["\']([^"\']*github\.com[^"\']*)["\']', re.IGNORECASE)

# issue #1112: вложения условия — файлы, которые решение ОТКРЫВАЕТ по имени
# (`open('files.txt')`). Живут на `stepik.org/media/attachments/...` и до сих
# пор не скачивались вовсе: принятое платформой решение падало локально
# `FileNotFoundError`, и подсказка глоссария добросовестно объясняла студенту
# его несуществующую ошибку.
_ATTACHMENT_URL_RE = re.compile(
    r'href=["\']([^"\']*?/media/attachments/[^"\']+)["\']', re.IGNORECASE
)


# Теги, чьё содержимое — не текст кейса, даже если лежит внутри <td> (issue
# #838): HTMLParser отдаёт тело <script>/<style> обычным handle_data, и текст
# скрипта уезжал в ожидаемый вывод теста, ломая кейс.
_NON_TEXT_TAGS = frozenset({"script", "style"})

# issue #941: блочные теги внутри ячейки означают перевод строки в данных.
# Список намеренно короткий — только то, что реально встречается в вёрстке
# примеров Stepik; лишние теги дали бы ложные переводы строк.
_BLOCK_TAGS = frozenset({"p", "div", "li", "tr"})


def _collapse_breaks(text: str) -> str:
    """Схлопнуть повторные переводы строк и убрать края (issue #941).

    `<p>10</p><p>20</p>` даёт перевод после каждого абзаца, включая последний,
    поэтому без схлопывания ожидание обрастало бы пустыми строками — а они в
    сравнении значимы и дали бы тот же ложный WA, от которого уходим.
    """
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


class _TableParser(HTMLParser):
    """Вытаскивает текст из <td> ячеек HTML-таблицы построчно."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_th: bool = False
        self._non_text_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag == "td":
            self._current_cell = []
        elif tag == "th":
            self._in_th = True
        elif tag in _NON_TEXT_TAGS:
            self._non_text_depth += 1
        elif tag == "br":
            # issue #941: перевод строки в вёрстке — это перевод строки в
            # данных. `<td>7<br>8</td>` давало «78», и верное решение,
            # печатающее две строки, стабильно получало WA — при том что
            # дефект в скачанных данных, а не в коде студента.
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        # issue #941: закрытие блочного тега разделяет строки так же, как <br>.
        # Типовая вёрстка Stepik использует и то, и другое вперемешку.
        if tag in _BLOCK_TAGS:
            self._append_break()
        if tag == "td" and self._current_cell is not None:
            cell_text = _collapse_breaks("".join(self._current_cell))
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell = None
        elif tag == "th":
            self._in_th = False
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._rows.append(self._current_row)
            self._current_row = None
        elif tag in _NON_TEXT_TAGS:
            self._non_text_depth = max(0, self._non_text_depth - 1)

    def _append_break(self) -> None:
        """Записать перевод строки в текущую ячейку (issue #941)."""
        if self._current_cell is not None and not self._in_th and not self._non_text_depth:
            self._current_cell.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Самозакрывающийся тег: `<br/>` встречается наравне с `<br>` (issue #941).

        Без этого метода HTMLParser зовёт для `<br/>` только `handle_starttag`
        в одних случаях и `handle_startendtag` в других — поведение зависит от
        разметки, и половина ячеек теряла бы переводы строк.
        """
        if tag == "br":
            self._append_break()
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None and not self._in_th and not self._non_text_depth:
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
    except (SyntaxError, ValueError):
        # Не парсится — режим не определить, считаем stdin. ValueError: ast.parse
        # документированно бросает его на исходнике с null-байтами (issue #691;
        # версинно-зависимо — часть CPython отдаёт SyntaxError). Вход — ячейка
        # HTML-таблицы задачи Stepik, т.е. подконтролен странице (fuzz-вектор).
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
        if len(row) < 3:
            continue
        input_data = row[1].strip()
        expected = row[2].strip()
        if not input_data or not expected:
            continue
        test_type = "function" if is_function_style(input_data) else "stdin"
        tests.append((input_data, expected, test_type))
    return tests


def _is_fetchable(url: str) -> bool:
    """Ссылку можно скачать по сети: ``http(s)`` или относительный путь (#838).

    HTML страницы задачи — недоверенный вход, а найденные здесь ссылки уходят
    прямо в загрузчик. ``file:///etc/passwd.zip`` и ``javascript:...zip``
    проходили как обычные «ZIP-ссылки»: схему никто не проверял, хотя ни одна
    из них не является тем, ради чего механизм существует (внешние тесты
    курса). Относительные ссылки остаются — загрузчик достраивает их до
    абсолютных сам.
    """
    scheme, sep, _ = url.partition(":")
    if not sep:
        return True  # относительный путь — схемы нет вовсе
    return scheme.lower() in {"http", "https"}


def extract_external_test_links(html: str) -> tuple[list[str], list[str]]:
    """Извлекает ZIP- и GitHub-ссылки из HTML текста задачи.

    Возвращает кортеж (zip_links, github_links) без дубликатов. Ссылки с
    неподходящей схемой (``file:``, ``javascript:``, ``data:``) отбрасываются —
    HTML приходит из сети, а результат уходит в загрузчик (issue #838).
    """

    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item in seen or not _is_fetchable(item):
                continue
            seen.add(item)
            result.append(item)
        return result

    zip_links = _unique(_ZIP_URL_RE.findall(html))
    github_links = _unique(_GITHUB_URL_RE.findall(html))
    return zip_links, github_links


def extract_attachment_links(html: str) -> list[str]:
    """Ссылки на вложения условия — файлы, которые открывает само решение (#1112).

    Это `stepik.org/media/attachments/...`: `files.txt`, `data.csv` и прочее,
    на что условие ссылается словами «вам доступен файл». Без них принятое
    платформой решение падает локально ``FileNotFoundError``.

    ``.zip`` отсюда исключён намеренно: архивы разбирает путь внешних тестов
    (:func:`extract_external_test_links`), и скачивать их вторым способом
    значило бы класть рядом с задачей сырой архив вместо тест-кейсов.

    Схема проверяется тем же ``_is_fetchable``, что и у тестовых ссылок: HTML
    приходит из сети, а результат уходит в загрузчик (issue #838).
    """
    seen: set[str] = set()
    links: list[str] = []
    for url in _ATTACHMENT_URL_RE.findall(html):
        if url in seen or not _is_fetchable(url) or url.lower().endswith(".zip"):
            continue
        seen.add(url)
        links.append(url)
    return links
