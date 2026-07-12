"""Tests for web.py — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1)."""

from __future__ import annotations

import http.client
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stepik_grader import web


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

    def test_unknown_re_exception_queues_missing_glossary_entry(
        self, tmp_path: pathlib.Path
    ) -> None:
        """J7 (web-current.md): unknown exception in an RE case gets queued for the
        glossary backlog, since core/glossary.py has no card for it (ValueError
        IS in the compact glossary, so raise something that isn't)."""
        from stepik_grader.glossary.json_provider import load_missing_queue

        sol = _make_task(tmp_path, "raise ArithmeticError('unusual')\n")
        # ArithmeticError itself isn't in core/glossary.py's curated ~28 entries
        # (only its subclasses ZeroDivisionError/OverflowError/FloatingPointError are).
        queue_path = tmp_path / "missing.json"

        web.grade_path(sol, missing_queue_path=queue_path)

        entries = load_missing_queue(queue_path)
        assert len(entries) == 1
        assert entries[0].concept == "ArithmeticError"
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
            missing_queue_path=str(tmp_path / "missing.json"),
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


# ---------------------------------------------------------------------------
# HTTP-хендлер (интеграционно: реальный сервер на эфемерном порту)
# ---------------------------------------------------------------------------


@pytest.fixture
def server_factory():
    """Фабрика серверов на 127.0.0.1:0 в отдельном потоке (issue #261 —
    параметризуемая workspace/confine); все созданные серверы гасятся в teardown."""
    started: list[tuple[web._GraderServer, threading.Thread]] = []

    def _make(workspace: pathlib.Path, *, confine: bool = True) -> str:
        httpd = web._GraderServer(
            ("127.0.0.1", 0), web._Handler, workspace=workspace, confine=confine
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
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost only)
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

    def test_unknown_path_404(self, server: str) -> None:
        status, _ = _get(server + "/nope")
        assert status == 404

    # -- static routes (issue #125 — JS/CSS extracted from _INDEX_HTML) ------

    def test_static_app_css_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.css", timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
            assert "text/css" in resp.headers["Content-Type"]
            assert b":root" in resp.read()

    def test_static_app_js_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.js", timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
            assert "javascript" in resp.headers["Content-Type"]
            assert b"function grade" in resp.read()

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
        with urllib.request.urlopen(server + "/static/fonts/" + name, timeout=5) as resp:  # noqa: S310
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


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost only)
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
        from stepik_grader.web.server import _clamp

        assert _clamp(5, 1, 10) == 5
        assert _clamp(-5, 1, 10) == 1
        assert _clamp(999, 1, 10) == 10

    def test_post_body_over_limit_is_413(self, server: str, tmp_path: pathlib.Path) -> None:
        from stepik_grader.web.server import _MAX_BODY_BYTES

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
            path: str, repeats: int, reference: str | None = None, lang: str = "ru"
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.server.grade_benchmark", fake_grade_benchmark)
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
            path: str, repeats: int, reference: str | None = None, lang: str = "ru"
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.server.grade_benchmark", fake_grade_benchmark)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "999999999"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["repeats"] == 1000

    def test_microbench_number_negative_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_microbench(path: str, number: int, lang: str = "ru") -> dict:
            captured["number"] = number
            return {"kind": "microbench", "mode": "microbench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.server.grade_microbench", fake_grade_microbench)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "microbench", "number": "-5"})
        status, _ = _get(server + "/api/grade?" + q)
        assert status == 200
        assert captured["number"] == 1

    def test_microbench_number_huge_is_clamped_not_500(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_grade_microbench(path: str, number: int, lang: str = "ru") -> dict:
            captured["number"] = number
            return {"kind": "microbench", "mode": "microbench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.server.grade_microbench", fake_grade_microbench)
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
            path: str, repeats: int, reference: str | None = None, lang: str = "ru"
        ) -> dict:
            captured["repeats"] = repeats
            return {"kind": "bench", "mode": "bench", "rows": []}

        monkeypatch.setattr("stepik_grader.web.server.grade_benchmark", fake_grade_benchmark)
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = _get(server + f"/api/v1/runs/{run_id}")
        assert status == 200
        data = json.loads(body)
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not reach a terminal state within {timeout}s")


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

        deadline = time.monotonic() + 10.0
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pidfile.exists(), "child process never started"
        pid = int(pidfile.read_text().strip())

        cancel_status, cancel_body = _post(server + f"/api/v1/runs/{run_id}/cancel", b"")
        assert cancel_status == 200
        assert json.loads(cancel_body)["status"] in ("running", "error")

        data = _poll_run(server, run_id)
        assert data["status"] == "error"
        assert data["message_id"] == "run_cancelled"

        # Best-effort: give the OS a brief moment to actually reap the killed
        # process before asserting it's gone.
        deadline = time.monotonic() + 3.0
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(pid)

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
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _post(
            server + "/api/v1/runs",
            json.dumps({"path": str(sol), "mode": "tests", "params": {}}).encode("utf-8"),
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

        deadline = time.monotonic() + 10.0
        total = 0
        while time.monotonic() < deadline:
            status, body = _get(server + f"/api/v1/runs/{run_id}")
            data = json.loads(body)
            total = data["progress"]["total"]
            if total > 0 or data["status"] in ("done", "error"):
                break
            time.sleep(0.02)
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


# ---------------------------------------------------------------------------
# Client-side esc() — HTML-attribute hardening (issue #214)
# ---------------------------------------------------------------------------
#
# esc() is embedded JS (no JS runtime in this Python test suite), so these are
# source-level regression checks: they pin down the escape table/regex that
# errorCard() relies on when inserting glossary.url into href="...". A quote
# character reaching that attribute unescaped would let it be broken out of.
#
# issue #125: the JS moved from an inline <script> in _INDEX_HTML to its own
# static/app.js file (re-exported as web._APP_JS) — these regressions now grep
# that instead.


def _ht_table_source() -> str:
    start = web._APP_JS.index("const HT = {")
    end = web._APP_JS.index("};", start)
    return web._APP_JS[start:end]


def test_client_esc_table_covers_html_and_attribute_special_chars() -> None:
    table_src = _ht_table_source()
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert f'"{entity}"' in table_src, f"{entity!r} missing from client-side HT map"


def test_client_esc_regex_includes_quote_chars() -> None:
    # The replace() char class must include both quote characters, or esc()
    # would keep stripping only &/</> and leave href="...' open to breakout.
    assert "replace(/[&<>\"']/g" in web._APP_JS


def test_error_card_url_field_is_passed_through_esc() -> None:
    # errorCard() must run g.url through esc() before inserting it into
    # href="..." -- if a future edit inlines g.url directly, this fails.
    assert r'href="' + "'" + " + esc(g.url) + " + "'" + '"' in web._APP_JS
