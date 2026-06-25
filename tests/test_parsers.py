"""tests/test_parsers.py — прямые тесты для parsers.parse_testblock_file().

Покрывает все ветви функции: базовый разбор, пустые блоки, строки
# INPUT DATA:, многострочные блоки, файл без маркеров, одиночный блок.
"""

from __future__ import annotations

import pytest

from parsers import parse_testblock_file


class TestParseTestblockFile:
    """Тесты parse_testblock_file()."""

    def test_basic_two_blocks(self) -> None:
        """Два стандартных блока — возвращает список из двух строк."""
        text = "# TEST_1:\nhello\n# TEST_2:\nworld\n"
        result = parse_testblock_file(text)
        assert result == ["hello", "world"]

    def test_single_block(self) -> None:
        """Один блок без завершающего перевода строки."""
        text = "# TEST_1:\n42"
        result = parse_testblock_file(text)
        assert result == ["42"]

    def test_empty_block_is_preserved(self) -> None:
        """Пустой блок сохраняется как '' для синхронизации индексов."""
        text = "# TEST_1:\ndata\n# TEST_2:\n# TEST_3:\nmore\n"
        result = parse_testblock_file(text)
        assert result == ["data", "", "more"]

    def test_whitespace_only_block_stripped_to_empty(self) -> None:
        """Блок из одних пробелов → '' после .strip()."""
        text = "# TEST_1:\n   \n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result == ["", "ok"]

    def test_input_data_line_skipped(self) -> None:
        """Строки # INPUT DATA: внутри блока пропускаются."""
        text = "# TEST_1:\n# INPUT DATA: something\nactual content\n"
        result = parse_testblock_file(text)
        assert result == ["actual content"]

    def test_input_data_line_skipped_multiple_blocks(self) -> None:
        """# INPUT DATA: пропускается в каждом блоке независимо."""
        text = (
            "# TEST_1:\n"
            "# INPUT DATA: ignored\n"
            "line1\n"
            "# TEST_2:\n"
            "# INPUT DATA: also ignored\n"
            "line2\n"
        )
        result = parse_testblock_file(text)
        assert result == ["line1", "line2"]

    def test_no_markers_returns_empty_list(self) -> None:
        """Файл без маркеров # TEST_N: → пустой список."""
        text = "just some text\nno markers here\n"
        result = parse_testblock_file(text)
        assert result == []

    def test_empty_string_returns_empty_list(self) -> None:
        """Пустая строка → пустой список."""
        assert parse_testblock_file("") == []

    def test_multiline_block_content(self) -> None:
        """Многострочное содержимое блока сохраняется корректно."""
        text = "# TEST_1:\nline_a\nline_b\nline_c\n# TEST_2:\nresult\n"
        result = parse_testblock_file(text)
        assert result == ["line_a\nline_b\nline_c", "result"]

    def test_leading_trailing_whitespace_stripped(self) -> None:
        """Пробелы в начале и конце блока удаляются через .strip()."""
        text = "# TEST_1:\n\n  spaces  \n\n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result[0] == "spaces"
        assert result[1] == "ok"

    def test_hash_comment_inside_block_preserved(self) -> None:
        """Строка с # внутри блока (не # INPUT DATA:, не # TEST_N:) сохраняется."""
        text = "# TEST_1:\n# regular comment\ncode line\n"
        result = parse_testblock_file(text)
        assert result == ["# regular comment\ncode line"]

    def test_marker_with_extra_spaces(self) -> None:
        """Маркер вида '#  TEST_1:' (лишние пробелы) тоже распознаётся."""
        text = "#  TEST_1:\nvalue\n"
        result = parse_testblock_file(text)
        assert result == ["value"]

    @pytest.mark.parametrize(
        "text,expected_count",
        [
            ("# TEST_1:\na\n# TEST_2:\nb\n# TEST_3:\nc\n", 3),
            ("# TEST_1:\n# TEST_2:\n# TEST_3:\n# TEST_4:\n", 4),
        ],
    )
    def test_block_count(self, text: str, expected_count: int) -> None:
        """Количество возвращённых блоков совпадает с числом маркеров."""
        result = parse_testblock_file(text)
        assert len(result) == expected_count
