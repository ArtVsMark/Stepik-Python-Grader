"""Тесты для core/tests_writer.py — запись форматов тест-кейсов (issue #302).

Выделено из test_downloader_extra.py: save_tests (Format 1: N/N.clue/N.type) +
write_testblock_tests (Format 3: input.txt/output.txt с # TEST_N:).
"""

from __future__ import annotations

import pathlib

from stepik_grader.core.tests_writer import save_tests, write_testblock_tests


class TestSaveTests:
    """save_tests пишет N / N.clue / N.type (Format 1)."""

    def test_writes_files(self, tmp_path: pathlib.Path):
        tests = [("in1", "out1", "stdin"), ("a=1", "1", "function")]
        count = save_tests(tmp_path, tests)
        assert count == 2
        tdir = tmp_path / "tests"
        assert (tdir / "1").read_text() == "in1"
        assert (tdir / "1.clue").read_text() == "out1"
        assert not (tdir / "1.type").exists()
        assert (tdir / "2.type").read_text() == "function"


class TestWriteTestblockTests:
    """write_testblock_tests пишет input.txt/output.txt с маркерами (Format 3)."""

    def test_writes_format3_with_markers(self, tmp_path: pathlib.Path):
        tests_dir = tmp_path / "tests"
        count = write_testblock_tests(tests_dir, {1: ("10\n20", "60"), 2: ("5", "15")})
        assert count == 2
        input_text = (tests_dir / "input.txt").read_text(encoding="utf-8")
        output_text = (tests_dir / "output.txt").read_text(encoding="utf-8")
        assert input_text.startswith("# INPUT DATA:\n")
        assert output_text.startswith("# OUTPUT DATA:\n")
        assert "# TEST_1:\n10\n20\n" in input_text
        assert "# TEST_2:\n5\n" in input_text
        assert "# TEST_1:\n60\n" in output_text

    def test_blocks_sorted_numerically(self, tmp_path: pathlib.Path):
        tests_dir = tmp_path / "tests"
        write_testblock_tests(tests_dir, {10: ("ten", "TEN"), 2: ("two", "TWO")})
        input_text = (tests_dir / "input.txt").read_text(encoding="utf-8")
        assert input_text.index("# TEST_2:") < input_text.index("# TEST_10:")
