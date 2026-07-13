"""Дополнительные тесты для grader.py — форматирование и вывод таблиц.

Покрывают чистые функции форматирования строк и plain-text ветки печати
(rich отключается через monkeypatch grader._RICH=False). Реальный subprocess,
сеть и rich-консоль не задействуются.
"""

from __future__ import annotations

import pathlib

from stepik_grader.core import reporter
from stepik_grader.grader import (
    TestCase,
    _correctness_status,
    format_benchmark_row,
    format_correctness_row,
    load_text_lines,
    print_benchmark_header,
    print_benchmark_results,
    print_case_verbose,
    print_correctness_header,
    print_correctness_results,
)


def _correct_result(passed=2, total=2, failed=0, errors=0):
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_time": 0.1234,
        "avg_time": 0.0617,
        "peak_memory_mb": 12.5,
        "first_fail": None,
        "error": "",
    }


def _bench_data():
    return {
        "runs": 5,
        "min": 0.1,
        "median": 0.2,
        "mean": 0.2,
        "max": 0.3,
        "stdev": 0.05,
        "peak_memory_mb": 10.0,
        "relative": 1.0,
        "verdict": "SIMILAR",
    }


class TestCorrectnessStatus:
    """_correctness_status различает OK, FAIL и NO TESTS."""

    def test_ok(self):
        assert _correctness_status(_correct_result()) == "OK"

    def test_fail_on_failure(self):
        assert _correctness_status(_correct_result(passed=1, failed=1)) == "FAIL"

    def test_no_tests_on_zero_total(self):
        """total=0 (тесты не найдены/пустая tests/) — не провал решения (issue #299)."""
        assert _correctness_status(_correct_result(passed=0, total=0)) == "NO TESTS"


class TestFormatRows:
    """format_correctness_row / format_benchmark_row дают непустые строки."""

    def test_correctness_row_ok(self):
        row = format_correctness_row("/base/sol.py", "/base", _correct_result(), col_file=20)
        assert "sol.py" in row
        assert "OK" in row

    def test_correctness_row_fail(self):
        row = format_correctness_row(
            "/base/sol.py", "/base", _correct_result(passed=1, failed=1), col_file=20
        )
        assert "FAIL" in row

    def test_benchmark_row(self):
        row = format_benchmark_row("/base/sol.py", "/base", _bench_data(), col_file=20)
        assert "sol.py" in row
        assert "SIMILAR" in row


class TestPrintHeadersPlain:
    """Заголовки таблиц печатаются (plain-text)."""

    def test_correctness_header(self, capsys):
        print_correctness_header(col_file=20)
        out = capsys.readouterr().out
        assert "File" in out and "Status" in out

    def test_benchmark_header(self, capsys):
        print_benchmark_header(col_file=20)
        out = capsys.readouterr().out
        assert "Verdict" in out


class TestPrintResultsPlain:
    """print_*_results в plain-режиме (rich отключён) печатают таблицу."""

    def test_correctness_plain(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [("/base/sol.py", _correct_result())]
        print_correctness_results(rows, "/base", col_file=20)
        out = capsys.readouterr().out
        assert "sol.py" in out
        assert "OK" in out

    def test_benchmark_plain(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [("/base/sol.py", _bench_data())]
        print_benchmark_results(rows, "/base", col_file=20)
        out = capsys.readouterr().out
        assert "sol.py" in out


class TestPrintCaseVerbose:
    """print_case_verbose печатает вердикт и diff (plain-режим)."""

    def test_passed_case(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["5"], expected_lines=["5"])
        print_case_verbose(case, {"passed": True, "error": ""})
        out = capsys.readouterr().out
        assert "Test 1" in out

    def test_error_case(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=2, input_lines=["x"], expected_lines=["y"])
        print_case_verbose(case, {"passed": False, "error": "boom"})
        out = capsys.readouterr().out
        assert "ERROR" in out

    def test_wa_case_with_diff(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=3, input_lines=["1"], expected_lines=["2"])
        r = {
            "passed": False,
            "error": "",
            "expected": ["2"],
            "output": ["3"],
            "diff": "-2\n+3\n unchanged",
        }
        print_case_verbose(case, r)
        out = capsys.readouterr().out
        assert "Expected" in out
        assert "Actual" in out
        assert "Diff" in out


class TestLoadTextLines:
    """load_text_lines читает файл построчно без переносов."""

    def test_reads_lines(self, tmp_path: pathlib.Path):
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        assert load_text_lines(f) == ["a", "b", "c"]
