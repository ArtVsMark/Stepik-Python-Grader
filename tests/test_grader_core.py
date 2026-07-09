"""Tests for grader core helpers identified as coverage gaps in the audit:
_is_python_code_block, load_test_cases format-detection priority, and
resolve_test_dir search order.

These pin down behavior that the upcoming refactoring touches indirectly, so
regressions surface immediately.
"""

from __future__ import annotations

import pathlib
import warnings

import pytest

from stepik_grader import grader

# ---------------------------------------------------------------------------
# _is_python_code_block  (parametrized — replaces 4 separate test functions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("print(func(x))", True),
        ("result = func(5)\nprint(result)", True),
        ("1\n2\n3", False),
        ("10", False),
        ("", False),
        ("   \n  ", False),
        ("04.11.2021", False),
        # Issue #47 R-02: a bare name with no call and no assignment is
        # degenerate stdin-shaped data, not a call-block or a declaration.
        ("x", False),
        ("print", False),
        ("True\nFalse\nNone", False),
        # Assignment target IS still a Name node -- unaffected by the bare-name
        # exception above (more than one top-level statement / not a bare Expr).
        ("x = 5", True),
        ("chainmap = ChainMap({})", True),
    ],
)
def test_is_python_code_block(code: str, expected: bool) -> None:
    """_is_python_code_block returns True only when the block contains a Name node."""
    assert grader._is_python_code_block(code) is expected


# ---------------------------------------------------------------------------
# _verdict — ratio → label
# Проверяем ТОЛЬКО вердикты, которые реально возвращает _verdict().
# FASTER не существует в текущей реализации — ratio < 1.0 → SIMILAR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ratio,expected_verdict",
    [
        (1.0, "SIMILAR"),
        (1.14, "SIMILAR"),
        (1.15, "SIMILAR"),  # граница включительно
        (0.9, "SIMILAR"),  # ratio < 1.0 → всё равно SIMILAR
        (0.5, "SIMILAR"),  # ratio << 1.0 → всё равно SIMILAR
        (1.16, "SLOWER"),
        (1.49, "SLOWER"),
        (1.50, "SLOWER"),  # граница включительно
        (1.51, "MUCH_SLOWER"),
        (2.0, "MUCH_SLOWER"),
    ],
)
def test_verdict(ratio: float, expected_verdict: str) -> None:
    """_verdict maps a timing ratio to the correct label."""
    assert grader._verdict(ratio) == expected_verdict


# ---------------------------------------------------------------------------
# _micro_stats — descriptive statistics
# ---------------------------------------------------------------------------


def test_micro_stats_basic() -> None:
    """_micro_stats returns correct min/max/mean/median/stdev for a simple series."""
    stats = grader._micro_stats([0.1, 0.2, 0.3, 0.4, 0.5])
    assert stats["min"] == pytest.approx(0.1)
    assert stats["max"] == pytest.approx(0.5)
    assert stats["median"] == pytest.approx(0.3)
    assert stats["mean"] == pytest.approx(0.3)


