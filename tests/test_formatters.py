"""Тесты форматирующих функций grader.py.

Отдельный файл согласно issue #15, пункт 4.
Покрывает format_correctness_row, format_benchmark_row,
print_correctness_header, print_benchmark_header,
print_correctness_results, print_benchmark_results.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.core import reporter
from stepik_grader.grader import (
    fmt_time,
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


def _no_tests_result() -> dict:
    return {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "total_time": 0.0,
        "avg_time": 0.0,
        "peak_memory_mb": 0.0,
        "first_fail": None,
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
# fmt_time — adaptive units for benchmark time columns (Issue #24)
# ---------------------------------------------------------------------------


class TestFmtTime:
    def test_seconds(self) -> None:
        assert fmt_time(1.5) == "1.500 s"

    def test_seconds_boundary(self) -> None:
        assert fmt_time(1.0) == "1.000 s"

    def test_milliseconds(self) -> None:
        assert fmt_time(0.15) == "150.000 ms"

    def test_milliseconds_boundary(self) -> None:
        assert fmt_time(1e-3) == "1.000 ms"

    def test_microseconds(self) -> None:
        assert fmt_time(0.00015) == "150.000 µs"

    def test_microseconds_boundary(self) -> None:
        assert fmt_time(1e-6) == "1.000 µs"

    def test_nanoseconds(self) -> None:
        """Values below 1us no longer collapse to '0.0000' as with fixed :.4f."""
        assert fmt_time(1.5e-7) == "150.000 ns"

    def test_zero(self) -> None:
        assert fmt_time(0.0) == "0.000 ns"


# ---------------------------------------------------------------------------
# format_correctness_row
# ---------------------------------------------------------------------------


class TestFormatCorrectnessRow:
    def test_ok_status(self) -> None:
        row = format_correctness_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _ok_result(), col_file=20
        )
        assert "OK" in row
        assert "task1.py" in row

    def test_fail_status(self) -> None:
        row = format_correctness_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _fail_result(), col_file=20
        )
        assert "FAIL" in row
        assert "task1.py" in row

    def test_no_tests_status(self) -> None:
        """total=0 (тесты не найдены/пустая tests/) — "NO TESTS", не "FAIL" (issue #299)."""
        row = format_correctness_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _no_tests_result(), col_file=20
        )
        assert "NO TESTS" in row
        assert "FAIL" not in row

    def test_passed_fraction(self) -> None:
        """Строка содержит дробь прошедших/всего тестов."""
        row = format_correctness_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _ok_result(), col_file=20
        )
        assert "3" in row  # 3/3 или 3 passed

    def test_col_file_truncation(self) -> None:
        """Очень короткий col_file не вызывает исключений."""
        row = format_correctness_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _ok_result(), col_file=5
        )
        assert isinstance(row, str)


# ---------------------------------------------------------------------------
# format_benchmark_row
# ---------------------------------------------------------------------------


class TestFormatBenchmarkRow:
    def test_contains_verdict(self) -> None:
        row = format_benchmark_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _bench_data(), col_file=20
        )
        assert "SIMILAR" in row

    def test_contains_filename(self) -> None:
        row = format_benchmark_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _bench_data(), col_file=20
        )
        assert "task1.py" in row

    def test_returns_string(self) -> None:
        row = format_benchmark_row(
            pathlib.Path("/dir/task1.py"), pathlib.Path("/dir"), _bench_data(), col_file=20
        )
        assert isinstance(row, str) and len(row) > 0


# ---------------------------------------------------------------------------
# issue #440: относительный путь против абсолютной базы не роняет вывод
# ---------------------------------------------------------------------------


class TestRelativePathDifferentAnchor:
    """`Path.relative_to(base, walk_up=True)` кидает ValueError при разных
    anchor'ах (относительный ввод `task.py` против абсолютной базы). Режимы
    1/2/3/4 и запись истории должны отдавать путь как есть, а не падать
    трейсбеком (issue #440)."""

    def test_safe_rel_falls_back_on_different_anchor(self) -> None:
        assert reporter._safe_rel(pathlib.Path("task.py"), pathlib.Path("/abs/dir")) == "task.py"

    def test_safe_rel_normal_relative(self) -> None:
        rel = reporter._safe_rel(pathlib.Path("/abs/dir/task.py"), pathlib.Path("/abs/dir"))
        assert rel == "task.py"

    def test_correctness_row_relative_path_no_crash(self) -> None:
        row = format_correctness_row(
            pathlib.Path("task.py"), pathlib.Path("/abs/dir"), _ok_result(), col_file=20
        )
        assert "task.py" in row

    def test_benchmark_row_relative_path_no_crash(self) -> None:
        row = format_benchmark_row(
            pathlib.Path("task.py"), pathlib.Path("/abs/dir"), _bench_data(), col_file=20
        )
        assert "task.py" in row

    def test_commands_rel_falls_back_on_different_anchor(self) -> None:
        """task_key под --history: относительный dir против абсолютного cwd."""
        from stepik_grader.cli.commands import _rel

        assert _rel(pathlib.Path("."), pathlib.Path("/abs/cwd")) == "."


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
        assert "Memory" in out  # default RSS label (mode 3)

    def test_benchmark_header_custom_memory_label(self, capsys) -> None:
        """issue #66: режим 4 передаёт memory_header='Py-heap' (tracemalloc)."""
        print_benchmark_header(col_file=25, memory_header="Py-heap")
        out = capsys.readouterr().out
        assert "Py-heap" in out
        assert "Memory" not in out


