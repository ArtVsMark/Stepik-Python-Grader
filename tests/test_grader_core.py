"""Tests for grader core helpers identified as coverage gaps in the audit:
_is_python_code_block, load_test_cases format-detection priority, and
_resolve_test_dir search order.

These pin down behavior that the upcoming refactoring touches indirectly, so
regressions surface immediately.
"""

from __future__ import annotations

import pathlib

import pytest

import grader

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
    ],
)
def test_is_python_code_block(code: str, expected: bool) -> None:
    """_is_python_code_block returns True only when the block contains a Name node."""
    assert grader._is_python_code_block(code) is expected


# ---------------------------------------------------------------------------
# _verdict — ratio → label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ratio,expected_verdict",
    [
        (1.0, "SIMILAR"),
        (1.14, "SIMILAR"),
        (1.15, "SIMILAR"),   # граница включительно
        (1.16, "SLOWER"),
        (1.49, "SLOWER"),
        (1.50, "SLOWER"),    # граница включительно
        (1.51, "MUCH_SLOWER"),
        (2.0, "MUCH_SLOWER"),
        (0.9, "FASTER"),
        (0.5, "FASTER"),
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


# ---------------------------------------------------------------------------
# _resolve_test_dir — search order
# ---------------------------------------------------------------------------


def test_resolve_test_dir_finds_tests_subfolder(tmp_path: pathlib.Path):
    """A sibling tests/ folder wins (highest priority)."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    assert grader._resolve_test_dir(str(sol)) == str(tests_dir)


def test_resolve_test_dir_finds_stem_folder(tmp_path: pathlib.Path):
    """A folder named after the solution stem is used when no tests/ exists."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    stem_dir = tmp_path / "task1"
    stem_dir.mkdir()

    assert grader._resolve_test_dir(str(sol)) == str(stem_dir)


def test_resolve_test_dir_finds_adjacent_input_txt(tmp_path: pathlib.Path):
    """If the parent folder holds input.txt + output.txt, the parent is returned."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
    (tmp_path / "output.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")

    assert grader._resolve_test_dir(str(sol)) == str(tmp_path.resolve())


def test_resolve_test_dir_finds_clue_in_parent(tmp_path: pathlib.Path):
    """A .clue file in the parent folder makes the parent the test dir."""
    sol = tmp_path / "task1.py"
    sol.write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "1").write_text("1\n", encoding="utf-8")
    (tmp_path / "1.clue").write_text("1\n", encoding="utf-8")

    assert grader._resolve_test_dir(str(sol)) == str(tmp_path.resolve())
