"""Дополнительные тесты для grader.py — форматирование и вывод таблиц.

Покрывают чистые функции форматирования строк и plain-text ветки печати
(rich отключается через monkeypatch grader._RICH=False). Реальный subprocess,
сеть и rich-консоль не задействуются.
"""

from __future__ import annotations

import pathlib

import pytest

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
        row = format_correctness_row(
            pathlib.Path("/base/sol.py"), pathlib.Path("/base"), _correct_result(), col_file=20
        )
        assert "sol.py" in row
        assert "OK" in row

    def test_correctness_row_fail(self):
        row = format_correctness_row(
            pathlib.Path("/base/sol.py"),
            pathlib.Path("/base"),
            _correct_result(passed=1, failed=1),
            col_file=20,
        )
        assert "FAIL" in row

    def test_benchmark_row(self):
        row = format_benchmark_row(
            pathlib.Path("/base/sol.py"), pathlib.Path("/base"), _bench_data(), col_file=20
        )
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
        rows = [(pathlib.Path("/base/sol.py"), _correct_result())]
        print_correctness_results(rows, pathlib.Path("/base"), col_file=20)
        out = capsys.readouterr().out
        assert "sol.py" in out
        assert "OK" in out

    def test_benchmark_plain(self, capsys, monkeypatch):
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [(pathlib.Path("/base/sol.py"), _bench_data())]
        print_benchmark_results(rows, pathlib.Path("/base"), col_file=20)
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


# ---------------------------------------------------------------------------
# issue #824 — отчёт о провале: вход кейса, обрезка простыней, выравнивание
# plain-таблиц, локализованные подписи. Всё это раньше отсутствовало: вход не
# печатался ни разу, Expected/Actual/diff не имели потолка (единственным
# предохранителем был CONFIG.max_output_bytes = 10 МиБ), а колонка памяти в
# benchmark-таблице была на символ шире своей шапки.
# ---------------------------------------------------------------------------


class TestFailureReportInput:
    """DESC-01: вход кейса виден рядом с ожидаемым и полученным."""

    @staticmethod
    def _wa(case: TestCase, capsys, monkeypatch, **over: object) -> str:
        monkeypatch.setattr(reporter, "_RICH", False)
        payload: dict[str, object] = {
            "passed": False,
            "error": "",
            "expected": ["2"],
            "output": ["3"],
            "diff": "",
        }
        payload.update(over)
        print_case_verbose(case, payload)  # type: ignore[arg-type]
        return capsys.readouterr().out

    def test_stdin_is_printed_for_failed_case(self, capsys, monkeypatch) -> None:
        """Первый вопрос после «✗ Test 7: WA» — на каких данных сломалось."""
        case = TestCase(index=7, input_lines=["4", "8 15"], expected_lines=["2"])
        out = self._wa(case, capsys, monkeypatch)
        assert "Input:" in out
        assert "4 | 8 15" in out

    def test_empty_stdin_is_labelled_not_blank(self, capsys, monkeypatch) -> None:
        """Кейс без ввода — это факт, а не пропущенная строка отчёта."""
        case = TestCase(index=1, input_lines=[], expected_lines=["2"])
        out = self._wa(case, capsys, monkeypatch)
        assert "Input:" in out
        assert "(empty)" in out

    def test_passed_case_stays_terse(self, capsys, monkeypatch) -> None:
        """У зачтённого кейса вход не печатается — отлаживать нечего."""
        case = TestCase(index=1, input_lines=["42"], expected_lines=["42"])
        monkeypatch.setattr(reporter, "_RICH", False)
        print_case_verbose(case, {"passed": True, "error": ""})
        assert "Input:" not in capsys.readouterr().out


class TestFailureReportClipping:
    """DESC-02: одна «болтливая» ошибка не вымывает вердикты остальных кейсов."""

    def test_long_expected_and_actual_are_clipped_with_volume(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["x"], expected_lines=["y"])
        payload = {
            "passed": False,
            "error": "",
            "expected": ["E" * 5000],
            "output": ["A" * 5000],
            "diff": "",
        }
        print_case_verbose(case, payload)  # type: ignore[arg-type]
        out = capsys.readouterr().out

        assert "E" * 5000 not in out
        assert "A" * 5000 not in out
        # Объём отброшенного назван: «обрезано» без числа не отличает потерю
        # одного символа от потери ста тысяч.
        dropped = 5000 - reporter._VERBOSE_MAX_VALUE_CHARS
        assert out.count(f"ещё {dropped} симв.") == 2

    def test_long_diff_is_clipped_with_line_count(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["x"], expected_lines=["y"])
        payload = {
            "passed": False,
            "error": "",
            "expected": ["y"],
            "output": ["z"],
            "diff": "\n".join(f"-line{i}" for i in range(500)),
        }
        print_case_verbose(case, payload)  # type: ignore[arg-type]
        out = capsys.readouterr().out

        printed = sum(1 for line in out.splitlines() if line.strip().startswith("-line"))
        assert printed == reporter._VERBOSE_MAX_DIFF_LINES
        assert f"ещё {500 - reporter._VERBOSE_MAX_DIFF_LINES} стр." in out

    def test_long_stdin_is_clipped_too(self, capsys, monkeypatch) -> None:
        """Вход обрезается наравне с остальным: он тоже приходит от решения."""
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["I" * 5000], expected_lines=["y"])
        payload = {"passed": False, "error": "", "expected": ["y"], "output": ["z"], "diff": ""}
        print_case_verbose(case, payload)  # type: ignore[arg-type]
        assert "I" * 5000 not in capsys.readouterr().out

    def test_short_values_are_untouched(self, capsys, monkeypatch) -> None:
        """Обычный кейс не должен обрасти многоточиями — регресс на границе."""
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["4"], expected_lines=["2"])
        payload = {
            "passed": False,
            "error": "",
            "expected": ["2"],
            "output": ["3"],
            "diff": "-2\n+3",
        }
        print_case_verbose(case, payload)  # type: ignore[arg-type]
        out = capsys.readouterr().out
        assert "ещё" not in out


