"""Тесты для багфиксов режимов меню 2/3/4 grader.py.

Покрывает:
    - Режим 2: per-solution resolve_test_dir вместо одного общего test_dir
    - run_benchmark: применяет _detect_run_mode (function-mode)
    - run_microbench_mode: function-call блоки идут через subprocess, не timeit
    - _resolve_test_dir_from_input(is_dir=True): Format 3 (input.txt + output.txt)
"""

from __future__ import annotations

import os
import pathlib

from stepik_grader import cli, grader
from stepik_grader.core import grader_core
from stepik_grader.grader import (
    _resolve_test_dir_from_input,
    run_benchmark,
    run_microbench_mode,
)


def _make_task(base: pathlib.Path, name: str, *, body: str, inp: str, out: str) -> pathlib.Path:
    """Создаёт подпапку задачи: <name>/<name>.py + <name>/tests/input_1.txt + expected_1.txt."""
    task_dir = base / name
    task_dir.mkdir(parents=True)
    (task_dir / f"{name}.py").write_text(body, encoding="utf-8")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text(inp, encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text(out, encoding="utf-8")
    return task_dir / f"{name}.py"


# ===========================================================================
# Режим 2 — per-solution test_dir (Bug 1)
# ===========================================================================


class TestMode2PerSolutionTestDir:
    """Режим 2 должен резолвить test_dir отдельно для каждого решения."""

    def test_each_solution_uses_own_test_dir(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        # Две задачи в разных подпапках, у каждой свой tests/
        _make_task(tmp_path, "task1", body="print(int(input()) + 1)\n", inp="1", out="2")
        _make_task(tmp_path, "task2", body="print(int(input()) * 2)\n", inp="3", out="6")

        used: dict[str, str] = {}

        def fake_run_tests(
            path, test_dir, *, verbose=False, verbose_callback=None, timeout=grader.TIMEOUT_SECONDS
        ):
            used[os.path.basename(path)] = test_dir
            return {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "total_time": 0.0,
                "avg_time": 0.0,
                "peak_memory_mb": 0.0,
                "first_fail": None,
                "cases": [],
            }

        monkeypatch.setattr(cli, "run_tests", fake_run_tests)

        inputs = iter(["2", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

        grader._interactive_menu()

        # Каждое решение получило СВОЙ tests/, а не один общий
        assert used["task1.py"] == str((tmp_path / "task1" / "tests").resolve())
        assert used["task2.py"] == str((tmp_path / "task2" / "tests").resolve())
        assert used["task1.py"] != used["task2.py"]

    def test_falls_back_to_folder_test_dir(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        # Решение без собственного tests/ → fallback на folder-level test_dir
        sol_dir = tmp_path / "solutions"
        sol_dir.mkdir()
        (sol_dir / "task1.py").write_text("print(input())\n", encoding="utf-8")
        # folder-level tests/ рядом с корнем
        folder_tests = tmp_path / "tests"
        folder_tests.mkdir()
        (folder_tests / "input_1.txt").write_text("x", encoding="utf-8")
        (folder_tests / "expected_1.txt").write_text("x", encoding="utf-8")

        used: list[str] = []

        def fake_run_tests(
            path, test_dir, *, verbose=False, verbose_callback=None, timeout=grader.TIMEOUT_SECONDS
        ):
            used.append(test_dir)
            return {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "errors": 0,
                "total_time": 0.0,
                "avg_time": 0.0,
                "peak_memory_mb": 0.0,
                "first_fail": None,
                "cases": [],
            }

        monkeypatch.setattr(cli, "run_tests", fake_run_tests)
        inputs = iter(["2", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

        grader._interactive_menu()

        # resolve_test_dir(task1.py) указывает на solutions/ (нет реального tests),
        # поэтому должен сработать fallback на folder-level tests/
        assert used == [str(folder_tests)]


# ===========================================================================
# run_benchmark — учитывает _detect_run_mode (Bug 2B)
# ===========================================================================


class TestRunBenchmarkRunMode:
    """run_benchmark должен переопределять test_type на function для function-mode задач."""

    def test_applies_function_mode(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        # Решение — function-only (нет точки входа) → _detect_run_mode == "function"
        sol = tmp_path / "task1.py"
        sol.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # Блок-данные с типом stdin (input_N формат → test_type="stdin" по умолчанию)
        (tests_dir / "input_1.txt").write_text("1 2", encoding="utf-8")
        (tests_dir / "expected_1.txt").write_text("3", encoding="utf-8")

        seen_types: list[str] = []

        def fake_run_single_test(path, case, *, timeout=grader.TIMEOUT_SECONDS):
            seen_types.append(case.test_type)
            return {"error": "", "timed_out": False, "time": 0.001, "memory": 0.0}

        monkeypatch.setattr(grader_core, "run_single_test", fake_run_single_test)

        run_benchmark(str(sol), str(tests_dir), repeats=1)

        # Изначально test_type был "stdin", но run_mode == "function" должен переопределить
        assert seen_types == ["function"]


# ===========================================================================
# run_microbench_mode — function-call блоки (Bug 3)
# ===========================================================================


class TestRunMicrobenchModeFunctionBlocks:
    """run_microbench_mode для function-call блоков использует subprocess, не timeit."""

    def test_function_block_uses_subprocess(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        # Format 3 с function-call блоком
        (test_dir / "input.txt").write_text("# TEST_1:\nprint(add(2, 3))\n", encoding="utf-8")
        (test_dir / "output.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")

        # run_microbench (timeit-путь) НЕ должен вызываться для function-блоков
        def fail_microbench(*a, **k):
            raise AssertionError("run_microbench should not be called for function blocks")

        monkeypatch.setattr(grader_core, "run_microbench", fail_microbench)

        results = run_microbench_mode([str(sol)], str(test_dir), number=100)

        assert str(sol) in results
        assert not results[str(sol)].get("error"), results[str(sol)]
        assert results[str(sol)]["runs"] >= 1

    def test_stdin_block_uses_timeit(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("5", encoding="utf-8")
        (test_dir / "expected_1.txt").write_text("6", encoding="utf-8")

        called: list[str] = []

        def fake_microbench(code, *, stdin_data="", number=1000, max_memory_mb=None):
            called.append(stdin_data)
            return {"times": [0.001, 0.002], "error": "", "peak_memory_mb": 0.05}

        monkeypatch.setattr(grader_core, "run_microbench", fake_microbench)

        results = run_microbench_mode([str(sol)], str(test_dir), number=100)

        assert called  # timeit-путь использован для stdin-блока
        assert not results[str(sol)].get("error")


# ===========================================================================
# _resolve_test_dir_from_input — Format 3 directory
# ===========================================================================


class TestResolveTestDirFromInput:
    """_resolve_test_dir_from_input(is_dir=True) должен распознавать Format 3."""

    def test_tests_subdir_priority(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        result = _resolve_test_dir_from_input(str(tmp_path), is_dir=True)
        assert result == str(tmp_path / "tests")

    def test_format3_input_output_in_dir(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "input.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        (tmp_path / "output.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        result = _resolve_test_dir_from_input(str(tmp_path), is_dir=True)
        assert result == str(tmp_path)

    def test_tests_subdir_wins_over_format3(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "input.txt").write_text("x", encoding="utf-8")
        (tmp_path / "output.txt").write_text("x", encoding="utf-8")
        result = _resolve_test_dir_from_input(str(tmp_path), is_dir=True)
        assert result == str(tmp_path / "tests")

    def test_fallback_returns_dir_as_is(self, tmp_path: pathlib.Path) -> None:
        result = _resolve_test_dir_from_input(str(tmp_path), is_dir=True)
        assert result == str(tmp_path)

    def test_is_dir_false_delegates_to_resolve_test_dir(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        result = _resolve_test_dir_from_input(str(sol), is_dir=False)
        assert result == str((tmp_path / "tests").resolve())