# ---------------------------------------------------------------------------
# print_correctness_results / print_benchmark_results  (plain-text режим)
# ---------------------------------------------------------------------------


class TestPrintResults:
    def test_correctness_results_ok(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [(pathlib.Path("/dir/task1.py"), _ok_result())]
        print_correctness_results(rows, pathlib.Path("/dir"), col_file=20)
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "OK" in out

    def test_correctness_results_fail(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [(pathlib.Path("/dir/task1.py"), _fail_result())]
        print_correctness_results(rows, pathlib.Path("/dir"), col_file=20)
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_correctness_results_no_tests(self, capsys, monkeypatch) -> None:
        """total=0 в plain-text таблице выводится как "NO TESTS" (issue #299)."""
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [(pathlib.Path("/dir/task1.py"), _no_tests_result())]
        print_correctness_results(rows, pathlib.Path("/dir"), col_file=20)
        out = capsys.readouterr().out
        assert "NO TESTS" in out

    def test_benchmark_results(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(reporter, "_RICH", False)
        rows = [(pathlib.Path("/dir/task1.py"), _bench_data())]
        print_benchmark_results(rows, pathlib.Path("/dir"), col_file=20)
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "SIMILAR" in out

    def test_empty_rows(self, capsys, monkeypatch) -> None:
        """Пустой список строк не вызывает исключений."""
        monkeypatch.setattr(reporter, "_RICH", False)
        print_correctness_results([], pathlib.Path("/dir"), col_file=20)
        print_benchmark_results([], pathlib.Path("/dir"), col_file=20)


# ---------------------------------------------------------------------------
# issue #836 (QA-05) — fallback-вывод трёх сводных printer'ов без rich.
# rich — runtime-зависимость и в тестовом окружении стоит всегда, поэтому
# `_RICH` там вечно True: опечатка в этих ветках вылезала бы только у
# пользователя, поставившего пакет без rich (инвариант №3 CLAUDE.md).
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    from stepik_grader.core import reporter

    monkeypatch.setattr(reporter, "_RICH", False)
    monkeypatch.setattr(reporter, "_console", None)


def test_stats_summary_without_rich(_no_rich, capsys) -> None:
    """Сводка статистики печатается plain-текстом, без падения на форматировании."""
    from stepik_grader.core.reporter import print_stats_summary

    print_stats_summary(
        {
            "total_runs": 7,
            "by_mode": {"1": 5, "2": 2},
            "by_os": {"Linux": 7},
            "verdict_totals": {"AC": 6, "WA": 1},
            "total_time": 1.5,
        }
    )
    out = capsys.readouterr().out
    assert "Total runs" in out and "7" in out
    assert "Verdict AC" in out and "6" in out
    assert "OS: Linux" in out


def test_insights_summary_without_rich(_no_rich, capsys) -> None:
    """Карточки «Подучить» печатаются plain-текстом со всеми колонками."""
    from stepik_grader.core.insights import InsightCard
    from stepik_grader.core.reporter import print_insights_summary

    card = InsightCard(
        key="timeout", category="failure", status="active", hits=3, runs_considered=10
    )
    print_insights_summary([card])
    out = capsys.readouterr().out
    assert "timeout" in out
    assert "3" in out


def test_progress_summary_without_rich(_no_rich, capsys) -> None:
    """Сводка «Прогресс» печатается plain-текстом, включая задачу и попытки."""
    from stepik_grader.core.insights import TaskProgress
    from stepik_grader.core.reporter import print_progress_summary

    print_progress_summary(
        [
            TaskProgress(
                task_key="04-slug",
                attempts=2,
                solved=True,
                total_runs=2,
                seconds_to_first_ac=42.0,
            )
        ]
    )
    out = capsys.readouterr().out
    assert "04-slug" in out
    assert "2" in out