class TestPlainTableAlignment:
    """DESC-07: без rich колонки не разъезжаются — это то, что видно в CI-логах."""

    @staticmethod
    def _edges(header: str, row: str, pairs: list[tuple[str, str]]) -> list[tuple[str, int, int]]:
        return [
            (label, header.index(label) + len(label), row.rindex(value) + len(value))
            for label, value in pairs
        ]

    def test_benchmark_header_matches_row_widths(self, capsys) -> None:
        print_benchmark_header(col_file=20)
        header = capsys.readouterr().out.splitlines()[1]
        row = format_benchmark_row(
            pathlib.Path("sol.py"),
            pathlib.Path("."),
            _bench_data(),
            col_file=20,
        )
        for label, head_end, value_end in self._edges(
            header,
            row,
            [("Std dev", "50.000 ms"), ("Memory", "10.00 MB"), ("Relative", "100.0%")],
        ):
            assert head_end == value_end, f"колонка {label!r} съехала на {value_end - head_end}"

    def test_correctness_header_matches_row_widths(self, capsys) -> None:
        print_correctness_header(col_file=20)
        header = capsys.readouterr().out.splitlines()[1]
        row = format_correctness_row(
            pathlib.Path("sol.py"),
            pathlib.Path("."),
            {
                "total": 5,
                "passed": 5,
                "failed": 0,
                "errors": 0,
                "total_time": 1.0,
                "avg_time": 0.2,
                "peak_memory_mb": 12.34,
                "first_fail": "-",
            },
            col_file=20,
        )
        for label, head_end, value_end in self._edges(
            header, row, [("Memory, MB", "12.34 MB"), ("Status", "OK")]
        ):
            assert head_end == value_end, f"колонка {label!r} съехала на {value_end - head_end}"


# ---------------------------------------------------------------------------
# Экранирование управляющих символов (issue #981): отчёт не подчиняется решению
# ---------------------------------------------------------------------------


class TestEscapeControlChars:
    """Вывод проверяемого решения не должен управлять терминалом грейдера."""

    @pytest.mark.parametrize(
        ("raw", "expected", "why"),
        [
            ("обычный текст", "обычный текст", "печатный текст не трогаем"),
            ("эмодзи 🎉 кириллица", "эмодзи 🎉 кириллица", "не-ASCII — обычные данные"),
            ("таб\tмежду", "таб\tмежду", "табуляция значима в выводе"),
            ("две\nстроки", "две\nстроки", "перевод строки значим"),
            ("\x1b[2J", "\\x1b[2J", "очистка экрана обезврежена"),
            ("\x1b[32mAC\x1b[0m", "\\x1b[32mAC\\x1b[0m", "подкраска вердикта обезврежена"),
            ("хвост\rзатирание", "хвост\\x0dзатирание", "возврат каретки затирает строку"),
            ("звонок\a", "звонок\\x07", "BEL"),
            ("\x08backspace", "\\x08backspace", "забой"),
            ("\x7f", "\\x7f", "DEL"),
        ],
    )
    def test_escaping(self, raw: str, expected: str, why: str) -> None:
        """Опасное экранируется, обычные данные остаются как есть."""
        assert reporter.escape_control_chars(raw) == expected, why

    def test_verbose_output_has_no_raw_escape(self, capsys, monkeypatch) -> None:
        """В verbose-отчёте не остаётся сырых ESC (issue #981).

        Решение, печатающее `\\x1b[2J\\x1b[H`, стирало уже напечатанный отчёт и
        рисовало на его месте зелёный «AC», которого грейдер не выносил.
        """
        monkeypatch.setattr(reporter, "_RICH", False)
        case = TestCase(index=1, input_lines=["1"], expected_lines=["ok"])
        print_case_verbose(
            case,
            {
                "passed": False,
                "error": "",
                "output": ["\x1b[2J\x1b[H", "\x1b[32m  task1.py  1/1  OK\x1b[0m"],
                "expected": ["ok"],
                "diff": "-ok\n+\x1b[32mOK\x1b[0m",
            },
        )

        out = capsys.readouterr().out
        assert "\x1b" not in out, "сырая ESC-последовательность дошла до терминала"
        assert "\\x1b[2J" in out, "последовательность должна быть видна как текст"

    def test_clip_counts_escaped_length(self, monkeypatch) -> None:
        """Обрезка считает уже экранированную строку (issue #981).

        Иначе рез приходился на середину управляющей последовательности, и её
        незакрытый остаток съедал само уведомление об обрезке.
        """
        monkeypatch.setattr(reporter, "_VERBOSE_MAX_VALUE_CHARS", 10)

        clipped = reporter._clip_value("\x1b[2J" + "x" * 100)

        assert "\x1b" not in clipped
        assert clipped.startswith("\\x1b[2J")
        assert "… ещё" in clipped
