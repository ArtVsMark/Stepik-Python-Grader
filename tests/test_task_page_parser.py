"""Тесты для core/task_page_parser.py — разбор HTML текста задачи (issue #302).

Выделено из test_downloader_extra.py: extract_tests_from_html (таблица кейсов)
и is_function_style (stdin vs function по AST). extract_external_test_links
тестируется в test_test_source_fetcher.py рядом со скачиванием.
"""

from __future__ import annotations

from stepik_grader.core.task_page_parser import extract_tests_from_html, is_function_style


class TestExtractTestsFromHtml:
    """extract_tests_from_html парсит HTML-таблицу тест-кейсов."""

    def test_stdin_tests(self):
        html = (
            "<table>"
            "<tr><th>#</th><th>in</th><th>out</th></tr>"
            "<tr><td>1</td><td>print(1)</td><td>1</td></tr>"
            "</table>"
        )
        tests = extract_tests_from_html(html)
        assert len(tests) == 1
        assert tests[0][2] == "stdin"

    def test_function_style_detected(self):
        html = "<table><tr><td>1</td><td>x = 5</td><td>5</td></tr></table>"
        tests = extract_tests_from_html(html)
        assert tests[0][2] == "function"

    def test_short_rows_skipped(self):
        html = "<table><tr><td>only</td><td>two</td></tr></table>"
        assert extract_tests_from_html(html) == []

    def test_empty_cells_skipped(self):
        html = "<table><tr><td>1</td><td></td><td>out</td></tr></table>"
        assert extract_tests_from_html(html) == []


class TestIsFunctionStyle:
    """is_function_style: присваивания без вызовов на верхнем уровне → function."""

    def test_assignments_only_is_function(self):
        assert is_function_style("date1 = date(2021, 11, 1)\ndate2 = date(2021, 11, 22)") is True

    def test_toplevel_call_is_stdin(self):
        # Вызов на верхнем уровне (Expr-Call) → stdin, даже при наличии присваивания.
        assert is_function_style("x = 1\nprint(x)") is False
        assert is_function_style("print(my_func(date1))") is False

    def test_empty_is_stdin(self):
        assert is_function_style("   ") is False

    def test_syntax_error_is_stdin(self):
        assert is_function_style("=== not python ===") is False


# ---------------------------------------------------------------------------
# Переводы строк в ячейках таблицы (issue #941)
# ---------------------------------------------------------------------------


def _one_case(cell: str) -> str:
    """Expected одного кейса из таблицы, где вывод свёрстан как ``cell``."""
    html = (
        "<table><tr><th>N</th><th>Ввод</th><th>Вывод</th></tr>"
        f"<tr><td>1</td><td>x</td><td>{cell}</td></tr></table>"
    )
    tests = extract_tests_from_html(html)
    assert tests, "кейс не разобрался"
    return tests[0][1]


def test_br_becomes_newline() -> None:
    """`<br>` в ячейке — перевод строки в данных, а не склейка (issue #941).

    До фикса `7<br>8` давало «78», и верное решение, печатающее две строки,
    стабильно получало WA — при том что дефект в скачанных данных, а не в
    коде студента.
    """
    assert _one_case("7<br>8") == "7\n8"


def test_self_closing_br_becomes_newline() -> None:
    """`<br/>` обрабатывается наравне с `<br>` (issue #941)."""
    assert _one_case("5<br/>6") == "5\n6"


def test_paragraphs_become_lines() -> None:
    """Соседние `<p>` — это строки вывода (issue #941)."""
    assert _one_case("<p>10</p><p>20</p>") == "10\n20"


def test_divs_become_lines() -> None:
    """`<div>` тоже разделяет строки (issue #941)."""
    assert _one_case("<div>a</div><div>b</div>") == "a\nb"


def test_pre_keeps_real_newlines() -> None:
    """`<pre>` с настоящими переводами строк не ломается (guard к issue #941)."""
    assert _one_case("<pre>1\n2</pre>") == "1\n2"


def test_single_line_cell_unchanged() -> None:
    """Обычная однострочная ячейка остаётся однострочной (guard к issue #941)."""
    assert _one_case("одна строка") == "одна строка"


def test_no_trailing_blank_lines() -> None:
    """Повторные переводы не превращаются в пустые строки (issue #941).

    Пустая строка в ожидании значима при сравнении и дала бы тот же ложный WA,
    от которого уходим.
    """
    assert _one_case("<p>a</p><br><br><p>b</p>") == "a\nb"
