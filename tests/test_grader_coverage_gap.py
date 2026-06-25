"""Точечные тесты для закрытия gap 84.50% → 85%+.

Покрывает непокрытые строки grader.py:
  - print_correctness_results  plain-text ветка (304-325)
  - print_benchmark_results    plain-text ветка (341-363)
  - _interactive_menu выход    (1349-1436, строки exit-path)
  - run_single_test OSError    (901-917)
  - run_tests verbose          (797-803)
  - _print_case_verbose diff   (585, 598)
  - _cprint plain              (673-675)
  - format_correctness_row / print_correctness_header / print_benchmark_header (206-207, 304, 374)
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from grader import (
    TestCase,
    _cprint,
    _interactive_menu,
    _print_case_verbose,
    format_benchmark_row,
    format_correctness_row,
    print_benchmark_header,
    print_benchmark_results,
    print_correctness_header,
    print_correctness_results,
    run_single_test,
    run_tests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_CORRECTNESS_RESULT = {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "errors": 0,
    "total_time": 0.123,
    "avg_time": 0.041,
    "peak_memory_mb": 8.5,
    "first_fail": 2,
}

_DUMMY_BENCHMARK_DATA = {
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


def _make_rich_mocks():
    """Возвращает (mock_console, mock_table_cls, mock_text_cls)."""
    mock_console = MagicMock()
    mock_table_cls = MagicMock(return_value=MagicMock())
    mock_text_cls = MagicMock(return_value=MagicMock())
    return mock_console, mock_table_cls, mock_text_cls


# ---------------------------------------------------------------------------
# plain-text output functions (строки 206-207, 304-325, 341-363, 374)
# ---------------------------------------------------------------------------


class TestPlainTextOutput:
    """Покрывает plain-text ветки print_correctness_results и print_benchmark_results."""

    def test_print_correctness_results_plain(self, capsys) -> None:
        rows = [("base/task1.py", _DUMMY_CORRECTNESS_RESULT)]
        with patch("grader._RICH", False):
            print_correctness_results(rows, "base", col_file=20)
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "FAIL" in out

    def test_print_benchmark_results_plain(self, capsys) -> None:
        rows = [("base/task1.py", _DUMMY_BENCHMARK_DATA)]
        with patch("grader._RICH", False):
            print_benchmark_results(rows, "base", col_file=20, title="bench")
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "SIMILAR" in out

    def test_print_correctness_header(self, capsys) -> None:
        print_correctness_header(col_file=20)
        out = capsys.readouterr().out
        assert "File" in out
        assert "Status" in out

    def test_print_benchmark_header(self, capsys) -> None:
        print_benchmark_header(col_file=20)
        out = capsys.readouterr().out
        assert "File" in out
        assert "Median" in out

    def test_format_correctness_row_ok(self) -> None:
        row = format_correctness_row(
            "base/task1.py", "base", _DUMMY_CORRECTNESS_RESULT, col_file=15
        )
        assert "FAIL" in row
        assert "task1.py" in row

    def test_format_benchmark_row(self) -> None:
        row = format_benchmark_row(
            "base/task1.py", "base", _DUMMY_BENCHMARK_DATA, col_file=15
        )
        assert "SIMILAR" in row
        assert "task1.py" in row

    def test_print_correctness_results_rich(self) -> None:
        """rich-ветка: Console.print вызывается без исключений."""
        rows = [("base/task1.py", _DUMMY_CORRECTNESS_RESULT)]
        mock_console, mock_table_cls, mock_text_cls = _make_rich_mocks()
        with (
            patch("grader._RICH", True),
            patch("grader._console", mock_console),
            patch("grader.Table", mock_table_cls),
            patch("grader.Text", mock_text_cls),
        ):
            print_correctness_results(rows, "base", col_file=20)
        mock_console.print.assert_called_once()

    def test_print_benchmark_results_rich(self) -> None:
        """rich-ветка: Console.print вызывается без исключений."""
        rows = [("base/task1.py", _DUMMY_BENCHMARK_DATA)]
        mock_console, mock_table_cls, mock_text_cls = _make_rich_mocks()
        with (
            patch("grader._RICH", True),
            patch("grader._console", mock_console),
            patch("grader.Table", mock_table_cls),
            patch("grader.Text", mock_text_cls),
        ):
            print_benchmark_results(rows, "base", col_file=20)
        mock_console.print.assert_called_once()


# ---------------------------------------------------------------------------
# _cprint plain-text (строки 673-675)
# ---------------------------------------------------------------------------


class TestCprint:
    def test_cprint_plain_no_style(self, capsys) -> None:
        with patch("grader._RICH", False):
            _cprint("hello plain")
        assert "hello plain" in capsys.readouterr().out

    def test_cprint_plain_with_style_ignored(self, capsys) -> None:
        with patch("grader._RICH", False):
            _cprint("styled text", style="green")
        assert "styled text" in capsys.readouterr().out

    def test_cprint_rich_no_style_falls_to_print(self, capsys) -> None:
        """_cprint с пустым style даже при _RICH=True уходит в plain print."""
        with patch("grader._RICH", True):
            _cprint("no style")
        assert "no style" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _print_case_verbose — diff и error ветки (строки 585, 598)
# ---------------------------------------------------------------------------


class TestPrintCaseVerbose:
    def _make_case(self) -> TestCase:
        return TestCase(index=1, input_lines=["5"], expected_lines=["10"])

    def test_verbose_wa_with_diff(self, capsys) -> None:
        case = self._make_case()
        r = {
            "passed": False,
            "verdict": "WA",
            "error": "",
            "expected": ["10"],
            "output": ["9"],
            "diff": "-10\n+9",
        }
        _print_case_verbose(case, r)
        out = capsys.readouterr().out
        assert "WA" in out or "✗" in out

    def test_verbose_error(self, capsys) -> None:
        case = self._make_case()
        r = {
            "passed": False,
            "verdict": "RE",
            "error": "NameError: x",
            "expected": ["10"],
            "output": [],
            "diff": "",
        }
        _print_case_verbose(case, r)
        out = capsys.readouterr().out
        assert "NameError" in out

    def test_verbose_passed(self, capsys) -> None:
        case = self._make_case()
        r = {
            "passed": True,
            "verdict": "AC",
            "error": "",
            "expected": ["10"],
            "output": ["10"],
            "diff": "",
        }
        _print_case_verbose(case, r)
        out = capsys.readouterr().out
        assert "✓" in out or "AC" in out


# ---------------------------------------------------------------------------
# run_tests verbose=True (строки 797-803)
# ---------------------------------------------------------------------------


class TestRunTestsVerbose:
    def test_verbose_output(self, tmp_path: pathlib.Path, capsys) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "input_1.txt").write_text("4", encoding="utf-8")
        (tests_dir / "expected_1.txt").write_text("5", encoding="utf-8")

        result = run_tests(str(sol), str(tests_dir), verbose=True)
        out = capsys.readouterr().out
        assert result["passed"] == 1
        assert "1" in out


# ---------------------------------------------------------------------------
# run_single_test — OSError ветка (строки 901-917)
# ---------------------------------------------------------------------------


class TestRunSingleTestOsError:
    def test_oserror_returns_re(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)", encoding="utf-8")
        case = TestCase(index=1, input_lines=["1"], expected_lines=["1"])

        with patch("subprocess.Popen", side_effect=OSError("no such file")):
            result = run_single_test(str(sol), case)

        assert result["passed"] is False
        assert result["verdict"] == "RE"
        assert "no such file" in result["error"]


# ---------------------------------------------------------------------------
# _interactive_menu — выход (строки 1349+)
# ---------------------------------------------------------------------------


class TestInteractiveMenuExit:
    def test_choice_zero_exits(self, monkeypatch) -> None:
        """Выбор '0' → немедленный выход без ошибок."""
        monkeypatch.setattr("builtins.input", lambda *a: "0")
        _interactive_menu()

    def test_invalid_choice_then_exit(self, monkeypatch) -> None:
        """Неверный ввод ('9') → сообщение об ошибке, затем '0' → выход."""
        inputs = iter(["9", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _interactive_menu()

    def test_menu_mode1_no_tests(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Режим 1: файл без тестов → run_tests возвращает пустой результат → выход."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)", encoding="utf-8")
        empty_result = {
            "total": 0, "passed": 0, "failed": 0, "errors": 0,
            "total_time": 0.0, "avg_time": 0.0, "peak_memory_mb": 0.0,
            "first_fail": None, "cases": [],
        }
        monkeypatch.setattr("grader.run_tests", lambda *a, **k: empty_result)
        inputs = iter(["1", str(sol), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _interactive_menu()

    def test_menu_mode3_benchmark(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Режим 3 (benchmark): мок run_benchmark → выход."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)", encoding="utf-8")
        bench_result = {
            "runs": 5, "min": 0.001, "median": 0.002, "mean": 0.002,
            "max": 0.003, "stdev": 0.0, "peak_memory_mb": 0.0,
            "relative": 1.0, "verdict": "SIMILAR",
        }
        monkeypatch.setattr("grader.run_benchmark", lambda *a, **k: {str(sol): bench_result})
        inputs = iter(["3", str(sol), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _interactive_menu()