def test_micro_stats_single_value() -> None:
    """A single-element series: stdev is 0, min == max == median == mean."""
    stats = grader._micro_stats([0.42])
    assert stats["min"] == pytest.approx(0.42)
    assert stats["max"] == pytest.approx(0.42)
    assert stats["stdev"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# load_test_cases — format detection priority
# ---------------------------------------------------------------------------


def test_load_test_cases_format3_priority(tmp_path: pathlib.Path):
    """Format 3 (input.txt + output.txt with # TEST_N: blocks) is detected.

    REFACTORING INVARIANT: format-3 must keep top priority and auto-classify
    each block's test_type (function vs stdin).
    """
    (tmp_path / "input.txt").write_text("# TEST_1:\n2\n3\n# TEST_2:\n4\n5\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n5\n# TEST_2:\n9\n", encoding="utf-8")

    cases = grader.load_test_cases(str(tmp_path))
    assert len(cases) == 2
    assert cases[0].index == 1
    assert cases[0].input_lines == ["2", "3"]
    assert cases[0].expected_lines == ["5"]
    assert cases[0].test_type == "stdin"


def test_load_test_cases_format3_classifies_function_block(tmp_path: pathlib.Path):
    """A format-3 block that is Python code is classified test_type='function'."""
    (tmp_path / "input.txt").write_text("# TEST_1:\nprint(add(1, 2))\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n3\n", encoding="utf-8")

    cases = grader.load_test_cases(str(tmp_path))
    assert len(cases) == 1
    assert cases[0].test_type == "function"


def test_load_test_cases_format1_fallback(tmp_path: pathlib.Path):
    """Format 1 (numbered N / N.clue files) is used when no input.txt/output.txt."""
    (tmp_path / "1").write_text("3\n7\n", encoding="utf-8")
    (tmp_path / "1.clue").write_text("10\n", encoding="utf-8")
    (tmp_path / "2").write_text("1\n", encoding="utf-8")
    (tmp_path / "2.clue").write_text("2\n", encoding="utf-8")

    cases = grader.load_test_cases(str(tmp_path))
    assert [c.index for c in cases] == [1, 2]
    assert cases[0].input_lines == ["3", "7"]
    assert cases[0].expected_lines == ["10"]


def test_load_test_cases_format1_type_file(tmp_path: pathlib.Path):
    """A N.type file containing 'function' sets the case test_type."""
    (tmp_path / "1").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "1.clue").write_text("1\n", encoding="utf-8")
    (tmp_path / "1.type").write_text("function\n", encoding="utf-8")

    cases = grader.load_test_cases(str(tmp_path))
    assert len(cases) == 1
    assert cases[0].test_type == "function"


def test_load_test_cases_format2(tmp_path: pathlib.Path):
    """Format 2 (input_N.txt / expected_N.txt) is detected when no format 3."""
    (tmp_path / "input_1.txt").write_text("5\n", encoding="utf-8")
    (tmp_path / "expected_1.txt").write_text("25\n", encoding="utf-8")

    cases = grader.load_test_cases(str(tmp_path))
    assert len(cases) == 1
    assert cases[0].index == 1
    assert cases[0].input_lines == ["5"]
    assert cases[0].expected_lines == ["25"]


def test_load_test_cases_empty_dir(tmp_path: pathlib.Path):
    """An empty directory yields no cases."""
    assert grader.load_test_cases(str(tmp_path)) == []


def test_load_test_cases_warns_on_mixed_format3_and_format1(tmp_path: pathlib.Path) -> None:
    """Format 3 + leftover Format 1 (.clue) files -- warn, still use Format 3.

    Issue #48 R-03: previously the ignored .clue files were silent.
    """
    (tmp_path / "input.txt").write_text("# TEST_1:\n2\n3\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")
    (tmp_path / "1").write_text("9\n", encoding="utf-8")
    (tmp_path / "1.clue").write_text("99\n", encoding="utf-8")

    with pytest.warns(UserWarning, match="Format 3 takes priority"):
        cases = grader.load_test_cases(str(tmp_path))

    assert len(cases) == 1
    assert cases[0].input_lines == ["2", "3"]


