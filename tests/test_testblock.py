"""Тесты для парсинга и детекции TEST-блоков формата python-generation.

Покрывает:
    - _parse_testblock_file   — разбор input.txt/output.txt с `# TEST_N:`
    - _is_python_code_block    — классификация блока (Python-код vs stdin)
"""

from __future__ import annotations

from stepik_grader.grader import _is_python_code_block, _parse_testblock_file

# ===========================================================================
# _is_python_code_block
# ===========================================================================


class TestIsPythonCodeBlock:
    """Эвристика: валидный Python AST с хотя бы одним ast.Name → код."""

    def test_bare_numbers_are_stdin(self) -> None:
        assert _is_python_code_block("10\n20\n30") is False

    def test_date_string_is_stdin(self) -> None:
        assert _is_python_code_block("04.11.2021") is False

    def test_plain_text_is_stdin(self) -> None:
        # Строки текста не парсятся как Python → stdin.
        assert _is_python_code_block("привет мир") is False

    def test_function_call_is_code(self) -> None:
        assert _is_python_code_block("print(func(x))") is True

    def test_for_loop_block_is_code(self) -> None:
        block = (
            "result = wins([('Тимур', 'Артур')])\n"
            "for winner, losers in sorted(result.items()):\n"
            "    print(winner, '->', *sorted(losers))"
        )
        assert _is_python_code_block(block) is True

    def test_chainmap_assignment_is_code(self) -> None:
        assert _is_python_code_block("chainmap = ChainMap({})") is True

    def test_empty_block_is_stdin(self) -> None:
        assert _is_python_code_block("") is False

    def test_whitespace_only_block_is_stdin(self) -> None:
        assert _is_python_code_block("   \n  ") is False


# ===========================================================================
# _parse_testblock_file
# ===========================================================================


class TestParseTestblockFile:
    """Разбор файлов с маркерами `# TEST_N:`, включая пустые блоки."""

    def test_basic_blocks(self) -> None:
        text = "# INPUT DATA:\n# TEST_1:\nhello\n# TEST_2:\nworld\n"
        assert _parse_testblock_file(text) == ["hello", "world"]

    def test_empty_block_preserved(self) -> None:
        # Module_4.1.10 TEST_5: пустой блок между маркерами.
        text = "# INPUT DATA:\n# TEST_1:\n\n# TEST_2:\nhello\n"
        assert _parse_testblock_file(text) == ["", "hello"]

    def test_trailing_empty_block_preserved(self) -> None:
        text = "# TEST_1:\nhello\n# TEST_2:\n"
        assert _parse_testblock_file(text) == ["hello", ""]

    def test_multiline_block(self) -> None:
        text = "# TEST_1:\na = wins([])\nfor k in a:\n    print(k)\n# TEST_2:\n42\n"
        assert _parse_testblock_file(text) == [
            "a = wins([])\nfor k in a:\n    print(k)",
            "42",
        ]

    def test_input_data_marker_ignored(self) -> None:
        text = "# INPUT DATA:\n# TEST_1:\nx\n"
        assert _parse_testblock_file(text) == ["x"]
