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