def test_load_test_cases_no_warning_for_format3_alone(tmp_path: pathlib.Path) -> None:
    """No leftover Format 1/2 files -- no warning fires."""
    (tmp_path / "input.txt").write_text("# TEST_1:\n2\n3\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cases = grader.load_test_cases(str(tmp_path))

    assert len(cases) == 1


# ---------------------------------------------------------------------------
# resolve_test_dir — search order
# ---------------------------------------------------------------------------


def test_resolve_test_dir_finds_tests_subfolder(tmp_path: pathlib.Path):
    """A sibling tests/ folder wins (highest priority)."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    assert grader.resolve_test_dir(str(sol)) == str(tests_dir)


def test_resolve_test_dir_finds_stem_folder(tmp_path: pathlib.Path):
    """A folder named after the solution stem is used when no tests/ exists."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    stem_dir = tmp_path / "task1"
    stem_dir.mkdir()

    assert grader.resolve_test_dir(str(sol)) == str(stem_dir)


def test_resolve_test_dir_finds_adjacent_input_txt(tmp_path: pathlib.Path):
    """If the parent folder holds input.txt + output.txt, the parent is returned."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")

    assert grader.resolve_test_dir(str(sol)) == str(tmp_path.resolve())


def test_resolve_test_dir_finds_clue_in_parent(tmp_path: pathlib.Path):
    """A .clue file in the parent folder makes the parent the test dir."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "1").write_text("1\n", encoding="utf-8")
    (tmp_path / "1.clue").write_text("1\n", encoding="utf-8")

    assert grader.resolve_test_dir(str(sol)) == str(tmp_path.resolve())


# ---------------------------------------------------------------------------
# _build_function_wrapper — identifier validation (Issue #20 finding #5)
# ---------------------------------------------------------------------------


def test_build_function_wrapper_accepts_valid_identifiers(tmp_path: pathlib.Path):
    """A well-formed function_name/module stem generates a wrapper normally."""
    sol = tmp_path / "task1.py"
    sol.write_text("def solve(x):\n    return x\n", encoding="utf-8")

    src = grader._build_function_wrapper(str(sol), "x = 1", "solve")

    assert "from task1 import solve" in src


def test_build_function_wrapper_rejects_invalid_function_name(tmp_path: pathlib.Path):
    """A function_name that isn't a valid identifier must not reach the f-string.

    Without validation, a value like "x\\nimport os" would inject an extra
    statement into the generated wrapper script.
    """
    sol = tmp_path / "task1.py"
    sol.write_text("def solve(x):\n    return x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid function_name"):
        grader._build_function_wrapper(str(sol), "x = 1", "solve\nimport os")


def test_build_function_wrapper_rejects_invalid_module_stem(tmp_path: pathlib.Path):
    """A solution filename whose stem isn't a valid identifier is rejected too."""
    sol = tmp_path / "task-1.py"
    sol.write_text("def solve(x):\n    return x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid module filename stem"):
        grader._build_function_wrapper(str(sol), "x = 1", "solve")


def test_run_single_test_reports_re_for_invalid_function_name(tmp_path: pathlib.Path):
    """run_single_test converts the ValueError into a graceful RE verdict.

    Without the try/except around _build_function_wrapper, this would crash
    the whole grading run instead of failing just this one test case.
    """
    sol = tmp_path / "task-1.py"  # stem "task-1" is not a valid identifier
    sol.write_text("def solve(x):\n    return x\n", encoding="utf-8")
    # "5" has no ast.Name node, so _is_python_code_block classifies it as data
    # (not a call block) and run_single_test routes to _build_function_wrapper.
    case = grader.TestCase(index=1, input_lines=["5"], expected_lines=["5"], test_type="function")

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "RE"
    assert result["passed"] is False
    assert "Invalid module filename stem" in result["error"]
    assert result["exit_code"] is None  # no process was ever launched


# ---------------------------------------------------------------------------
# exit_code — additive field on run_single_test's result dict (issue #125)
# ---------------------------------------------------------------------------


def test_run_single_test_exit_code_zero_on_ac(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "task.py"
    sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
    case = grader.TestCase(index=1, input_lines=["4"], expected_lines=["5"])

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "AC"
    assert result["exit_code"] == 0


def test_run_single_test_exit_code_zero_on_wa(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "task.py"
    sol.write_text("print(int(input()) + 2)\n", encoding="utf-8")
    case = grader.TestCase(index=1, input_lines=["4"], expected_lines=["5"])

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "WA"
    assert result["exit_code"] == 0


def test_run_single_test_exit_code_nonzero_on_re(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "task.py"
    sol.write_text("raise ValueError('boom')\n", encoding="utf-8")
    case = grader.TestCase(index=1, input_lines=[""], expected_lines=["5"])

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "RE"
    assert result["exit_code"] not in (0, None)


def test_run_single_test_exit_code_none_on_tle(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "task.py"
    sol.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    case = grader.TestCase(index=1, input_lines=[""], expected_lines=["5"])

    result = grader.run_single_test(str(sol), case, timeout=0.1, measure_memory=False)

    assert result["verdict"] == "TLE"
    assert result["exit_code"] is None


# ---------------------------------------------------------------------------
# BenchStats — shared stats between run_benchmark() and _micro_stats() (Sprint 7.2)
# ---------------------------------------------------------------------------


def test_bench_stats_computes_all_fields() -> None:
    stats = grader.BenchStats(timings=[0.001, 0.002, 0.003, 0.004, 0.005])
    assert stats.min == 0.001
    assert stats.max == 0.005
    assert stats.median == 0.003
    assert stats.mean == pytest.approx(0.003)
    assert stats.stdev > 0.0


def test_bench_stats_stdev_zero_for_single_timing() -> None:
    stats = grader.BenchStats(timings=[0.5])
    assert stats.stdev == 0.0
    assert stats.min == stats.max == stats.median == stats.mean == 0.5


def test_bench_stats_relative_to() -> None:
    stats = grader.BenchStats(timings=[0.002])
    assert stats.relative_to(0.001) == pytest.approx(200.0)


def test_bench_stats_relative_to_zero_baseline() -> None:
    """baseline == 0 avoids division by zero, returns 0.0 rather than raising."""
    stats = grader.BenchStats(timings=[0.002])
    assert stats.relative_to(0.0) == 0.0


def test_run_benchmark_and_micro_stats_agree_on_same_timings() -> None:
    """run_benchmark()'s stats dict and _micro_stats() compute identically via BenchStats."""
    from stepik_grader.core.grader_core import _micro_stats

    times = [0.01, 0.02, 0.015, 0.03]
    micro = _micro_stats(times)
    direct = grader.BenchStats(timings=times)
    assert micro["min"] == direct.min
    assert micro["max"] == direct.max
    assert micro["median"] == direct.median
    assert micro["mean"] == direct.mean
    assert micro["stdev"] == direct.stdev


# ---------------------------------------------------------------------------
# _build_call_wrapper — explicit imports instead of wildcard (Issue #44)
# ---------------------------------------------------------------------------


def test_build_call_wrapper_has_no_wildcard_imports() -> None:
    """Generated wrapper source must not contain `import *` (regression guard)."""
    src = grader._build_call_wrapper("task1.py", "print(1)")
    assert "import *" not in src


def test_build_call_wrapper_solution_name_overrides_stdlib(tmp_path: pathlib.Path) -> None:
    """A solution defining its own `reduce`/`chain` must win over the stdlib one.

    functools.reduce/itertools.chain are among the names explicitly imported
    for use in test-blocks (Issue #44); the solution's public names are
    copied into globals() afterwards specifically so they take priority.
    """
    sol = tmp_path / "task1.py"
    sol.write_text(
        "def reduce(a, b):\n"
        "    return f'custom-reduce({a},{b})'\n"
        "\n"
        "def chain(a, b):\n"
        "    return f'custom-chain({a},{b})'\n",
        encoding="utf-8",
    )
    case = grader.TestCase(
        index=1,
        input_lines=["print(reduce(1, 2))", "print(chain(3, 4))"],
        expected_lines=["custom-reduce(1,2)", "custom-chain(3,4)"],
        test_type="function",
    )

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "AC", result["error"] or result["diff"]
    assert result["output"] == ["custom-reduce(1,2)", "custom-chain(3,4)"]


def test_build_call_wrapper_stdlib_names_available_without_solution_definitions(
    tmp_path: pathlib.Path,
) -> None:
    """Test-blocks may use stdlib names the solution never defines itself."""
    sol = tmp_path / "task1.py"
    sol.write_text("def solve(x):\n    return x\n", encoding="utf-8")
    case = grader.TestCase(
        index=1,
        input_lines=["print(list(product([1, 2], [3, 4])))"],
        expected_lines=["[(1, 3), (1, 4), (2, 3), (2, 4)]"],
        test_type="function",
    )

    result = grader.run_single_test(str(sol), case, measure_memory=False)

    assert result["verdict"] == "AC", result["error"] or result["diff"]


# _apply_memory_limit/_measure_peak_memory moved to core/runner.py along with
# the subprocess execution itself (issue #136/#137/#138, Runner abstraction) —
# their tests moved to tests/test_runner.py, which now also targets that
# module directly for monkeypatching (resource/psutil live there, not here).
# grader_core._apply_memory_limit/._measure_peak_memory remain valid
# re-exported references (see grader_core.py's import block).
