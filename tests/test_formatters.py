"""Тесты форматирующих функций grader.py.

Отдельный файл согласно issue #15, пункт 4.
Покрывает format_correctness_row, format_benchmark_row,
print_correctness_header, print_benchmark_header,
print_correctness_results, print_benchmark_results.
"""

from __future__ import annotations

import grader
from grader import (
    format_benchmark_row,
    format_correctness_row,
    print_benchmark_header,
    print_benchmark_results,
    print_correctness_header,
    print_correctness_results,
)

# ---------------------------------------------------------------------------
# Fixtures-like helpers
# ---------------------------------------------------------------------------


def _ok_result() -> dict:
    return {
        "total": 3,
        "passed": 3,
        "failed": 0,
        "errors": 0,
        "total_time": 0.5,
        "avg_time": 0.166,
        "peak_memory_mb": 12.5,
        "first_fail": None,
    }


def _fail_result() -> dict:
    return {
        "total": 3,
        "passed": 1,
        "failed": 2,
        "errors": 0,
        "total_time": 0.3,
        "avg_time": 0.1,
        "peak_memory_mb": 8.0,
        "first_fail": 2,
    }


def _bench_data() -> dict:
    return {
        "runs": 10,
        "min": 0.001,
        "median": 0.002,
        "mean": 0.0022,
        "max": 0.005,
        "stdev": 0.0003,
        "peak_memory_mb": 12.3,
        "relative": 1.0,
        "verdict": "SIMILAR",
    }


# ---------------------------------------------------------------------------
# format_correctness_row
# ---------------------------------------------------------------------------


class TestFormatCorrectnessRow:
    def test_ok_status(self) -> None:
        row = format_correctness_row("/dir/task1.py", "/dir", _ok_result(), col_file=20)
        assert "OK" in row
        assert "task1.py" in row

    def test_fail_status(self) -> None:
        row = format_correctness_row("/dir/task1.py", "/dir", _fail_result(), col_file=20)
        assert "FAIL" in row
        assert "task1.py" in row

    def test_passed_fraction(self) -> None:
        """Строка содержит дробь прошедших/всего тестов."""
        row = format_correctness_row("/dir/task1.py", "/dir", _ok_result(), col_file=20)
        assert "3" in row  # 3/3 или 3 passed

    def test_col_file_truncation(self) -> None:
        """Очень короткий col_file не вызывает исключений."""
        row = format_correctness_row("/dir/task1.py", "/dir", _ok_result(), col_file=5)
        assert isinstance(row, str)


# ---------------------------------------------------------------------------
# format_benchmark_row
# ---------------------------------------------------------------------------


class TestFormatBenchmarkRow:
    def test_contains_verdict(self) -> None:
        row = format_benchmark_row("/dir/task1.py", "/dir", _bench_data(), col_file=20)
        assert "SIMILAR" in row

    def test_contains_filename(self) -> None:
        row = format_benchmark_row("/dir/task1.py", "/dir", _bench_data(), col_file=20)
        assert "task1.py" in row

    def test_returns_string(self) -> None:
        row = format_benchmark_row("/dir/task1.py", "/dir", _bench_data(), col_file=20)
        assert isinstance(row, str) and len(row) > 0


# ---------------------------------------------------------------------------
# print_correctness_header / print_benchmark_header
# ---------------------------------------------------------------------------


class TestPrintHeaders:
    def test_correctness_header_columns(self, capsys) -> None:
        print_correctness_header(col_file=25)
        out = capsys.readouterr().out
        assert "File" in out
        assert "Status" in out

    def test_benchmark_header_columns(self, capsys) -> None:
        print_benchmark_header(col_file=25)
        out = capsys.readouterr().out
        assert "File" in out
        assert "Median" in out


# ---------------------------------------------------------------------------
# print_correctness_results / print_benchmark_results  (plain-text режим)
# ---------------------------------------------------------------------------


class TestPrintResults:
    def test_correctness_results_ok(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(grader, "_RICH", False)
        rows = [("/dir/task1.py", _ok_result())]
        print_correctness_results(rows, "/dir", col_file=20)
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "OK" in out

    def test_correctness_results_fail(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(grader, "_RICH", False)
        rows = [("/dir/task1.py", _fail_result())]
        print_correctness_results(rows, "/dir", col_file=20)
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_benchmark_results(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(grader, "_RICH", False)
        rows = [("/dir/task1.py", _bench_data())]
        print_benchmark_results(rows, "/dir", col_file=20)
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "SIMILAR" in out

    def test_empty_rows(self, capsys, monkeypatch) -> None:
        """Пустой список строк не вызывает исключений."""
        monkeypatch.setattr(grader, "_RICH", False)
        print_correctness_results([], "/dir", col_file=20)
        print_benchmark_results([], "/dir", col_file=20)
