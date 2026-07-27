"""Tests for web.py — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1)."""

from __future__ import annotations

import dataclasses
import http.client
import json
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.web import runs
from tests._wait import wait_until


def _make_task(tmp_path: pathlib.Path, body: str, *, with_tests: bool = True) -> pathlib.Path:
    """Создать task.py и (опционально) папку tests/ с одним кейсом 4 -> 5."""
    sol = tmp_path / "task.py"
    sol.write_text(body, encoding="utf-8")
    if with_tests:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "1").write_text("4", encoding="utf-8")
        (tests / "1.clue").write_text("5", encoding="utf-8")
    return sol


# ---------------------------------------------------------------------------
# grade_path
# ---------------------------------------------------------------------------


class TestGradePath:
    def test_passing_file(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_path(sol)
        assert data["kind"] == "file"
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["status"] == "OK"
        assert row["passed"] == row["total"] == 1
        assert row["cases"][0]["verdict"] == "AC"
        assert row["cases"][0]["diff"] == ""

    def test_failing_file_has_diff(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4 -> 6, ждём 5
        row = web.grade_path(sol)["rows"][0]
        assert row["status"] == "FAIL"
        assert row["cases"][0]["verdict"] == "WA"
        assert row["cases"][0]["diff"]  # непустой diff

    def test_directory(self, tmp_path: pathlib.Path) -> None:
        _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_path(tmp_path)
        assert data["kind"] == "dir"
        assert data["rows"][0]["status"] == "OK"

    def test_nonexistent_path(self) -> None:
        data = web.grade_path(pathlib.Path("/no/such/path.py"))
        assert data["kind"] == "error"
        assert "не найден" in data["message"].lower()
        assert data["rows"] == []

    def test_empty_directory(self, tmp_path: pathlib.Path) -> None:
        data = web.grade_path(tmp_path)
        assert data["kind"] == "error"
        assert "не найден" in data["message"].lower()

    def test_file_without_tests_marked_no_tests(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        row = web.grade_path(sol)["rows"][0]
        assert row["status"] == "NO TESTS"
        assert row["total"] == 0

    def test_empty_tests_dir_marked_no_tests(self, tmp_path: pathlib.Path) -> None:
        """tests/ существует, но не содержит распознаваемых кейсов — не FAIL 0/0 (issue #299)."""
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        (tmp_path / "tests").mkdir()
        row = web.grade_path(sol)["rows"][0]
        assert row["status"] == "NO TESTS"
        assert row["total"] == 0
        assert row["passed"] == 0

    def test_wa_case_carries_stdin_from_test_case(self, tmp_path: pathlib.Path) -> None:
        """grade_path() wires stdin through to the case's ErrorCard (issue #125)."""
        sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4 -> 6, ждём 5
        case = web.grade_path(sol)["rows"][0]["cases"][0]
        assert case["stdin"] == "4"
        assert case["actual"] == "6"
        assert case["expected"] == "5"

    def test_re_case_carries_exit_code_from_core(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "raise ValueError('boom')\n")
        case = web.grade_path(sol)["rows"][0]["cases"][0]
        assert case["verdict"] == "RE"
        assert case["exit_code"] not in (0, None)
        assert case["stderr"] == case["error"]

    def test_cancel_midrun_no_zip_strict_crash(self, tmp_path: pathlib.Path) -> None:
        """issue #422: отмена усекает res['cases'] — grade_path не должен падать
        ValueError на zip(strict=True) при большем числе загруженных тест-кейсов
        (иначе job.status=error вместо cancelled, красная ошибка в UI)."""
        sol = tmp_path / "task.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        tests = tmp_path / "tests"
        tests.mkdir()
        for i in range(1, 4):  # три кейса
            (tests / str(i)).write_text(str(i), encoding="utf-8")
            (tests / f"{i}.clue").write_text(str(i + 1), encoding="utf-8")

        cancel = threading.Event()

        def _cancel_after_tick(*args: object, **kwargs: object) -> None:
            cancel.set()  # отмена после первого тика прогресса run_tests

        data = web.grade_path(sol, progress_callback=_cancel_after_tick, cancel_event=cancel)

        assert data["kind"] == "file"  # не бросило ValueError
        assert len(data["rows"][0]["cases"]) < 3  # набор усечён, структура валидна

    def test_read_source_non_utf8_returns_error(self, tmp_path: pathlib.Path) -> None:
        """issue #423: не-UTF8 файл даёт kind=error, а не UnicodeDecodeError/500."""
        from stepik_grader.web.viewmodels import read_source

        p = tmp_path / "cp1251.py"
        p.write_bytes(b"x = '\xff\xfe'\n")  # 0xFF — заведомо невалидный UTF-8
        data = read_source(p)
        assert data["kind"] == "error"

    def test_unknown_re_exception_queues_missing_glossary_entry(
        self, tmp_path: pathlib.Path
    ) -> None:
        """J7 (web-current.md): unknown exception in an RE case gets queued for the
        glossary backlog when no card exists for it. Since #356 the RE hint
        resolver also consults the bundled JSON base (~140 stdlib exceptions),
        so "unknown" now means absent from BOTH sources — a custom (non-stdlib)
        exception is guaranteed to qualify."""
        from stepik_grader.glossary.json_provider import load_missing_queue

        sol = _make_task(
            tmp_path,
            "class UnlistedError(Exception):\n    pass\n\n\nraise UnlistedError('unusual')\n",
        )
        # A user-defined exception is in neither the compact map nor the bundled
        # JSON base, so it stays a genuine glossary gap.
        queue_path = tmp_path / "missing.json"

        web.grade_path(sol, missing_queue_path=queue_path)

        entries = load_missing_queue(queue_path)
        assert len(entries) == 1
        assert entries[0].concept == "UnlistedError"
        assert entries[0].kind == "exception"
        assert entries[0].origin == "error"
        assert entries[0].verdict == "RE"

    def test_known_re_exception_does_not_queue_missing_entry(self, tmp_path: pathlib.Path) -> None:
        from stepik_grader.glossary.json_provider import load_missing_queue

        sol = _make_task(tmp_path, "raise KeyError('x')\n")  # curated in core/glossary.py
        queue_path = tmp_path / "missing.json"

        web.grade_path(sol, missing_queue_path=queue_path)

        assert load_missing_queue(queue_path) == []

    def test_missing_queue_write_failure_does_not_break_grading(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A bad/unwritable queue path must never break grading (graceful degradation)."""
        sol = _make_task(tmp_path, "raise ArithmeticError('unusual')\n")
        # A directory path where a file write must fail — OSError, swallowed.
        bad_queue_path = tmp_path  # it's a dir, not a file

        data = web.grade_path(sol, missing_queue_path=bad_queue_path)

        assert data["rows"][0]["cases"][0]["verdict"] == "RE"


# ---------------------------------------------------------------------------
# ErrorCard fields on _case_view — issue #125 (web-current.md § Модель error cards)
# ---------------------------------------------------------------------------


class TestErrorCardFields:
    def test_ac_case_has_minimal_fields_only(self) -> None:
        case = web._case_view(
            1, {"passed": True, "verdict": "AC", "time": 0.01, "output": ["5"]}, stdin="4"
        )
        assert case["case_n"] == 1
        assert case["actions"] == ["run_again", "copy_input", "copy_output"]
        for key in ("severity", "suggestions", "glossary_ids", "expected", "stderr", "timeout_s"):
            assert key not in case

    def test_wa_case_error_card_fields(self) -> None:
        case = web._case_view(
            2,
            {
                "passed": False,
                "verdict": "WA",
                "time": 0.02,
                "output": ["6"],
                "expected": ["5"],
                "diff": "- 5\n+ 6",
                "error": "",
            },
            stdin="4",
        )
        assert case["severity"] == "error"
        assert case["expected"] == "5"
        assert case["actual"] == "6"
        assert case["diff"]
        assert "glossary_ids" not in case  # WA never gets glossary_ids (RE only)
        assert set(case["actions"]) == {"run_again", "copy_input", "copy_output", "explain_error"}

    def test_wa_case_with_invalid_utf8_output_has_hint(self) -> None:
        """issue #301: WA с '�' (U+FFFD) в actual → подсказка про не-UTF-8 байты."""
        case = web._case_view(
            5,
            {
                "passed": False,
                "verdict": "WA",
                "time": 0.01,
                "output": ["�� bad"],  # runner декодировал байты с заменами
                "expected": ["5"],
                "diff": "- 5\n+ �� bad",
                "error": "",
            },
            stdin="4",
        )
        assert case["suggestions"], "invalid-UTF-8 WA must carry a hint"
        assert "UTF-8" in case["suggestions"][0]

    def test_re_case_known_exception_has_glossary_ids_and_suggestion(self) -> None:
        case = web._case_view(
            3,
            {
                "passed": False,
                "verdict": "RE",
                "time": 0.03,
                "output": [],
                "error": "KeyError: 'x'",
                "exit_code": 1,
            },
            stdin="4",
        )
        assert case["severity"] == "error"
        assert case["stderr"] == "KeyError: 'x'"
        assert case["exit_code"] == 1
        assert case["glossary_ids"] == ["keyerror"]
        assert case["suggestions"]  # non-empty — curated hint from core/glossary.py
        assert "open_glossary" in case["actions"]

    def test_re_case_unknown_exception_has_empty_glossary_ids(self, tmp_path: pathlib.Path) -> None:
        # missing_queue_path pinned to tmp_path -- an unknown exception here
        # triggers J7 queuing (see TestGradePath.test_unknown_re_exception_...
        # below), and without this the default CONFIG.glossary_missing_queue
        # would write into the repo's real working directory.
        case = web._case_view(
            4,
            {
                "passed": False,
                "verdict": "RE",
                "time": 0.01,
                "output": [],
                "error": "CustomProjectError: boom",
                "exit_code": 1,
            },
            missing_queue_path=tmp_path / "missing.json",
        )
        assert case["glossary_ids"] == []
        assert case["suggestions"] == []
        assert "open_glossary" not in case["actions"]

    def test_tle_case_error_card_fields(self) -> None:
        from stepik_grader.config import CONFIG

        case = web._case_view(
            5,
            {
                "passed": False,
                "verdict": "TLE",
                "time": CONFIG.timeout_seconds,
                "output": [],
                "error": f"Timeout after {CONFIG.timeout_seconds}s",
                "exit_code": None,
                "timed_out": True,
            },
        )
        assert case["severity"] == "warning"
        assert case["timeout_s"] == CONFIG.timeout_seconds
        assert case["exit_code"] is None
        assert case["suggestions"]
        assert "glossary_ids" not in case  # TLE never links glossary content
        assert "expected" not in case


class TestWaSuggestion:
    """issue #301 — _wa_suggestion: одна подсказка по форме WA-вывода."""

    def test_invalid_utf8_takes_priority_over_whitespace(self) -> None:
        # actual с '�' И совпадающий после rstrip -> побеждает UTF-8-подсказка
        # (более специфичная причина, чем хвостовые пробелы).
        hint = web._wa_suggestion("5�  ", "5", lang="ru")
        assert hint is not None
        assert "UTF-8" in hint

    def test_whitespace_hint_when_no_replacement_char(self) -> None:
        hint = web._wa_suggestion("5  ", "5", lang="ru")
        assert hint is not None
        assert "UTF-8" not in hint  # это whitespace-подсказка, не UTF-8

    def test_no_hint_for_plain_mismatch(self) -> None:
        assert web._wa_suggestion("6", "5", lang="ru") is None

    def test_invalid_utf8_hint_localized_en(self) -> None:
        assert "UTF-8" in web._wa_suggestion("�", "5", lang="en")


# ---------------------------------------------------------------------------
# grade_benchmark (режим бенчмарка)
# ---------------------------------------------------------------------------


class TestGradeBenchmark:
    def test_benchmark_file(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_benchmark(sol, repeats=3)
        assert data["mode"] == "bench"
        row = data["rows"][0]
        assert row["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}
        assert row["runs"] >= 1
        assert isinstance(row["median"], str)  # отформатировано fmt_time

    def test_benchmark_dir_ranks_all_solutions(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        data = web.grade_benchmark(tmp_path, repeats=3)
        assert data["kind"] == "dir"
        assert len(data["rows"]) == 2
        # Строки отсортированы по возрастанию медианы — самый быстрый первым.
        assert all("verdict" in r for r in data["rows"])

    def test_benchmark_error_row_for_missing_tests(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        row = web.grade_benchmark(sol)["rows"][0]
        assert row["verdict"] == "ERR"
        assert row["error"]

    def test_benchmark_nonexistent_path(self) -> None:
        assert web.grade_benchmark(pathlib.Path("/no/such/dir"))["kind"] == "error"


# ---------------------------------------------------------------------------
# grade_benchmark/grade_microbench — progress_callback/cancel_event (issue #262)
# ---------------------------------------------------------------------------


class TestGradeBenchmarkProgressAndCancel:
    def test_progress_callback_forwarded_across_directory(self, tmp_path: pathlib.Path) -> None:
        """One shared callback ticks across ALL solutions in a directory run
        (not reset per-solution) -- proves the per-solution loop forwards the
        SAME callback object into every run_benchmark() call."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")

        ticks: list[int] = []
        web.grade_benchmark(tmp_path, repeats=2, progress_callback=ticks.append)

        assert ticks == [1] * 4  # 2 solutions * 1 case * 2 repeats

    def test_cancel_event_stops_before_all_solutions_processed(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")

        cancel_event = threading.Event()
        cancel_event.set()  # pre-cancelled -- loop must not process any solution
        data = web.grade_benchmark(tmp_path, repeats=2, cancel_event=cancel_event)

        assert data["rows"] == []


class TestEstimateRunCount:
    def test_bench_multiplies_cases_by_repeats(self, tmp_path: pathlib.Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        for i in (1, 2, 3):
            (tests / f"input_{i}.txt").write_text(str(i), encoding="utf-8")
            (tests / f"expected_{i}.txt").write_text(str(i), encoding="utf-8")
        sol_a = tmp_path / "task1_1.py"
        sol_a.write_text("print(input())\n", encoding="utf-8")
        sol_b = tmp_path / "task1_2.py"
        sol_b.write_text("print(input())\n", encoding="utf-8")

        total = web.estimate_run_count([sol_a, sol_b], kind="bench", repeats=5)

        assert total == 2 * 3 * 5  # 2 solutions * 3 cases * 5 repeats

    def test_microbench_counts_solutions_not_cases(self, tmp_path: pathlib.Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "input_1.txt").write_text("1", encoding="utf-8")
        (tests / "expected_1.txt").write_text("1", encoding="utf-8")
        sol = tmp_path / "task1_1.py"
        sol.write_text("print(input())\n", encoding="utf-8")

        total = web.estimate_run_count([sol] * 3, kind="microbench")

        assert total == 3  # one tick per solution, repeats/cases irrelevant

    def test_solution_without_test_dir_contributes_zero(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1_1.py"
        sol.write_text("print(1)\n", encoding="utf-8")  # no tests/ dir at all

        assert web.estimate_run_count([sol], kind="bench", repeats=10) == 0


# ---------------------------------------------------------------------------
# grade_benchmark(reference=...) — режим «Сравнение» (Compare, редизайн #123)
# ---------------------------------------------------------------------------


def _make_bench_pair(tmp_path: pathlib.Path) -> None:
    """task1_1.py (быстрый) + task1_2.py (заметно медленнее) + общий tests/."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")
    (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
    (tmp_path / "task1_2.py").write_text(
        "import time\ntime.sleep(0.05)\nprint(int(input()) + 1)\n", encoding="utf-8"
    )


class TestGradeBenchmarkReference:
    def test_reference_file_gets_reference_verdict(self, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        data = web.grade_benchmark(tmp_path, repeats=3, reference="task1_1.py")
        rows_by_file = {r["file"]: r for r in data["rows"]}
        assert rows_by_file["task1_1.py"]["verdict"] == "REFERENCE"
        assert rows_by_file["task1_1.py"]["relative"] == 100.0
        assert data["reference_file"] == "task1_1.py"
        assert "print(int(input()) + 1)" in data["reference_source"]

    def test_slower_solution_relative_to_reference(self, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        data = web.grade_benchmark(tmp_path, repeats=3, reference="task1_1.py")
        rows_by_file = {r["file"]: r for r in data["rows"]}
        assert rows_by_file["task1_2.py"]["verdict"] in {"SLOWER", "MUCH_SLOWER"}
        assert rows_by_file["task1_2.py"]["relative"] > 100.0

    def test_faster_solution_relative_to_reference(self, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        data = web.grade_benchmark(tmp_path, repeats=3, reference="task1_2.py")
        rows_by_file = {r["file"]: r for r in data["rows"]}
        assert rows_by_file["task1_1.py"]["verdict"] == "FASTER"
        assert rows_by_file["task1_2.py"]["verdict"] == "REFERENCE"

    def test_reference_by_full_path_also_resolves(self, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        full_path = str(tmp_path / "task1_1.py")
        data = web.grade_benchmark(tmp_path, repeats=3, reference=full_path)
        rows_by_file = {r["file"]: r for r in data["rows"]}
        assert rows_by_file["task1_1.py"]["verdict"] == "REFERENCE"

    def test_unresolvable_reference_falls_back_to_normal_ranking(
        self, tmp_path: pathlib.Path
    ) -> None:
        _make_bench_pair(tmp_path)
        data = web.grade_benchmark(tmp_path, repeats=3, reference="no_such_file.py")
        assert "REFERENCE" not in {r["verdict"] for r in data["rows"]}
        assert "reference_source" not in data

    def test_no_reference_behaves_exactly_as_before(self, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        data = web.grade_benchmark(tmp_path, repeats=3)
        assert "REFERENCE" not in {r["verdict"] for r in data["rows"]}
        assert "reference_source" not in data


# ---------------------------------------------------------------------------
# grade_microbench — режим 4 (timeit) в web (issue #187)
# ---------------------------------------------------------------------------


class TestGradeMicrobench:
    def test_microbench_file(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_microbench(sol, number=10)
        assert data["mode"] == "microbench"
        row = data["rows"][0]
        assert row["runs"] >= 1
        assert isinstance(row["min_us"], float)
        assert isinstance(row["median_us"], float)
        assert isinstance(row["mean_us"], float)
        assert isinstance(row["max_us"], float)
        assert isinstance(row["stdev_us"], float)
        assert row["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}
        assert "group" not in data  # только для kind="dir"

    def test_microbench_dir_single_group_sorted_by_median(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        data = web.grade_microbench(tmp_path, number=10)
        assert data["kind"] == "dir"
        assert data["group"] == tmp_path.name
        assert len(data["rows"]) == 2
        assert data["rows"][0]["median_us"] <= data["rows"][1]["median_us"]

    def test_microbench_multiple_groups_picks_first_and_lists_rest(
        self, tmp_path: pathlib.Path
    ) -> None:
        for name in ("group_a", "group_b"):
            folder = tmp_path / name
            (folder / "tests").mkdir(parents=True)
            (folder / "tests" / "1").write_text("4", encoding="utf-8")
            (folder / "tests" / "1.clue").write_text("5", encoding="utf-8")
            (folder / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        data = web.grade_microbench(tmp_path, number=10)
        assert data["group"] == "group_a"
        assert data["other_groups"] == ["group_b"]
        assert len(data["rows"]) == 1

    def test_microbench_no_tests_found(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        data = web.grade_microbench(sol, number=10)
        assert data["kind"] == "error"

    def test_microbench_empty_tests_dir(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        sol = tmp_path / "task.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        data = web.grade_microbench(sol, number=10)
        assert data["kind"] == "error"

    def test_microbench_partial_error_produces_err_row(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("raise ValueError('boom')\n", encoding="utf-8")
        data = web.grade_microbench(tmp_path, number=10)
        verdicts_by_file = {r["file"]: r["verdict"] for r in data["rows"]}
        assert verdicts_by_file["task1_2.py"] == "ERR"
        assert verdicts_by_file["task1_1.py"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}

    def test_microbench_custom_number(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_microbench(sol, number=50)
        assert data["rows"][0]["runs"] > 0

    def test_microbench_nonexistent_path(self) -> None:
        assert web.grade_microbench(pathlib.Path("/no/such/dir"))["kind"] == "error"


# ---------------------------------------------------------------------------
# list_solutions / read_source — пикер режима 1 «Один файл» (issue #125-fix)
# ---------------------------------------------------------------------------


class TestListSolutions:
    def test_finds_solutions_in_directory(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task1_1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(2)\n", encoding="utf-8")
        data = web.list_solutions(tmp_path)
        assert data["kind"] == "dir"
        assert data["base"] == str(tmp_path)
        assert len(data["files"]) == 2
        assert all(f.endswith(".py") for f in data["files"])

    def test_empty_directory_is_error(self, tmp_path: pathlib.Path) -> None:
        data = web.list_solutions(tmp_path)
        assert data["kind"] == "error"
        assert data["files"] == []

    def test_nonexistent_path_is_error(self) -> None:
        data = web.list_solutions(pathlib.Path("/no/such/dir"))
        assert data["kind"] == "error"
        assert data["files"] == []

    def test_file_path_is_error_not_a_directory(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1_1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        data = web.list_solutions(sol)
        assert data["kind"] == "error"


class TestReadSource:
    def test_reads_existing_file(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1_1.py"
        sol.write_text("print('hello')\n", encoding="utf-8")
        data = web.read_source(sol)
        assert data["kind"] == "file"
        assert data["source"] == "print('hello')\n"
        assert data["path"] == str(sol)

    def test_nonexistent_file_is_error(self, tmp_path: pathlib.Path) -> None:
        data = web.read_source(tmp_path / "nope.py")
        assert data["kind"] == "error"
        assert "message" in data

    def test_directory_path_is_error_not_a_file(self, tmp_path: pathlib.Path) -> None:
        data = web.read_source(tmp_path)
        assert data["kind"] == "error"

    def test_returns_mtime_baseline(self, tmp_path: pathlib.Path) -> None:
        """issue #297: read_source отдаёт mtime — baseline для optimistic locking."""
        sol = tmp_path / "task1_1.py"
        sol.write_text("print('hi')\n", encoding="utf-8")
        data = web.read_source(sol)
        assert data["mtime"] == pytest.approx(sol.stat().st_mtime)


class TestSaveSolution:
    def test_overwrites_existing_file(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task_1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        data = web.save_solution(tmp_path, sol, "print(2)\n")
        assert data["ok"] is True
        assert data["path"] == str(sol)
        assert sol.read_text(encoding="utf-8") == "print(2)\n"

    def test_no_path_creates_task_1_in_empty_folder(self, tmp_path: pathlib.Path) -> None:
        data = web.save_solution(tmp_path, None, "print('hi')\n")
        assert data["ok"] is True
        assert data["path"] == str(tmp_path / "task_1.py")
        assert (tmp_path / "task_1.py").read_text(encoding="utf-8") == "print('hi')\n"

    def test_no_path_extends_bare_task_series(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task_1.py").write_text("print(1)\n", encoding="utf-8")
        data = web.save_solution(tmp_path, None, "print(2)\n")
        assert data["path"] == str(tmp_path / "task_2.py")

    def test_no_path_extends_downloader_style_series(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task4_1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "task4_2.py").write_text("print(2)\n", encoding="utf-8")
        data = web.save_solution(tmp_path, None, "print(3)\n")
        assert data["path"] == str(tmp_path / "task4_3.py")

    def test_folder_not_found_is_error(self, tmp_path: pathlib.Path) -> None:
        data = web.save_solution(tmp_path / "nope", None, "print(1)\n")
        assert data["ok"] is False
        assert "message" in data

    def test_write_failure_does_not_raise(self, tmp_path: pathlib.Path) -> None:
        bad_path = tmp_path / "no_such_subdir" / "task_1.py"
        data = web.save_solution(tmp_path, bad_path, "print(1)\n")
        assert data["ok"] is False
        assert "message" in data

    def test_returns_mtime_on_success(self, tmp_path: pathlib.Path) -> None:
        """issue #297: успешный save отдаёт свежий mtime (новый baseline)."""
        sol = tmp_path / "task_1.py"
        data = web.save_solution(tmp_path, sol, "print(1)\n")
        assert data["ok"] is True
        assert data["mtime"] == pytest.approx(sol.stat().st_mtime)

    def test_expected_mtime_mismatch_refuses_with_conflict(self, tmp_path: pathlib.Path) -> None:
        """issue #297: файл изменился на диске с момента загрузки → conflict, не пишем."""
        sol = tmp_path / "task_1.py"
        sol.write_text("on disk v1\n", encoding="utf-8")
        stale_mtime = sol.stat().st_mtime - 1000  # заведомо расходится
        data = web.save_solution(tmp_path, sol, "editor content\n", expected_mtime=stale_mtime)
        assert data["ok"] is False
        assert data["conflict"] is True
        # Файл НЕ перезаписан.
        assert sol.read_text(encoding="utf-8") == "on disk v1\n"

    def test_expected_mtime_match_writes(self, tmp_path: pathlib.Path) -> None:
        """issue #297: mtime совпадает → запись проходит."""
        sol = tmp_path / "task_1.py"
        sol.write_text("v1\n", encoding="utf-8")
        current = sol.stat().st_mtime
        data = web.save_solution(tmp_path, sol, "v2\n", expected_mtime=current)
        assert data["ok"] is True
        assert sol.read_text(encoding="utf-8") == "v2\n"

    def test_expected_mtime_ignored_for_new_file(self, tmp_path: pathlib.Path) -> None:
        """issue #297: для нового файла (path=None) optimistic locking не применяется."""
        data = web.save_solution(tmp_path, None, "print(1)\n", expected_mtime=12345.0)
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# HTTP-хендлер (интеграционно: реальный сервер на эфемерном порту)
# ---------------------------------------------------------------------------


@pytest.fixture
def server_factory():
    """Фабрика серверов на 127.0.0.1:0 в отдельном потоке (issue #261 —
    параметризуемая workspace/confine); все созданные серверы гасятся в teardown."""
    started: list[tuple[web._GraderServer, threading.Thread]] = []

    def _make(
        workspace: pathlib.Path,
        *,
        confine: bool = True,
        sandbox: bool = False,
        record_history: bool = True,
    ) -> str:
        httpd = web._GraderServer(
            ("127.0.0.1", 0),
            web._Handler,
            workspace=workspace,
            confine=confine,
            sandbox=sandbox,
            record_history=record_history,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        host, port = httpd.server_address[0], httpd.server_address[1]
        return f"http://{host}:{port}"

    yield _make
    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def server(tmp_path: pathlib.Path, server_factory) -> str:
    """Сервер с workspace=tmp_path, confine=True — дефолт для большинства тестов
    (все они оперируют путями внутри tmp_path, так что конфайнмент им не мешает)."""
    return server_factory(tmp_path)


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class TestHttpHandler:
    def test_index_serves_html(self, server: str) -> None:
        status, body = _get(server + "/")
        assert status == 200
        assert b"<!doctype html>" in body.lower()
        assert b"Stepik Python Grader" in body

    def test_api_grade_returns_json(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(sol)))
        assert status == 200
        data = json.loads(body)
        assert data["kind"] == "file"
        assert data["rows"][0]["status"] == "OK"

    def test_api_grade_without_path_is_error(self, server: str) -> None:
        status, body = _get(server + "/api/grade")
        assert status == 200
        assert json.loads(body)["kind"] == "error"

    def test_api_grade_bench_mode(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "3"})
        status, body = _get(server + "/api/grade?" + q)
        assert status == 200
        data = json.loads(body)
        assert data["mode"] == "bench"
        assert data["rows"][0]["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}

    def test_api_grade_microbench_mode(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "microbench", "number": "10"})
        status, body = _get(server + "/api/grade?" + q)
        assert status == 200
        data = json.loads(body)
        assert data["mode"] == "microbench"
        assert data["rows"][0]["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}
        assert isinstance(data["rows"][0]["median_us"], float)

    def test_api_grade_bench_mode_with_reference(self, server: str, tmp_path: pathlib.Path) -> None:
        _make_bench_pair(tmp_path)
        q = urllib.parse.urlencode(
            {"path": str(tmp_path), "mode": "bench", "repeats": "3", "reference": "task1_1.py"}
        )
        status, body = _get(server + "/api/grade?" + q)
        assert status == 200
        data = json.loads(body)
        rows_by_file = {r["file"]: r for r in data["rows"]}
        assert rows_by_file["task1_1.py"]["verdict"] == "REFERENCE"
        assert data["reference_file"] == "task1_1.py"

    def test_index_injects_default_path(self, server: str) -> None:
        # Плейсхолдер __DEFAULT_PATH__ должен быть заменён на реальный cwd.
        _, body = _get(server + "/")
        assert b"__DEFAULT_PATH__" not in body

    def test_index_injects_exec_mode_flags_default(self, server: str) -> None:
        """issue #565: HTML несёт data-флаги режима; дефолт — без OS-изоляции, история on."""
        _, body = _get(server + "/")
        page = body.decode("utf-8")
        assert 'data-sandbox="false"' in page
        assert 'data-record-history="true"' in page
        # плейсхолдеры заменены, «сырых» не осталось
        assert "__EXEC_SANDBOX__" not in page
        assert "__RECORD_HISTORY__" not in page

    def test_index_injects_exec_mode_flags_sandbox_no_history(
        self, tmp_path: pathlib.Path, server_factory
    ) -> None:
        """issue #565: при sandbox=True и record_history=False флаги отражают режим."""
        url = server_factory(tmp_path, sandbox=True, record_history=False)
        _, body = _get(url + "/")
        page = body.decode("utf-8")
        assert 'data-sandbox="true"' in page
        assert 'data-record-history="false"' in page

    def test_index_injects_onboarding_seen_default_false(self, server: str) -> None:
        """issue #660: чистый workspace → онбординг ещё не закрыт (флаг false)."""
        _, body = _get(server + "/")
        page = body.decode("utf-8")
        assert 'data-onboarding-seen="false"' in page
        assert "__ONBOARDING_SEEN__" not in page

    def test_index_onboarding_seen_true_when_flag_set(
        self, tmp_path: pathlib.Path, server_factory
    ) -> None:
        """issue #660: сохранённый в .grader_settings.json флаг → data-onboarding-seen=true."""
        from stepik_grader.core import user_settings

        user_settings.save_settings(
            user_settings.UserSettings(onboarding_seen=True),
            tmp_path / user_settings.SETTINGS_FILE_NAME,
        )
        url = server_factory(tmp_path)
        _, body = _get(url + "/")
        assert 'data-onboarding-seen="true"' in body.decode("utf-8")

    def test_settings_endpoint_persists_onboarding_seen(
        self, tmp_path: pathlib.Path, server_factory
    ) -> None:
        """issue #660: POST /api/v1/settings пишет флаг закрытия онбординга."""
        from stepik_grader.core import user_settings

        url = server_factory(tmp_path)
        status, _ = _post(
            url + "/api/v1/settings", json.dumps({"onboarding_seen": True}).encode("utf-8")
        )
        assert status == 200
        assert (
            user_settings.load_settings(tmp_path / user_settings.SETTINGS_FILE_NAME).onboarding_seen
            is True
        )

    def test_settings_endpoint_can_reset_onboarding(
        self, tmp_path: pathlib.Path, server_factory
    ) -> None:
        """issue #660: снятая галка «не показывать» (POST false) возвращает авто-показ."""
        from stepik_grader.core import user_settings

        settings_path = tmp_path / user_settings.SETTINGS_FILE_NAME
        user_settings.save_settings(user_settings.UserSettings(onboarding_seen=True), settings_path)
        url = server_factory(tmp_path)
        status, _ = _post(
            url + "/api/v1/settings", json.dumps({"onboarding_seen": False}).encode("utf-8")
        )
        assert status == 200
        assert user_settings.load_settings(settings_path).onboarding_seen is False

    def test_unknown_path_404(self, server: str) -> None:
        status, _ = _get(server + "/nope")
        assert status == 404

    # -- static routes (issue #125 — JS/CSS extracted from _INDEX_HTML) ------

    def test_static_app_css_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.css", timeout=5) as resp:
            assert resp.status == 200
            assert "text/css" in resp.headers["Content-Type"]
            assert b":root" in resp.read()

    def test_static_app_js_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.js", timeout=5) as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers["Content-Type"]
            # issue #426: app.js is now the ES-module entry that imports the
            # split feature modules (grade/sandbox/… were extracted out of it).
            assert b"import {" in resp.read()

    # -- static/fonts/*.woff2 (issue #260 — вендоринг вместо Google Fonts CDN) --

    @pytest.mark.parametrize(
        "name",
        [
            "jetbrains-mono-latin.woff2",
            "jetbrains-mono-cyrillic.woff2",
            "inter-latin.woff2",
            "inter-cyrillic.woff2",
        ],
    )
    def test_static_font_served(self, server: str, name: str) -> None:
        with urllib.request.urlopen(server + "/static/fonts/" + name, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "font/woff2"
            body = resp.read()
            assert body[:4] == b"wOF2"  # WOFF2 magic number
            assert len(body) > 1000

    def test_index_html_has_no_external_resource_links(self) -> None:
        """Регрессия issue #260: страница не должна грузить ни один ресурс с
        внешнего домена (Google Fonts CDN был единственным источником) —
        только placeholder-текст со ссылкой-примером допустим."""
        assert "fonts.googleapis.com" not in web._INDEX_HTML


class TestUiLocaleCatalog:
    """issue #545 — каталог i18n статической оболочки (/static/locales/ui.json)
    и его согласованность с data-i18n-разметкой index.html."""

    def test_ui_locales_served_as_json(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/locales/ui.json", timeout=5) as resp:
            assert resp.status == 200
            assert "application/json" in resp.headers["Content-Type"]
            # _send ставит nosniff на все ответы (issue #563)
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
            cat = json.loads(resp.read())
        assert set(cat) == {"ru", "en"}
        assert cat["ru"] and cat["en"]  # непустые каталоги

    def test_ui_locales_ru_en_key_parity(self, server: str) -> None:
        """Наборы ключей ru и en идентичны (паритет обязателен, иначе рассинхрон)."""
        _, body = _get(server + "/static/locales/ui.json")
        cat = json.loads(body)
        assert set(cat["ru"]) == set(cat["en"])

    def test_every_index_data_i18n_key_is_in_catalog(self, server: str) -> None:
        """Каждый ключ data-i18n[/-placeholder/-title/-aria-label] из index.html
        присутствует в обеих локалях каталога (issue #545)."""
        _, body = _get(server + "/static/locales/ui.json")
        cat = json.loads(body)
        keys = set(
            re.findall(
                r'data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"',
                web._INDEX_HTML,
            )
        )
        assert keys, "в index.html нет ни одного data-i18n — разметка не размечена"
        for key in keys:
            assert key in cat["ru"], f"ключ {key!r} отсутствует в локали ru"
            assert key in cat["en"], f"ключ {key!r} отсутствует в локали en"


class TestSecurityHeaders:
    """issue #563: CSP + X-Content-Type-Options на ответах и read-timeout."""

    def test_html_response_carries_csp_and_nosniff(self, server: str) -> None:
        with urllib.request.urlopen(server + "/", timeout=5) as resp:
            csp = resp.headers["Content-Security-Policy"]
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
        for token in (
            "default-src 'self'",
            "base-uri 'none'",
            "object-src 'none'",
            # script остаётся строгим 'self' (главный барьер XSS); 'unsafe-inline'
            # только для style — его требует вендоренный CodeMirror (issue #563).
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self'",
        ):
            assert token in csp, token
        assert "script-src" not in csp  # script наследует строгий default-src 'self'

    def test_html_response_denies_framing(self, server: str) -> None:
        """issue #631: страницу нельзя встроить в чужой iframe (clickjacking).

        Внутрифреймовые вызовы идут в СВОЙ origin, поэтому CSRF-guard их не
        режет — без этих заголовков жертва кликает по невидимым кнопкам
        «Проверить»/«Скачать» своего же грейдера.
        """
        with urllib.request.urlopen(server + "/", timeout=5) as resp:
            assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
            # X-Frame-Options — для браузеров, не понимающих frame-ancestors.
            assert resp.headers["X-Frame-Options"] == "DENY"

    def test_static_css_carries_nosniff(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.css", timeout=5) as resp:
            assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_api_json_carries_nosniff(self, server: str) -> None:
        # /api/grade без path → JSON-ошибка (200), заголовок nosniff всё равно есть.
        with urllib.request.urlopen(server + "/api/grade", timeout=5) as resp:
            assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_no_inline_styles_in_served_static(self) -> None:
        """Наш собственный код не полагается на 'unsafe-inline' в CSP (его
        требует лишь вендоренный CodeMirror) — гейт против регресса: ни HTML, ни
        один static/*.js не содержат ``style="``/``style='``. Динамика идёт через
        CSSOM (``el.style.prop``), классы — через app.css (issue #563)."""
        for source, label in (
            (web._INDEX_HTML, "index.html"),
            (web._STATIC_JS_SOURCES, "static/*.js"),
        ):
            assert 'style="' not in source, f"инлайновый style= в {label}"
            assert "style='" not in source, f"инлайновый style= в {label}"

    def test_handler_has_read_timeout(self) -> None:
        """Соединение получает read-timeout — медленный клиент не держит поток."""
        assert web._Handler.timeout == 30
        assert "fonts.gstatic.com" not in web._INDEX_HTML
        assert not re.search(r'(?:href|src)="https?://', web._INDEX_HTML)

    # -- /api/solutions, /api/source (пикер режима 1, issue #125-fix) --------

    def test_api_solutions_lists_files(self, server: str, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task1_1.py").write_text("print(1)\n", encoding="utf-8")
        status, body = _get(server + "/api/solutions?path=" + urllib.parse.quote(str(tmp_path)))
        assert status == 200
        data = json.loads(body)
        assert data["kind"] == "dir"
        assert len(data["files"]) == 1

    def test_api_solutions_without_path_is_error(self, server: str) -> None:
        status, body = _get(server + "/api/solutions")
        assert status == 200
        assert json.loads(body)["kind"] == "error"

    def test_api_source_reads_file(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1_1.py"
        sol.write_text("print(42)\n", encoding="utf-8")
        status, body = _get(server + "/api/source?path=" + urllib.parse.quote(str(sol)))
        assert status == 200
        data = json.loads(body)
        assert data["kind"] == "file"
        assert data["source"] == "print(42)\n"

    def test_api_source_without_path_is_error(self, server: str) -> None:
        status, body = _get(server + "/api/source")
        assert status == 200
        assert json.loads(body)["kind"] == "error"

    # -- POST /api/save-solution (окно ввода кода, доделка #125) --------------

    def test_api_save_solution_creates_new_file(self, server: str, tmp_path: pathlib.Path) -> None:
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(tmp_path), "code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["path"] == str(tmp_path / "task_1.py")
        assert (tmp_path / "task_1.py").read_text(encoding="utf-8") == "print(1)\n"

    def test_api_save_solution_missing_folder_is_400(self, server: str) -> None:
        status, body = _post(
            server + "/api/save-solution", json.dumps({"code": "print(1)\n"}).encode("utf-8")
        )
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_api_save_solution_invalid_json_is_400(self, server: str) -> None:
        status, _ = _post(server + "/api/save-solution", b"not json")
        assert status == 400

    def test_api_save_solution_non_string_code_is_400(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        """issue #646 (T5): code не строка → 400 (server.py:578, ветка была без теста)."""
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(tmp_path), "code": 123}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_api_body_not_json_object_is_400(self, server: str) -> None:
        """issue #646 (T5): тело — валидный JSON, но не объект → 400 (server.py:657)."""
        status, body = _post(server + "/api/save-solution", json.dumps([1, 2, 3]).encode("utf-8"))
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_api_save_solution_returns_mtime(self, server: str, tmp_path: pathlib.Path) -> None:
        """issue #297: ответ save-solution несёт mtime (новый baseline фронта)."""
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(tmp_path), "code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["mtime"] == pytest.approx((tmp_path / "task_1.py").stat().st_mtime)

    def test_api_save_solution_stale_mtime_is_conflict(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        """issue #297: expected_mtime расходится с диском → conflict, файл не тронут."""
        sol = tmp_path / "task_1.py"
        sol.write_text("on disk\n", encoding="utf-8")
        status, body = _post(
            server + "/api/save-solution",
            json.dumps(
                {
                    "folder": str(tmp_path),
                    "path": str(sol),
                    "code": "from editor\n",
                    "expected_mtime": sol.stat().st_mtime - 1000,
                }
            ).encode("utf-8"),
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is False
        assert data["conflict"] is True
        assert sol.read_text(encoding="utf-8") == "on disk\n"  # не перезаписан


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post_raw(
    server: str, path: str, body: bytes = b"", *, content_length: str | None = "__auto__"
) -> tuple[int, bytes]:
    """POST with full control over the ``Content-Length`` header.

    ``content_length="__auto__"`` sends the real length of ``body`` (normal
    case). ``None`` omits the header entirely. Any other string sends that
    literal value instead — used to test missing/malformed/negative
    Content-Length (issue #259).
    """
    parsed = urllib.parse.urlparse(server)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", "application/json")
        if content_length == "__auto__":
            conn.putheader("Content-Length", str(len(body)))
        elif content_length is not None:
            conn.putheader("Content-Length", content_length)
        conn.endheaders()
        if body and content_length != "0":
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# API input limits — request body size, repeats/number clamps (issue #259)
# ---------------------------------------------------------------------------


class TestApiInputLimits:
    def test_clamp_helper(self) -> None:
        from stepik_grader.web.api_routes import _clamp

        assert _clamp(5, 1, 10) == 5
        assert _clamp(-5, 1, 10) == 1
        assert _clamp(999, 1, 10) == 10

    def test_post_body_over_limit_is_413(self, server: str, tmp_path: pathlib.Path) -> None:
        from stepik_grader.web.http_guards import _MAX_BODY_BYTES

        oversized = json.dumps({"folder": str(tmp_path), "code": "x" * (_MAX_BODY_BYTES + 10)})
        status, body = _post(server + "/api/save-solution", oversized.encode("utf-8"))
        assert status == 413
        assert json.loads(body)["ok"] is False

    def test_post_missing_content_length_is_400(self, server: str) -> None:
        status, body = _post_raw(server, "/api/save-solution", content_length=None)
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_post_non_numeric_content_length_is_400(self, server: str) -> None:
        status, body = _post_raw(server, "/api/save-solution", content_length="not-a-number")
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_post_negative_content_length_is_400(self, server: str) -> None:
        status, body = _post_raw(server, "/api/save-solution", content_length="-5")
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_bench_repeats_zero_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_benchmark(
            path: str, repeats: int, reference: str | None = None, lang: str = "ru", **_kwargs
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.api_routes.grade_benchmark", fake_grade_benchmark)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "0"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["repeats"] == 1

    def test_bench_repeats_huge_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_benchmark(
            path: str, repeats: int, reference: str | None = None, lang: str = "ru", **_kwargs
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.api_routes.grade_benchmark", fake_grade_benchmark)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "999999999"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["repeats"] == 1000

    def test_microbench_number_negative_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_microbench(path: str, number: int, lang: str = "ru", **_kwargs) -> dict:
            captured["number"] = number
            return {"kind": "microbench", "mode": "microbench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.api_routes.grade_microbench", fake_grade_microbench)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "microbench", "number": "-5"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["number"] == 1

    def test_microbench_number_huge_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_microbench(path: str, number: int, lang: str = "ru", **_kwargs) -> dict:
            captured["number"] = number
            return {"kind": "microbench", "mode": "microbench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.api_routes.grade_microbench", fake_grade_microbench)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode(
            {"path": str(sol), "mode": "microbench", "number": "99999999999"}
        )
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["number"] == 1_000_000

    def test_bench_repeats_non_numeric_uses_default(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_benchmark(
            path: str, repeats: int, reference: str | None = None, lang: str = "ru", **_kwargs
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.api_routes.grade_benchmark", fake_grade_benchmark)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "abc"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["repeats"] == 15  # default, within range


# ---------------------------------------------------------------------------
# Workspace root confinement — /api/grade, /api/source, /api/solutions,
# /api/save-solution (issue #261)
# ---------------------------------------------------------------------------


class TestApiPathConfinement:
    def test_grade_path_inside_root_is_allowed(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(sol)))
        assert status == 200
        assert json.loads(body)["kind"] == "file"

    def test_grade_absolute_path_outside_root_is_403(
        self, server: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        sol = _make_task(outside, "print(int(input()) + 1)\n")
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(sol)))
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_grade_dotdot_escape_is_403(
        self,
        server: str,
        tmp_path: pathlib.Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        sol = _make_task(outside, "print(int(input()) + 1)\n")
        escape_path = tmp_path / ".." / outside.name / sol.name
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(escape_path)))
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_grade_symlink_escape_is_403(
        self,
        server: str,
        tmp_path: pathlib.Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        sol = _make_task(outside, "print(int(input()) + 1)\n")
        link = tmp_path / "escape_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported/permitted in this environment")
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(link / sol.name)))
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_source_inside_root_is_allowed(self, server: str, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "task1_1.py"
        f.write_text("print(42)\n", encoding="utf-8")
        status, body = _get(server + "/api/source?path=" + urllib.parse.quote(str(f)))
        assert status == 200
        assert json.loads(body)["kind"] == "file"

    def test_source_outside_root_is_403(
        self, server: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        f = outside / "secret.py"
        f.write_text("print('leak')\n", encoding="utf-8")
        status, body = _get(server + "/api/source?path=" + urllib.parse.quote(str(f)))
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_solutions_inside_root_is_allowed(self, server: str, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task1_1.py").write_text("print(1)\n", encoding="utf-8")
        status, body = _get(server + "/api/solutions?path=" + urllib.parse.quote(str(tmp_path)))
        assert status == 200
        assert json.loads(body)["kind"] == "dir"

    def test_solutions_outside_root_is_403(
        self, server: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        (outside / "task1_1.py").write_text("print(1)\n", encoding="utf-8")
        status, body = _get(server + "/api/solutions?path=" + urllib.parse.quote(str(outside)))
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_save_solution_folder_inside_root_is_allowed(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(tmp_path), "code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 200
        assert json.loads(body)["ok"] is True

    def test_save_solution_folder_outside_root_is_403(
        self, server: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(outside), "code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 403
        assert json.loads(body)["kind"] == "error"
        assert not (outside / "task_1.py").exists()

    def test_save_solution_target_path_outside_root_is_403(
        self,
        server: str,
        tmp_path: pathlib.Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        target = outside / "overwrite_me.py"
        target.write_text("print('orig')\n", encoding="utf-8")
        status, body = _post(
            server + "/api/save-solution",
            json.dumps(
                {"folder": str(tmp_path), "path": str(target), "code": "print('pwned')\n"}
            ).encode("utf-8"),
        )
        assert status == 403
        assert json.loads(body)["kind"] == "error"
        assert target.read_text(encoding="utf-8") == "print('orig')\n"

    def test_confine_false_allows_path_outside_workspace(
        self,
        server_factory,
        tmp_path: pathlib.Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        """--no-root-confinement escape hatch: outside paths allowed again."""
        outside = tmp_path_factory.mktemp("outside")
        sol = _make_task(outside, "print(int(input()) + 1)\n")
        unconfined = server_factory(tmp_path, confine=False)
        status, body = _get(unconfined + "/api/grade?path=" + urllib.parse.quote(str(sol)))
        assert status == 200
        assert json.loads(body)["kind"] == "file"

    def test_default_path_reflects_workspace(self, server_factory, tmp_path: pathlib.Path) -> None:
        """__DEFAULT_PATH__ in index.html is the server's workspace, not cwd."""
        custom = server_factory(tmp_path)
        _, body = _get(custom + "/")
        assert str(tmp_path) in body.decode("utf-8")


# ---------------------------------------------------------------------------
# Host/Origin/Referer guard for /api/* (issue #242, F-03)
# ---------------------------------------------------------------------------


class TestApiHostOriginGuard:
    def test_wrong_host_header_is_rejected(self, server: str) -> None:
        status, body = _get(server + "/api/grade", headers={"Host": "evil.example"})
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_localhost_host_header_is_allowed(self, server: str) -> None:
        """``Host: localhost:<port>`` — точное значение хоста, к которому шёл коннект,
        для guard'а не важно (проверяется только hostname)."""
        port = urllib.parse.urlparse(server).port
        status, _ = _get(server + "/api/grade", headers={"Host": f"localhost:{port}"})
        assert status == 200

    def test_cross_origin_get_is_rejected(self, server: str) -> None:
        status, body = _get(server + "/api/grade", headers={"Origin": "http://evil.example"})
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_matching_origin_is_allowed(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, _ = _get(
            server + "/api/grade?path=" + urllib.parse.quote(str(sol)),
            headers={"Origin": server},
        )
        assert status == 200

    def test_no_origin_or_referer_is_allowed(self, server: str) -> None:
        """Отсутствие Origin/Referer вообще (curl, тесты) не блокируется —
        только несовпадающее значение."""
        status, _ = _get(server + "/api/grade")
        assert status == 200

    def test_cross_site_fetch_metadata_is_rejected(self, server: str) -> None:
        """issue #399: Sec-Fetch-Site: cross-site отклоняется даже без Origin/
        Referer (Fetch Metadata — браузерный межсайтовый запрос)."""
        status, body = _get(server + "/api/grade", headers={"Sec-Fetch-Site": "cross-site"})
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_same_origin_fetch_metadata_is_allowed(self, server: str) -> None:
        """Sec-Fetch-Site: same-origin (собственные fetch страницы) — проходит."""
        status, _ = _get(server + "/api/grade", headers={"Sec-Fetch-Site": "same-origin"})
        assert status == 200

    def test_cross_site_referer_on_post_is_rejected(self, server: str) -> None:
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": "x", "code": "print(1)\n"}).encode("utf-8"),
            headers={"Referer": "http://evil.example/attack.html"},
        )
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_matching_referer_on_post_is_allowed(self, server: str, tmp_path: pathlib.Path) -> None:
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"folder": str(tmp_path), "code": "print(1)\n"}).encode("utf-8"),
            headers={"Referer": server + "/"},
        )
        assert status == 200
        assert json.loads(body)["ok"] is True

    def test_index_and_static_are_not_guarded(self, server: str) -> None:
        """`/` и `/static/*` не относятся к `/api/*` — guard их не трогает."""
        status, _ = _get(server + "/", headers={"Origin": "http://evil.example"})
        assert status == 200
        status, _ = _get(server + "/static/app.css", headers={"Host": "evil.example"})
        assert status == 200


# ---------------------------------------------------------------------------
# ?lang= — message catalog locale selection (issue #264)
# ---------------------------------------------------------------------------


class TestApiLangQueryParam:
    """``?lang=en`` translates ``message``; omitting it (or ``?lang=ru``) keeps the
    exact Russian text that was hardcoded before issue #264 — verified byte-for-byte
    against the sentences other tests in this file already assert on."""

    def test_grade_without_path_lang_en(self, server: str) -> None:
        status, body = _get(server + "/api/grade?lang=en")
        assert status == 200
        data = json.loads(body)
        assert data["kind"] == "error"
        assert data["message_id"] == "specify_path_file_or_folder"
        assert data["message"] == "Specify a path to a file or folder."
        assert data["message_params"] == {}

    def test_grade_without_path_default_lang_is_russian(self, server: str) -> None:
        status, body = _get(server + "/api/grade")
        assert status == 200
        data = json.loads(body)
        assert data["message"] == "Укажите путь к файлу или папке."
        assert data["message_id"] == "specify_path_file_or_folder"

    def test_grade_without_path_explicit_lang_ru_matches_default(self, server: str) -> None:
        status, body = _get(server + "/api/grade?lang=ru")
        assert status == 200
        assert json.loads(body)["message"] == "Укажите путь к файлу или папке."

    def test_grade_unknown_lang_falls_back_to_russian(self, server: str) -> None:
        status, body = _get(server + "/api/grade?lang=fr")
        assert status == 200
        assert json.loads(body)["message"] == "Укажите путь к файлу или папке."

    def test_grade_nonexistent_path_lang_en(self, server: str, tmp_path: pathlib.Path) -> None:
        # Путь внутри workspace (tmp_path), но не существующий — confinement
        # (issue #261) не мешает, ошибка именно "path_not_found", не 403.
        missing = tmp_path / "no_such_path.py"
        status, body = _get(server + "/api/grade?lang=en&path=" + urllib.parse.quote(str(missing)))
        assert status == 200
        data = json.loads(body)
        assert data["message_id"] == "path_not_found"
        assert "not found" in data["message"].lower()

    def test_solutions_without_path_lang_en(self, server: str) -> None:
        status, body = _get(server + "/api/solutions?lang=en")
        assert status == 200
        data = json.loads(body)
        assert data["message"] == "Specify a path to a folder."
        assert data["message_id"] == "specify_path_folder"

    def test_source_without_path_lang_en(self, server: str) -> None:
        status, body = _get(server + "/api/source?lang=en")
        assert status == 200
        data = json.loads(body)
        assert data["message"] == "Specify a path to a file."
        assert data["message_id"] == "specify_path_file"

    def test_glossary_missing_card_lang_en(self, server: str) -> None:
        status, body = _get(server + "/api/glossary/does-not-exist?lang=en")
        assert status == 404
        data = json.loads(body)
        assert data["message_id"] == "glossary_card_not_found"
        assert "not found" in data["message"].lower()

    def test_glossary_missing_card_default_lang_is_russian(self, server: str) -> None:
        status, body = _get(server + "/api/glossary/does-not-exist")
        assert status == 404
        data = json.loads(body)
        assert data["message"] == "Карточка не найдена: does-not-exist"

    def test_save_solution_missing_folder_lang_en(self, server: str) -> None:
        status, body = _post(
            server + "/api/save-solution?lang=en",
            json.dumps({"code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 400
        data = json.loads(body)
        assert data["message_id"] == "specify_folder"
        assert data["message"] == "Specify a folder."

    def test_save_solution_missing_folder_default_lang_is_russian(self, server: str) -> None:
        status, body = _post(
            server + "/api/save-solution",
            json.dumps({"code": "print(1)\n"}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message"] == "Укажите папку."


# ---------------------------------------------------------------------------
# POST /api/v1/runs — async job model (issue #262)
# ---------------------------------------------------------------------------


def _poll_run(server: str, run_id: str, *, timeout: float = 15.0) -> dict:
    def _terminal() -> dict | None:
        status, body = _get(server + f"/api/v1/runs/{run_id}")
        assert status == 200
        data = json.loads(body)
        return data if data["status"] in ("done", "error", "cancelled") else None

    data = wait_until(_terminal, timeout=timeout)
    if data is None:
        raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")
    return data


class TestStepikSubmit:
    """POST /api/stepik/submit (issue #683) — отправка решения режима 1 на Stepik.

    Живой сабмит на Stepik не делается (нужен реальный токен); проверяются
    валидация входа и путь «нет токена → stepik_auth_required» на пустом
    workspace без secrets.json.
    """

    def test_empty_code_rejected(self, tmp_path: pathlib.Path, server_factory) -> None:
        url = server_factory(tmp_path)
        status, body = _post(url + "/api/stepik/submit", json.dumps({"code": ""}).encode("utf-8"))
        assert status == 400
        assert json.loads(body)["message_id"] == "stepik_no_code"

    def test_no_step_id_rejected(self, tmp_path: pathlib.Path, server_factory) -> None:
        url = server_factory(tmp_path)
        status, body = _post(
            url + "/api/stepik/submit", json.dumps({"code": "print(1)"}).encode("utf-8")
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "stepik_no_step_id"

    def test_step_id_from_meta_then_auth_required(
        self, tmp_path: pathlib.Path, server_factory
    ) -> None:
        """step_id читается из meta.json; без secrets.json job → stepik_auth_required."""
        task = tmp_path / "task"
        task.mkdir()
        (task / "meta.json").write_text(json.dumps({"step_id": 123}), encoding="utf-8")
        url = server_factory(tmp_path)
        status, body = _post(
            url + "/api/stepik/submit",
            json.dumps({"code": "print(1)", "path": "task"}).encode("utf-8"),
        )
        assert status == 202
        data = _poll_run(url, json.loads(body)["run_id"])
        assert data["status"] == "error"
        assert data["message_id"] == "stepik_auth_required"

    def test_explicit_step_id_queues_job(self, tmp_path: pathlib.Path, server_factory) -> None:
        url = server_factory(tmp_path)
        status, body = _post(
            url + "/api/stepik/submit",
            json.dumps({"code": "print(1)", "step_id": 123}).encode("utf-8"),
        )
        assert status == 202
        data = _poll_run(url, json.loads(body)["run_id"])
        assert data["status"] == "error"
        assert data["message_id"] == "stepik_auth_required"


class TestRunsApiBackPressure:
    """issue #429 — POST /api/v1/runs отвечает 429 при превышении лимита
    активных job'ов; после их завершения снова принимает (202)."""

    def test_over_limit_returns_429_then_recovers(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Сервер живёт в этом же процессе (отдельный поток) — monkeypatch модуля
        # runs виден серверному потоку. Свежий реестр + лимит 1 для изоляции.
        monkeypatch.setattr(runs, "_JOBS", {})
        monkeypatch.setattr(runs, "CONFIG", dataclasses.replace(runs.CONFIG, max_active_runs=1))
        release = threading.Event()

        def _blocking(job: runs.Job, *args: object, **kwargs: object) -> None:
            with job.lock:
                job.status = "running"
            release.wait(10)
            with job.lock:
                job.status = "done"

        monkeypatch.setattr(runs, "_run_job", _blocking)

        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        payload = json.dumps({"path": str(sol), "mode": "tests"}).encode("utf-8")

        s1, b1 = _post(server + "/api/v1/runs", payload)
        assert s1 == 202  # первый — в пределах лимита
        run1 = json.loads(b1)["run_id"]

        s2, b2 = _post(server + "/api/v1/runs", payload)
        assert s2 == 429  # второй сверх лимита
        data = json.loads(b2)
        assert data["kind"] == "error"
        assert data["message_id"] == "too_many_runs"

        # Освобождаем первый → лимит снова доступен → 202.
        release.set()
        _poll_run(server, run1)
        s3, b3 = _post(server + "/api/v1/runs", payload)
        assert s3 == 202
        _poll_run(server, json.loads(b3)["run_id"])


class TestRunsApiGoldenComparison:
    """Acceptance criterion: create -> poll until done -> result equals the
    old /api/grade result on the same input. Compared STRUCTURALLY (verdict/
    key-set/row-count), not byte-for-byte -- min/median are independent
    subprocess timings from two separate runs and will differ numerically."""

    def test_bench_matches_sync_grade(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")

        sync_status, sync_body = _get(
            server + "/api/grade?mode=bench&repeats=2&path=" + urllib.parse.quote(str(sol))
        )
        assert sync_status == 200
        sync_data = json.loads(sync_body)

        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {"repeats": 2}}).encode(
                "utf-8"
            ),
        )
        assert create_status == 202
        run_id = json.loads(create_body)["run_id"]
        async_data = _poll_run(server, run_id)["result"]

        assert async_data["mode"] == sync_data["mode"] == "bench"
        assert async_data["kind"] == sync_data["kind"]
        assert {r["file"]: r["verdict"] for r in async_data["rows"]} == {
            r["file"]: r["verdict"] for r in sync_data["rows"]
        }

    def test_microbench_matches_sync_grade(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")

        sync_status, sync_body = _get(
            server + "/api/grade?mode=microbench&number=50&path=" + urllib.parse.quote(str(sol))
        )
        assert sync_status == 200
        sync_data = json.loads(sync_body)

        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "microbench", "params": {"number": 50}}).encode(
                "utf-8"
            ),
        )
        assert create_status == 202
        run_id = json.loads(create_body)["run_id"]
        async_data = _poll_run(server, run_id)["result"]

        assert async_data["mode"] == sync_data["mode"] == "microbench"
        assert {r["file"]: r["verdict"] for r in async_data["rows"]} == {
            r["file"]: r["verdict"] for r in sync_data["rows"]
        }

    def test_tests_run_honors_per_run_timeout_limit(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        """issue #641: limits.timeout_s из тела POST /api/v1/runs доходит до RunSpec.

        Дискриминирующий тест: решение спит 3 c. Под дефолтным timeout (10 c) оно
        успело бы и дало AC; per-run ``timeout_s=0.5`` режет его в TLE. Значит
        именно лимит из API применён (server → runs → grade → RunSpec), а не
        глобальный CONFIG.
        """
        sol = _make_task(tmp_path, "import time\ntime.sleep(3)\nprint(int(input()) + 1)\n")

        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "tests", "limits": {"timeout_s": 0.5}}).encode(
                "utf-8"
            ),
        )
        assert create_status == 202
        run_id = json.loads(create_body)["run_id"]

        result = _poll_run(server, run_id)["result"]
        verdicts = [c["verdict"] for row in result["rows"] for c in row["cases"]]
        assert verdicts == ["TLE"], result


class TestRunsApiCancel:
    def test_cancel_stops_child_process(self, server: str, tmp_path: pathlib.Path) -> None:
        """Cancellation must actually kill the child, not just flip a status
        string -- the PID-file + psutil.pid_exists() check is the
        load-bearing part of this test."""
        import psutil

        pidfile = tmp_path / "child.pid"
        sol = tmp_path / "task.py"
        sol.write_text(
            "import os, pathlib, time\n"
            f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(30)\n"
            "print(input())\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "input_1.txt").write_text("4\n", encoding="utf-8")
        (tests_dir / "expected_1.txt").write_text("5\n", encoding="utf-8")

        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {"repeats": 5}}).encode(
                "utf-8"
            ),
        )
        assert create_status == 202
        run_id = json.loads(create_body)["run_id"]

        assert wait_until(pidfile.exists, timeout=10.0), "child process never started"
        pid = int(pidfile.read_text().strip())

        cancel_status, cancel_body = _post(server + f"/api/v1/runs/{run_id}/cancel", b"")
        assert cancel_status == 200
        assert json.loads(cancel_body)["status"] in ("running", "cancelled")

        data = _poll_run(server, run_id)
        assert data["status"] == "cancelled"
        assert data["message_id"] == "run_cancelled"

        # Best-effort: wait for the OS to actually reap the killed process.
        assert wait_until(lambda: not psutil.pid_exists(pid), timeout=3.0)

    def test_cancel_unknown_run_is_404(self, server: str) -> None:
        status, body = _post(server + "/api/v1/runs/no-such-id/cancel", b"")
        assert status == 404
        assert json.loads(body)["message_id"] == "run_not_found"

    def test_cancel_already_done_run_is_idempotent_ok(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {"repeats": 1}}).encode(
                "utf-8"
            ),
        )
        run_id = json.loads(create_body)["run_id"]
        _poll_run(server, run_id)

        status, body = _post(server + f"/api/v1/runs/{run_id}/cancel", b"")
        assert status == 200
        assert json.loads(body)["status"] == "done"

    def test_cancel_already_cancelled_run_is_idempotent_ok(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        """Повторный cancel уже отменённого run'а — 200, не ошибка (issue #296)."""
        sol = _make_task(tmp_path, "import time\ntime.sleep(30)\nprint(input())\n")
        create_status, create_body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {"repeats": 20}}).encode(
                "utf-8"
            ),
        )
        run_id = json.loads(create_body)["run_id"]

        _post(server + f"/api/v1/runs/{run_id}/cancel", b"")
        _poll_run(server, run_id)

        status, body = _post(server + f"/api/v1/runs/{run_id}/cancel", b"")
        assert status == 200
        assert json.loads(body)["status"] == "cancelled"


class TestRunsApiConcurrency:
    def test_two_parallel_runs_do_not_mix_results(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        sol_a = _make_task(dir_a, "print(int(input()) + 1)\n")
        sol_b = _make_task(dir_b, "raise ValueError('boom')\n")

        _, body_a = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol_a), "mode": "bench", "params": {"repeats": 2}}).encode(
                "utf-8"
            ),
        )
        _, body_b = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol_b), "mode": "bench", "params": {"repeats": 2}}).encode(
                "utf-8"
            ),
        )
        run_id_a = json.loads(body_a)["run_id"]
        run_id_b = json.loads(body_b)["run_id"]
        assert run_id_a != run_id_b

        data_a = _poll_run(server, run_id_a)
        data_b = _poll_run(server, run_id_b)

        assert data_a["status"] == "done"
        assert data_a["result"]["rows"][0]["verdict"] != "ERR"
        assert data_b["status"] == "done"
        assert data_b["result"]["rows"][0]["verdict"] == "ERR"


class TestRunsApiPathConfinement:
    def test_path_outside_root_is_403(
        self, server: str, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        outside = tmp_path_factory.mktemp("outside")
        sol = _make_task(outside, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {}}).encode("utf-8"),
        )
        assert status == 403
        assert json.loads(body)["kind"] == "error"


class TestRunsApiValidation:
    def test_missing_path_is_400(self, server: str) -> None:
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"mode": "bench", "params": {}}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "specify_path_file_or_folder"

    def test_invalid_mode_is_400(self, server: str, tmp_path: pathlib.Path) -> None:
        # "tests"/"bench"/"microbench" валидны (issue #297 добавил "tests");
        # берём заведомо неизвестный режим.
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bogus", "params": {}}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "invalid_run_mode"

    def test_mode_missing_is_400(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol)}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "invalid_run_mode"

    def test_repeats_clamped_to_range(self, server: str, tmp_path: pathlib.Path) -> None:
        # A real 1000-repeat run takes tens of seconds (1000 real
        # subprocesses) -- clamping is proven by progress.total as soon as
        # it's computed, without waiting for the whole job to finish, then
        # cancelled to avoid burning the rest of the test run on it.
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {"repeats": 999_999}}).encode(
                "utf-8"
            ),
        )
        assert status == 202
        run_id = json.loads(body)["run_id"]

        def _total_known() -> int | None:
            _, body = _get(server + f"/api/v1/runs/{run_id}")
            total = json.loads(body)["progress"]["total"]
            return total if total > 0 else None

        total = wait_until(_total_known, timeout=10.0)
        # 1 case * clamped repeats (max 1000) -- not 1 * 999_999.
        assert total == 1000

        _post(server + f"/api/v1/runs/{run_id}/cancel", b"")

    def test_body_too_large_is_413(self, server: str) -> None:
        huge = json.dumps({"path": "x" * (2 * 1024 * 1024), "mode": "bench"}).encode("utf-8")
        status, body = _post(server + "/api/v1/runs", huge)
        assert status == 413
        assert json.loads(body)["message_id"] == "body_too_large"


class TestRunsApiNotFound:
    def test_get_unknown_run_is_404(self, server: str) -> None:
        status, body = _get(server + "/api/v1/runs/no-such-id")
        assert status == 404
        assert json.loads(body)["message_id"] == "run_not_found"


class TestRunsApiHostGuard:
    def test_create_run_wrong_host_is_403(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "bench", "params": {}}).encode("utf-8"),
            headers={"Host": "evil.example.com"},
        )
        assert status == 403
        assert json.loads(body)["message_id"] == "invalid_host"


class TestRunsApiInlineCode:
    def test_code_field_graded_instead_of_on_disk_content(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        sol = _make_task(tmp_path, "print(999)\n")  # wrong on disk
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps(
                {
                    "path": str(sol),
                    "code": "print(int(input()) + 1)\n",
                    "mode": "bench",
                    "params": {"repeats": 1},
                }
            ).encode("utf-8"),
        )
        assert status == 202
        run_id = json.loads(body)["run_id"]
        data = _poll_run(server, run_id)
        assert data["status"] == "done"
        assert data["result"]["rows"][0]["verdict"] != "ERR"


class TestRunsApiMode1Tests:
    """issue #297 — режим 1 (корректность) через POST /api/v1/runs с code в теле."""

    def _create(self, server: str, path: str, code: str) -> str:
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": path, "code": code, "mode": "tests"}).encode("utf-8"),
        )
        assert status == 202, body
        return json.loads(body)["run_id"]

    def test_tests_mode_grades_correctness_from_body(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = _poll_run(server, self._create(server, str(sol), "print(int(input()) + 1)\n"))
        assert data["status"] == "done"
        assert data["result"]["mode"] == "tests"
        assert data["result"]["rows"][0]["status"] == "OK"

    def test_grade_does_not_write_target_file(self, server: str, tmp_path: pathlib.Path) -> None:
        """AC1: проверка режима 1 не пишет в целевой файл."""
        sol = _make_task(tmp_path, "print(999)\n")  # неверно на диске
        original = sol.read_text(encoding="utf-8")
        data = _poll_run(server, self._create(server, str(sol), "print(int(input()) + 1)\n"))
        assert data["status"] == "done"
        assert data["result"]["rows"][0]["status"] == "OK"  # тело верное
        assert sol.read_text(encoding="utf-8") == original  # диск не тронут
        assert {p.name for p in tmp_path.glob("*.py")} == {"task.py"}  # temp убран

    def test_two_windows_grade_without_clobbering_each_other(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        """AC2: две последовательные проверки разного кода на одной папке не
        затирают ни файл, ни результат друг друга — потому что «Проверить»
        вообще не пишет на диск."""
        sol = _make_task(tmp_path, "print(0)\n")  # исходный (неверный) на диске
        disk_before = sol.read_text(encoding="utf-8")

        # «Окно A» проверяет верный код, «окно B» — падающий; оба на том же path.
        data_a = _poll_run(server, self._create(server, str(sol), "print(int(input()) + 1)\n"))
        data_b = _poll_run(server, self._create(server, str(sol), "raise ValueError('boom')\n"))

        assert data_a["result"]["rows"][0]["status"] == "OK"
        assert data_b["result"]["rows"][0]["status"] == "FAIL"  # RE
        # Ни одна проверка не записала свой код в целевой файл.
        assert sol.read_text(encoding="utf-8") == disk_before
        assert {p.name for p in tmp_path.glob("*.py")} == {"task.py"}


# ---------------------------------------------------------------------------
# Client-side esc() — HTML-attribute hardening (issue #214)
# ---------------------------------------------------------------------------
#
# esc() is embedded JS (no JS runtime in this Python test suite), so these are
# source-level regression checks: they pin down the escape table/regex that the
# glossary card relies on when inserting card.docs_url into href="...". A quote
# character reaching that attribute unescaped would let it be broken out of.
#
# issue #125: the JS moved from an inline <script> in _INDEX_HTML to its own
# static/app.js file; issue #426 split it into ES modules, so these source-level
# regressions grep web._STATIC_JS_SOURCES (all static/*.js concatenated) — a
# pinned pattern may live in any module now, not just app.js.


def _ht_table_source() -> str:
    start = web._STATIC_JS_SOURCES.index("const HT = {")
    end = web._STATIC_JS_SOURCES.index("};", start)
    return web._STATIC_JS_SOURCES[start:end]


def test_client_esc_table_covers_html_and_attribute_special_chars() -> None:
    table_src = _ht_table_source()
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert f'"{entity}"' in table_src, f"{entity!r} missing from client-side HT map"


def test_client_esc_regex_includes_quote_chars() -> None:
    # The replace() char class must include both quote characters, or esc()
    # would keep stripping only &/</> and leave href="...' open to breakout.
    assert "replace(/[&<>\"']/g" in web._STATIC_JS_SOURCES


# --- issue #633: обратная связь на действия -------------------------------


def test_toast_primitive_is_exported_and_mounted() -> None:
    """Тост-примитив есть в JS, а контейнер под него — в разметке."""
    assert "function toast(" in web._STATIC_JS_SOURCES
    assert 'id="toast-stack"' in web._INDEX_HTML


def test_clipboard_errors_are_no_longer_swallowed() -> None:
    """Регрессия #633: копирование молчало — ошибка глушилась пустым catch.

    Пустой ``.catch(() => {})`` делал успех и провал неотличимыми: пользователь
    не знал, попал ли текст в буфер.
    """
    start = web._STATIC_JS_SOURCES.index("function copyToClipboard(")
    end = web._STATIC_JS_SOURCES.index("\n}", start)
    body = web._STATIC_JS_SOURCES[start:end]

    # Именно в этой функции пустой catch недопустим. Fire-and-forget отмена
    # прогона (grade.js/sandbox.js) — легитимный случай и под проверку не идёт.
    assert ".catch(() => {})" not in body
    assert 'toast(t("common.copied"), "success")' in body
    assert 'toast(t("common.copy_failed"), "error")' in body


# --- issue #637: a11y и клавиатурные потоки ---------------------------------


def test_consent_modal_traps_focus_and_restores_it() -> None:
    """Модалка согласия удерживает фокус и возвращает его при закрытии (#637).

    `aria-modal` в разметке — лишь обещание скринридеру, сам атрибут ничего не
    удерживает: `Tab` свободно уходил на страницу под оверлеем, где можно было
    нажимать кнопки, пока диалог «ждёт» ответа. Плюс `Escape`: диалог с двумя
    вариантами обязан закрываться отказом.
    """
    start = web._STATIC_JS_SOURCES.index("function _requestAiConsent(")
    body = web._STATIC_JS_SOURCES[start : web._STATIC_JS_SOURCES.index("\n}", start)]

    assert 'e.key === "Escape"' in body, "Escape должен закрывать диалог отказом"
    assert 'e.key !== "Tab"' in body, "нужен перехват Tab на краях"
    assert "returnFocus" in body, "фокус обязан вернуться туда, откуда открыли"


def test_waiting_states_share_one_skeleton_language() -> None:
    """Ожидание везде показывается скелетоном, а не смесью скелетон/текст (#637).

    Раньше грейд и загрузчик рисовали скелетон, а список решений и песочница —
    текстовые заглушки. Текст не выброшен, а спрятан в `sr-only`: скелетон сам
    по себе декоративен, и незрячий пользователь остался бы без сигнала.
    """
    src = web._STATIC_JS_SOURCES
    assert "function skeletonWithLabel(" in src
    assert "function skeletonListItems(" in src
    assert "sr-only" in src and 'role="status"' in src

    # Текстовые заглушки ожидания заменены на скелетоны.
    assert 'skeletonListItems(t("check.searching"))' in src
    assert "skeletonWithLabel(busyMsg)" in src


def test_check_shortcuts_do_not_fire_while_typing() -> None:
    """Цифры-режимы не срабатывают, пока пользователь печатает (#637).

    Иначе ввод «2 3» в поле stdin или в редакторе переключал бы режим прямо
    под руками. Редактор кода — contenteditable (CodeMirror), поэтому одной
    проверки на input/textarea мало.
    """
    src = web._STATIC_JS_SOURCES
    assert "function _isTyping(" in src
    assert "isContentEditable" in src, "CodeMirror — contenteditable, его надо учесть"
    assert "!_isTyping(e.target)" in src

    # Ctrl+S обязан перехватываться, иначе браузер откроет «Сохранить страницу».
    start = src.index("_MODE_BY_DIGIT")
    body = src[start : start + 2000]
    assert "e.preventDefault()" in body


# --- issue #636: инлайн-diff разбора WA -------------------------------------


def test_wa_detail_uses_aligned_inline_diff() -> None:
    """Разбор WA показывает выровненный diff, а не два блока + сырую строку.

    Раньше «Ожидалось/Получено» рендерились двумя `codeBlock` плюс необработанный
    вывод difflib — искать различие приходилось глазами.
    """
    src = web._STATIC_JS_SOURCES
    assert "function renderInlineDiff(" in src
    assert "renderInlineDiff(c.expected, c.actual)" in src
    # Сырой diff остаётся, но свёрнутым — он больше не основной способ понять WA.
    assert "details class='raw-diff'" in src


def test_diff_marks_only_ambiguous_whitespace_on_full_lines() -> None:
    """Точками помечаются табы и ХВОСТОВЫЕ пробелы, а не каждый пробел.

    Регрессия из разработки #636: первая версия прогоняла всю строку через
    пометку всех пробелов, и обычный текст читался как «extra·line». Межсловный
    пробел ничем не примечателен, а хвостовой невидим и регулярно оказывается
    причиной WA — метить нужно только его. Внутри различающегося фрагмента
    помечаются все пробелы: там пробел и ЕСТЬ различие.
    """
    src = web._STATIC_JS_SOURCES
    assert "function escWithHiddenMarks(" in src

    start = src.index("function escWithHiddenMarks(")
    body = src[start : src.index("\n}", start)]
    assert "[ \\t]+$" in body, "должен вычленяться именно хвостовой пробел"

    # Целые строки (совпавшая / лишняя / недостающая) — через «щадящую» пометку.
    for call in (
        'escWithHiddenMarks(a), t("grade.diff_line_extra")',
        'escWithHiddenMarks(e), t("grade.diff_line_missing")',
    ):
        assert call in src, call
    # А внутри подсвеченного фрагмента — полная пометка.
    assert 'visibleWhitespace(mid) + "</mark>"' in src


# --- issue #659: топбар — язык и состояние темы ----------------------------


def test_language_switch_lives_in_topbar_not_settings() -> None:
    """Язык переключается тумблером в topbar, а не селектом в «Настройках».

    Раздел «Настройки» состоял почти целиком из дублей topbar-тумблеров; язык
    при этом был спрятан именно там, хотя переключают его часто.
    """
    assert 'id="lang-switch"' in web._INDEX_HTML
    assert 'data-lang="ru"' in web._INDEX_HTML
    assert 'data-lang="en"' in web._INDEX_HTML

    # Селекты-дубли убраны вместе с их обработчиками.
    assert 'id="settings-theme"' not in web._INDEX_HTML
    assert 'id="settings-lang"' not in web._INDEX_HTML
    assert "settings-lang" not in web._STATIC_JS_SOURCES
    assert "settings-theme" not in web._STATIC_JS_SOURCES


def test_theme_button_label_names_the_current_mode() -> None:
    """Подпись кнопки темы называет текущий режим (issue #659).

    Иконка режим отражала и раньше, но «системная» на глаз неотличима от
    остальных, а скринридер видел статичное «Переключить тему». Состояние
    показывал селект в «Настройках» — он убран, поэтому режим обязан читаться
    с самой кнопки.

    Ключи проверяются как явная карта: guardrail локалей разбирает вызовы
    переводчика статически, и склейка ключа с переменной дала бы ему обрубок
    префикса вместо трёх реальных ключей.
    """
    assert "THEME_STATE_KEYS" in web._STATIC_JS_SOURCES
    for mode in ("system", "light", "dark"):
        assert f'"topbar.theme_state_{mode}"' in web._STATIC_JS_SOURCES

    catalog = json.loads(
        (pathlib.Path(web.__file__).parent / "static" / "locales" / "ui.json").read_text(
            encoding="utf-8"
        )
    )
    for lang in ("ru", "en"):
        for mode in ("system", "light", "dark"):
            assert catalog[lang][f"topbar.theme_state_{mode}"]


def test_theme_label_is_restored_after_language_switch() -> None:
    """После смены языка подпись темы возвращается к фактическому режиму.

    На кнопке висит `data-i18n` с ключом СИСТЕМНОГО состояния — он нужен, пока
    JS не стартовал. Но `applyUiLocale` при смене языка переписывает атрибуты
    по разметке, и без повторного `applyTheme()` подпись соврала бы: показывала
    бы «системная» при включённой светлой или тёмной теме.
    """
    start = web._STATIC_JS_SOURCES.index("function setLang(")
    body = web._STATIC_JS_SOURCES[start : web._STATIC_JS_SOURCES.index("\n}", start)]
    assert "applyUiLocale(value)" in body
    assert "applyTheme()" in body, "после перелокализации разметки подпись темы должна обновиться"


def test_sections_registry_lists_every_sidebar_section() -> None:
    """Реестр SECTIONS совпадает с разделами sidebar (issue #317/#428/#538).

    Регрессия, которую это стережёт: раньше список разделов дублировался в
    коде жёстко, и `rules`/`insights`/`settings` из него выпадали — раздел
    существовал в навигации, но переключение по реестру его не видело.

    До #658 зону охранял e2e-тест циклического переключения через палитру
    команд. Палитра удалена, и сам цикл перестал быть достижимым (после смены
    раздела панель с карточкой действий скрывается), поэтому полнота реестра
    проверяется здесь — дёшево и точнее, чем через браузер. Достижимость самих
    разделов проверяет e2e `test_all_sections_reachable_from_sidebar`.
    """
    match = re.search(r"const SECTIONS = \[(.*?)\]", web._STATIC_JS_SOURCES, re.S)
    assert match, "реестр SECTIONS не найден в static/*.js"
    registry = re.findall(r'"([a-z]+)"', match.group(1))

    # Только пункты навигации: `data-section` встречается ещё и в пустых
    # состояниях («Откройте раздел „Проверка решений“»), их считать нельзя.
    nav = re.search(r'<nav class="sidebar".*?</nav>', web._INDEX_HTML, re.S)
    assert nav, "sidebar-навигация не найдена в разметке"
    sidebar = re.findall(r'data-section="([a-z]+)"', nav.group(0))

    assert registry == sidebar, (
        f"реестр и sidebar разошлись: в реестре {registry}, в разметке {sidebar}"
    )


# --- issue #635: загрузка .py перетаскиванием ------------------------------


def test_editor_accepts_dropped_python_file() -> None:
    """Перетаскивание .py грузит содержимое в редактор (issue #635)."""
    src = web._STATIC_JS_SOURCES
    assert "function wireEditorFileDrop(" in src
    assert "function loadDroppedFile(" in src
    # Содержимое читается и кладётся в редактор, а не пытается стать путём.
    assert "readAsText(file" in src
    assert "setEditorCode(String(reader.result" in src
    # Не-Python отклоняется явным сообщением, а не молча.
    assert 'endsWith(".py")' in src
    assert 'toast(t("check.drop_not_python"' in src


def test_file_drop_is_captured_before_codemirror() -> None:
    """Слушатели drop/dragover — на фазе capture (issue #635).

    CodeMirror обрабатывает drop сам: без перехвата он вставил бы имя файла как
    текст. При этом перетаскивание ТЕКСТА должно продолжать работать штатно,
    поэтому обработчик выходит, если файлов в переносе нет.
    """
    body = _js_fn_body("wireEditorFileDrop")
    assert body.count("true,") >= 2, "drop/dragover должны слушаться на capture"
    assert "if (!hasFiles(e)) return;" in body


def test_drop_does_not_pretend_to_set_path() -> None:
    """Регрессия #635: подставить путь брошенного файла браузер не даёт.

    `dataTransfer` отдаёт `File` только с именем — абсолютного пути там нет
    (граница безопасности). Поэтому обработчик не трогает поле пути, а
    сообщение честно просит указать папку с задачей: без неё сервер не найдёт
    `tests/`. Если кто-то попробует «починить» это присваиванием в #path,
    тест упадёт.
    """
    body = _js_fn_body("loadDroppedFile")
    assert '$("#path").value =' not in body

    catalog = json.loads(
        (pathlib.Path(web.__file__).parent / "static" / "locales" / "ui.json").read_text(
            encoding="utf-8"
        )
    )
    assert "{name}" in catalog["ru"]["check.drop_loaded"]
    assert "папку" in catalog["ru"]["check.drop_loaded"]


# --- issue #634: переходы вместо телепортации ------------------------------


def _js_fn_body(name: str) -> str:
    """Тело JS-функции из конкатенации static/*.js — для source-регрессий."""
    start = web._STATIC_JS_SOURCES.index(f"function {name}(")
    return web._STATIC_JS_SOURCES[start : web._STATIC_JS_SOURCES.index("\n}", start)]


def test_section_and_tab_switching_go_through_motion_helper() -> None:
    """Регрессия #634: разделы и вкладки переключались мгновенным `.hidden`.

    `hidden` — это `display: none`, его нельзя анимировать переходом, поэтому
    появление должно идти через общий помощник с CSS-анимацией. Прямое
    присваивание `.hidden` вернуло бы телепортацию.
    """
    assert "function revealWithMotion(" in web._STATIC_JS_SOURCES

    section_body = _js_fn_body("setSection")
    assert ".hidden = section !== s" not in section_body
    assert "revealWithMotion(" in section_body

    tab_body = _js_fn_body("setResultTab")
    assert ".hidden = tab !==" not in tab_body
    assert "revealWithMotion(" in tab_body


def test_mode_buttons_are_equal_width_and_fit_their_labels() -> None:
    """Регрессия #663: подпись «Микробенчмарк» вылезала за границы кнопки.

    Раньше раскладка была на flex: `flex: 1` разворачивается в `flex-basis: 0`
    (ширина делится поровну независимо от содержимого), а явный `min-width` в
    пикселях отключал `min-width: auto`, который обычно не даёт flex-элементу
    сжаться уже контента. Кнопка получала 101px при потребности в 122px и при
    `overflow: visible` наезжала на «Запустить».

    Grid закрывает обе задачи разом: `1fr` — это `minmax(auto, 1fr)`, поэтому
    колонки равны между собой И не уже самой длинной подписи. Flex так не
    умеет: там «равные» и «не уже контента» противоречат друг другу.
    """
    css = (pathlib.Path(web.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    row_start = css.index(".mode-row {")
    row_rule = css[row_start : css.index("}", row_start)]

    assert "display: grid" in row_rule
    assert "repeat(4, 1fr)" in row_rule, (
        "колонки должны быть равными (1fr), иначе ширины кнопок разъедутся"
    )

    btn_start = css.index(".mode-btn {")
    btn_rule = css[btn_start : css.index("}", btn_start)]
    assert not re.search(r"min-width:\s*\d+px", btn_rule), (
        "фиксированный min-width в пикселях уже ломал раскладку — длинная "
        "подпись снова вылезет за кнопку (#663)"
    )

    # На узком экране четыре колонки по ширине «Микробенчмарка» не помещаются.
    assert re.search(r"\.mode-row\s*\{\s*grid-template-columns:\s*repeat\(2, 1fr\)", css), (
        "нужен мобильный брейкпоинт 2×2, иначе на 375px появится горизонтальный скролл"
    )


def test_view_enter_animation_reuses_shared_motion_token() -> None:
    """Длительность/кривая входа — из общего токена, а не хардкод.

    До #634 `--transition-interactive` была объявлена, но почти не
    использовалась; смысл правки — задействовать её, а не завести дубль.
    """
    css = (pathlib.Path(web.__file__).parent / "static" / "app.css").read_text(encoding="utf-8")
    assert "@keyframes view-enter" in css
    assert "animation: view-enter var(--transition-interactive)" in css
    # Глобальный prefers-reduced-motion гасит анимации через !important —
    # пер-компонентные блоки не нужны и не должны заводиться заново.
    assert "animation-duration: 0.01ms !important" in css


def test_save_errors_go_to_inline_slot_not_results_panel() -> None:
    """Регрессия #633: ошибка сохранения затирала панель результатов #out.

    Панель показывает результаты грейда (терять их незачем), а на вкладке
    «Разбор» она вообще скрыта — сообщение уходило в никуда. Теперь ошибка
    печатается рядом с кнопкой «Сохранить».
    """
    assert 'id="save-error"' in web._INDEX_HTML
    assert "function showSaveError(" in web._STATIC_JS_SOURCES
    # saveSolution больше не пишет ошибки в #out
    start = web._STATIC_JS_SOURCES.index("async function saveSolution(")
    end = web._STATIC_JS_SOURCES.index("function showSaveError(", start)
    assert '$("#out").innerHTML' not in web._STATIC_JS_SOURCES[start:end]


# ---------------------------------------------------------------------------
# Client-side a11y — source-level regression checks (issue #298)
# ---------------------------------------------------------------------------
#
# No JS runtime in this suite (the live behaviour is covered by
# tests/e2e/test_journeys.py); these pin the source so the a11y affordances
# can't silently regress.


def test_render_verdict_emits_verdict_text_not_colour_only() -> None:
    """WCAG 1.4.1: verdict badge must carry the verdict string as text, not
    convey meaning by colour class alone -- renderVerdict() interpolates esc(v)
    into the span body."""
    start = web._STATIC_JS_SOURCES.index("function renderVerdict(")
    end = web._STATIC_JS_SOURCES.index("}", start)
    body = web._STATIC_JS_SOURCES[start:end]
    assert "esc(v)" in body, "renderVerdict no longer inlines the verdict text"


def test_progress_bar_has_progressbar_role_and_aria_values() -> None:
    """issue #298: the progress bar markup exposes role=progressbar + aria-value*."""
    assert 'role="progressbar"' in web._STATIC_JS_SOURCES
    assert "aria-valuemin=" in web._STATIC_JS_SOURCES
    assert "aria-valuemax=" in web._STATIC_JS_SOURCES
    assert "aria-valuenow=" in web._STATIC_JS_SOURCES


def test_result_announce_live_region_present() -> None:
    """issue #298: a polite aria-live region exists for the result summary."""
    assert 'id="result-announce"' in web._INDEX_HTML
    assert 'aria-live="polite"' in web._INDEX_HTML


def test_error_card_link_is_internal_deep_link() -> None:
    # issue #684: карточка ошибки ведёт в СВОЙ раздел «Глоссарий»
    # (#/glossary/<anchor>), а не во внешнюю витрину-копию.
    # issue #214: якорь проходит через encodeURIComponent, поэтому не может
    # разорвать href="..." -- если правка вставит g.anchor напрямую, тест упадёт.
    assert "#/glossary/' + encodeURIComponent(g.anchor)" in web._STATIC_JS_SOURCES
    assert "artvsmark.github.io" not in web._STATIC_JS_SOURCES


# ---------------------------------------------------------------------------
# run_server(sandbox=...) — проброс OS-песочницы в web (issue #396)
# ---------------------------------------------------------------------------


class TestRunServerSandbox:
    def test_sandbox_true_sets_sandbox_runner_before_serving(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """issue #396: run_server(sandbox=True) ставит SandboxRunner активным
        grader_core._RUNNER ДО старта — grade/playground/microbench/trace идут
        через него, поэтому изолируются разом."""
        import stepik_grader.core.sandbox as sandbox_mod
        from stepik_grader.core import grader_core
        from stepik_grader.web import server as server_mod

        class _FakeSandboxRunner:
            pass

        class _FakeServer:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def serve_forever(self) -> None:
                raise KeyboardInterrupt  # немедленно завершаем run_server

            def server_close(self) -> None:
                pass

        monkeypatch.setattr(sandbox_mod, "SandboxRunner", _FakeSandboxRunner)
        monkeypatch.setattr(server_mod, "_GraderServer", _FakeServer)

        original = grader_core._RUNNER
        try:
            server_mod.run_server(port=0, sandbox=True)
            assert isinstance(grader_core._RUNNER, _FakeSandboxRunner)
        finally:
            grader_core.set_runner(original)

    def test_sandbox_unavailable_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SandboxUnavailableError (нет backend'а) пробрасывается вызывающему
        (CLI → parser.error), сервер не стартует — без молчаливого отката."""
        import stepik_grader.core.sandbox as sandbox_mod
        from stepik_grader.core.sandbox import SandboxUnavailableError
        from stepik_grader.web import server as server_mod

        def _raise() -> None:
            raise SandboxUnavailableError("no backend")

        monkeypatch.setattr(sandbox_mod, "SandboxRunner", _raise)
        with pytest.raises(SandboxUnavailableError):
            server_mod.run_server(port=0, sandbox=True)


# ---------------------------------------------------------------------------
# Браузерный OAuth в --serve — /api/auth/status + /api/auth/start (issue #402)
# ---------------------------------------------------------------------------


class TestAuthApi:
    def test_status_no_secrets(self, server: str) -> None:
        status, body = _get(server + "/api/auth/status")
        assert status == 200
        assert json.loads(body) == {"authorized": False, "reason": "no_secrets"}

    def test_status_ok_with_valid_token(self, server: str, tmp_path: pathlib.Path) -> None:
        import time

        (tmp_path / "secrets.json").write_text(
            json.dumps(
                {
                    "client_id": "a",
                    "client_secret": "b",
                    "redirect_uri": "c",
                    "access_token": "tok",
                    "expires_at": time.time() + 3600,
                }
            ),
            encoding="utf-8",
        )
        status, body = _get(server + "/api/auth/status")
        assert status == 200
        assert json.loads(body) == {"authorized": True, "reason": "ok"}

    def test_start_requires_creds(self, server: str) -> None:
        status, body = _post(
            server + "/api/auth/start",
            json.dumps({"client_id": "only-id"}).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "specify_oauth_creds"

    def test_start_wrong_host_is_403(self, server: str) -> None:
        status, body = _post(
            server + "/api/auth/start",
            json.dumps({"client_id": "a", "client_secret": "b"}).encode("utf-8"),
            headers={"Host": "evil.example.com"},
        )
        assert status == 403
        assert json.loads(body)["message_id"] == "invalid_host"

    def test_start_rejects_non_loopback_redirect(self, server: str) -> None:
        """issue #402 (harden): redirect_uri с не-loopback host → 400, callback
        не биндится на все интерфейсы."""
        status, body = _post(
            server + "/api/auth/start",
            json.dumps(
                {
                    "client_id": "a",
                    "client_secret": "b",
                    "redirect_uri": "http://0.0.0.0:8080/callback",
                }
            ).encode("utf-8"),
        )
        assert status == 400
        assert json.loads(body)["message_id"] == "invalid_redirect_uri"

    def test_start_runs_flow_and_writes_secrets(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /api/auth/start → async-job → браузерный flow (мок) → done; креды
        записаны в secrets.json, реальный браузер не открывается."""
        from stepik_grader.web import auth_adapter

        captured: dict[str, object] = {}

        def _fake_authorize(client_id, client_secret, redirect_uri, secrets_path):
            captured["args"] = (client_id, client_secret, redirect_uri)
            return {"access_token": "t"}

        monkeypatch.setattr(auth_adapter, "authorize_and_get_token", _fake_authorize)
        status, body = _post(
            server + "/api/auth/start",
            json.dumps({"client_id": "cid", "client_secret": "csec"}).encode("utf-8"),
        )
        assert status == 202
        run_id = json.loads(body)["run_id"]
        data = _poll_run(server, run_id)
        assert data["status"] == "done", data
        assert data["result"] == {"authorized": True, "reason": "ok"}
        assert captured["args"] == ("cid", "csec", auth_adapter.DEFAULT_REDIRECT_URI)
        secrets = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
        assert secrets["client_id"] == "cid"
        assert secrets["client_secret"] == "csec"

    def test_status_no_token_over_http(self, server: str, tmp_path: pathlib.Path) -> None:
        """Креды есть, токена нет → no_token через GET /api/auth/status."""
        (tmp_path / "secrets.json").write_text(
            json.dumps({"client_id": "a", "client_secret": "b", "redirect_uri": "c"}),
            encoding="utf-8",
        )
        status, body = _get(server + "/api/auth/status")
        assert status == 200
        assert json.loads(body) == {"authorized": False, "reason": "no_token"}

    def test_start_flow_error_becomes_error_status(
        self, server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth-flow бросил (120с таймаут / сбой браузера) → job error, не hang."""
        from stepik_grader.web import auth_adapter

        def _boom(*_a, **_k):
            raise TimeoutError("OAuth code not received within 120s")

        monkeypatch.setattr(auth_adapter, "authorize_and_get_token", _boom)
        status, body = _post(
            server + "/api/auth/start",
            json.dumps({"client_id": "cid", "client_secret": "csec"}).encode("utf-8"),
        )
        assert status == 202
        run_id = json.loads(body)["run_id"]
        data = _poll_run(server, run_id)
        assert data["status"] == "error"
        assert "120s" in (data.get("message") or "")


# ---------------------------------------------------------------------------
# /api/import-reference — импорт закреплённого решения Stepik (issue #55)
# ---------------------------------------------------------------------------


class TestApiImportReference:
    _TARGET = "stepik_grader.web.api_routes.import_reference"

    def test_happy_path_returns_files(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            self._TARGET,
            lambda path, *, top=5: {"ok": True, "files": ["task3_100.py"], "message": "ok"},
        )
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        status, body = _post(
            server + "/api/import-reference", json.dumps({"path": str(task_dir)}).encode()
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True
        assert data["files"] == ["task3_100.py"]

    def test_missing_path_is_400(self, server: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            self._TARGET, lambda *a, **k: pytest.fail("adapter не должен вызываться без path")
        )
        status, _ = _post(server + "/api/import-reference", json.dumps({}).encode())
        assert status == 400

    def test_path_outside_workspace_is_403_before_adapter(
        self,
        server: str,
        tmp_path_factory: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called: list[int] = []
        monkeypatch.setattr(
            self._TARGET,
            lambda path, *, top=5: called.append(1) or {"ok": True, "files": [], "message": ""},
        )
        outside = tmp_path_factory.mktemp("outside")
        status, _ = _post(
            server + "/api/import-reference", json.dumps({"path": str(outside)}).encode()
        )
        assert status == 403
        assert not called  # confinement сработал до вызова adapter

    def test_adapter_error_is_200_with_ok_false(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ошибка цепочки (нет ветки/решений) — HTTP 200, ok=False (паттерн download)
        monkeypatch.setattr(
            self._TARGET,
            lambda path, *, top=5: {"ok": False, "message": "У задачи нет ветки решений"},
        )
        task_dir = tmp_path / "t"
        task_dir.mkdir()
        status, body = _post(
            server + "/api/import-reference", json.dumps({"path": str(task_dir)}).encode()
        )
        assert status == 200
        assert json.loads(body)["ok"] is False

    def test_top_param_forwarded(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, int] = {}
        monkeypatch.setattr(
            self._TARGET,
            lambda path, *, top=5: seen.update(top=top) or {"ok": True, "files": [], "message": ""},
        )
        task_dir = tmp_path / "t"
        task_dir.mkdir()
        _post(
            server + "/api/import-reference",
            json.dumps({"path": str(task_dir), "top": 3}).encode(),
        )
        assert seen["top"] == 3


class TestReferenceAdapterUnit:
    _MOD = "stepik_grader.web.reference_adapter"

    def _patch_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        monkeypatch.setattr(
            self._MOD + "._resolve_config", lambda _root: (tmp_path, tmp_path / "secrets.json")
        )

    def test_no_secrets_returns_ok_false(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.web.reference_adapter import import_reference

        self._patch_config(monkeypatch, tmp_path)

        def _raise(_p):
            raise FileNotFoundError("no secrets")

        monkeypatch.setattr(self._MOD + ".load_secrets_dict", _raise)
        result = import_reference(str(tmp_path))
        assert result["ok"] is False
        assert "secrets" in result["message"].lower()

    def test_no_browser_session_returns_ok_false(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.web.reference_adapter import import_reference

        self._patch_config(monkeypatch, tmp_path)
        monkeypatch.setattr(self._MOD + ".load_secrets_dict", lambda _p: {})
        monkeypatch.setattr(self._MOD + ".try_create_session_without_browser", lambda _s, _p: None)
        result = import_reference(str(tmp_path))
        assert result["ok"] is False
        assert "авториз" in result["message"].lower()

    def test_happy_returns_filenames(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.web import reference_adapter

        self._patch_config(monkeypatch, tmp_path)
        monkeypatch.setattr(self._MOD + ".load_secrets_dict", lambda _p: {})
        monkeypatch.setattr(
            self._MOD + ".try_create_session_without_browser", lambda _s, _p: object()
        )
        monkeypatch.setattr(
            reference_adapter,
            "import_references_from_task_dir",
            lambda _d, *, max_top, session: [tmp_path / "task3_100.py", tmp_path / "task3_101.py"],
        )
        result = reference_adapter.import_reference(str(tmp_path), top=2)
        assert result["ok"] is True
        assert result["files"] == ["task3_100.py", "task3_101.py"]

    def test_core_error_becomes_ok_false(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.web import reference_adapter

        self._patch_config(monkeypatch, tmp_path)
        monkeypatch.setattr(self._MOD + ".load_secrets_dict", lambda _p: {})
        monkeypatch.setattr(
            self._MOD + ".try_create_session_without_browser", lambda _s, _p: object()
        )

        def _raise(_d, *, max_top, session):
            raise ValueError("У задачи нет ветки решений")

        monkeypatch.setattr(reference_adapter, "import_references_from_task_dir", _raise)
        result = reference_adapter.import_reference(str(tmp_path))
        assert result["ok"] is False
        assert "ветк" in result["message"]


class TestAiHintApi:
    """POST /api/v1/hint (issue #543): async AI-подсказка + обязательный consent.

    Мокаем канал на уровне ``runs.explain_failure``/``runs.is_configured`` (сам
    explain_failure покрыт в test_ai_hints.py) — проверяем плумбинг эндпоинт →
    job → результат, consent-гейт и graceful skip.
    """

    _HINT = "🤖 AI-подсказка: попробуй прибавить 1"

    def test_hint_requires_consent_nothing_sent(self, server, monkeypatch) -> None:
        """Без согласия → 403 consent_required; провайдер НЕ вызывается (в сеть 0)."""
        called: list[int] = []
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: called.append(1) or "x")
        monkeypatch.setattr(runs, "is_configured", lambda cfg: True)
        body = json.dumps({"verdict": "WA", "actual": "6", "expected": "5"}).encode()
        status, resp = _post(server + "/api/v1/hint", body)
        assert status == 403
        assert json.loads(resp)["message_id"] == "consent_required"
        assert called == []  # job не поставлен, explain_failure не вызван

    def test_hint_configured_returns_marked_hint(self, server, monkeypatch) -> None:
        """consent:true + настроенный канал → 202 → job отдаёт hint отдельным полем;
        контекст собран из полей тела (verdict/actual/expected/stdin)."""
        captured: dict[str, object] = {}

        def _fake(fc: object, cfg: object) -> str:
            captured["fc"] = fc
            return self._HINT

        monkeypatch.setattr(runs, "explain_failure", _fake)
        monkeypatch.setattr(runs, "is_configured", lambda cfg: True)
        body = json.dumps(
            {"verdict": "WA", "stdin": "4", "expected": "5", "actual": "6", "consent": True}
        ).encode()
        status, resp = _post(server + "/api/v1/hint", body)
        assert status == 202
        data = _poll_run(server, json.loads(resp)["run_id"])
        assert data["status"] == "done"
        assert data["result"] == {"hint": self._HINT, "configured": True}
        fc = captured["fc"]
        assert fc.verdict == "WA"  # type: ignore[attr-defined]
        assert fc.actual == "6" and fc.expected == "5"  # type: ignore[attr-defined]
        assert fc.case_input == "4"  # type: ignore[attr-defined]

    def test_hint_not_configured_graceful_null(self, server, monkeypatch) -> None:
        """consent:true, провайдер не настроен → job done с hint=null (graceful)."""
        monkeypatch.setattr(runs, "is_configured", lambda cfg: False)
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: None)
        body = json.dumps(
            {"verdict": "RE", "error": "ZeroDivisionError: x", "consent": True}
        ).encode()
        status, resp = _post(server + "/api/v1/hint", body)
        assert status == 202
        data = _poll_run(server, json.loads(resp)["run_id"])
        assert data["status"] == "done"
        assert data["result"] == {"hint": None, "configured": False}

    def test_hint_consent_persists_across_requests(self, server, tmp_path, monkeypatch) -> None:
        """consent:true фиксируется в .grader_settings.json; далее без consent проходит."""
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: self._HINT)
        monkeypatch.setattr(runs, "is_configured", lambda cfg: True)
        first = json.dumps({"verdict": "WA", "actual": "6", "consent": True}).encode()
        status1, resp1 = _post(server + "/api/v1/hint", first)
        assert status1 == 202
        _poll_run(server, json.loads(resp1)["run_id"])
        settings_file = tmp_path / ".grader_settings.json"
        assert settings_file.exists()
        assert json.loads(settings_file.read_text(encoding="utf-8"))["ai_hint_consent"] is True
        # Второй запрос БЕЗ поля consent — согласие уже запомнено сервером.
        second = json.dumps({"verdict": "WA", "actual": "7"}).encode()
        status2, _ = _post(server + "/api/v1/hint", second)
        assert status2 == 202

    def test_hint_path_outside_workspace_rejected(self, server, monkeypatch) -> None:
        """consent даёт согласие, но path вне workspace → 403 path_outside_workspace."""
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: self._HINT)
        monkeypatch.setattr(runs, "is_configured", lambda cfg: True)
        body = json.dumps({"verdict": "WA", "path": "../../etc/passwd", "consent": True}).encode()
        status, resp = _post(server + "/api/v1/hint", body)
        assert status == 403
        assert json.loads(resp)["message_id"] == "path_outside_workspace"
