"""tests/test_parsers.py — прямые тесты для parsers.parse_testblock_file().

Покрывает все ветви функции: базовый разбор, пустые блоки, строки
# INPUT DATA:, многострочные блоки, файл без маркеров, одиночный блок.
"""

from __future__ import annotations

import pytest

from stepik_grader.core.parsers import parse_testblock_file


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
        """Блок целиком из отбивки → '' (как `# TEST_N:` без данных)."""
        text = "# TEST_1:\n   \n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result == ["", "ok"]

    def test_input_data_header_skipped_before_first_block(self) -> None:
        """Заголовок # INPUT DATA: ДО первого # TEST_N: пропускается (файловый заголовок).

        Это форма, которую пишет ``write_testblock_tests`` (Format 3): первая
        строка файла — ``# INPUT DATA:``, затем пустая строка и блоки.
        """
        text = "# INPUT DATA:\n# TEST_1:\nactual content\n"
        result = parse_testblock_file(text)
        assert result == ["actual content"]

    def test_input_data_inside_block_is_preserved(self) -> None:
        """issue #410 (B6): # INPUT DATA: ВНУТРИ блока — реальные данные, не заголовок.

        Прежде такая строка молча терялась (безусловный skip). Теперь строка,
        начинающаяся с ``# INPUT DATA:`` внутри открытого блока, сохраняется как
        часть данных кейса.
        """
        text = "# TEST_1:\n# INPUT DATA: something\nactual content\n"
        result = parse_testblock_file(text)
        assert result == ["# INPUT DATA: something\nactual content"]

    def test_input_data_header_skipped_but_in_block_kept(self) -> None:
        """Заголовок вне блока пропущен, но одноимённая строка в блоке — сохранена."""
        text = (
            "# INPUT DATA:\n"  # файловый заголовок — пропускается
            "# TEST_1:\n"
            "# INPUT DATA: real payload\n"  # внутри блока — сохраняется
            "line1\n"
            "# TEST_2:\n"
            "line2\n"
        )
        result = parse_testblock_file(text)
        assert result == ["# INPUT DATA: real payload\nline1", "line2"]

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

    def test_blank_edges_dropped_but_inner_spaces_kept(self) -> None:
        """Пустые строки по краям режутся, пробелы ВНУТРИ строки — нет (issue #783).

        Прежде здесь стоял `.strip()` всего блока, срезавший заодно значимые
        пробелы: ожидаемый вывод задачи с выравниванием получал WA при верном
        решении.
        """
        text = "# TEST_1:\n\n  spaces  \n\n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result[0] == "  spaces  "
        assert result[1] == "ok"

    def test_indented_lines_survive(self) -> None:
        """Фигура с отступами (ёлочка) доезжает до сравнения дословно."""
        text = "# TEST_1:\n  *\n ***\n*****\n"
        result = parse_testblock_file(text)
        assert result == ["  *\n ***\n*****"]

    def test_whitespace_only_edge_line_is_treated_as_padding(self) -> None:
        """Строка из одних пробелов на краю — отбивка редактора, а не данные."""
        text = "# TEST_1:\n   \n  data  \n \n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result[0] == "  data  "

    def test_blank_line_inside_block_is_kept(self) -> None:
        """Пустая строка ВНУТРИ блока — часть данных, режутся только края."""
        text = "# TEST_1:\na\n\nb\n# TEST_2:\nok\n"
        result = parse_testblock_file(text)
        assert result[0] == "a\n\nb"

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


class TestMarkerRecognition:
    """issue #996: маркер блока и номера в нём.

    Файлы формата 3 пишет и генератор, и человек: обе находки — про то, что
    отклонение от канонической записи проходило молча и меняло НАБОР кейсов.
    """

    def test_lowercase_marker_starts_a_block(self) -> None:
        """MTX-5-05: `# test_2:` строчными уезжал в данные предыдущего блока.

        Следствие двойное и оба раза немое: ожидание первого кейса разрасталось
        чужими строками, а второй кейс исчезал вовсе.
        """
        blocks = parse_testblock_file("# TEST_1:\n5\n# test_2:\n7\n")

        assert blocks == ["5", "7"]

    @pytest.mark.parametrize("marker", ["# Test_2:", "# TEST_2:", "#test_2:", "# tEsT_2:"])
    def test_marker_case_and_spacing_variants(self, marker: str) -> None:
        """Регистр и пробел после решётки на разбор не влияют."""
        assert parse_testblock_file(f"# TEST_1:\n5\n{marker}\n7\n") == ["5", "7"]

    def test_gap_in_numbering_is_reported(self) -> None:
        """PY-1-03: `# TEST_1:` и `# TEST_7:` дают два кейса, а не семь.

        Спаривание позиционное и таким остаётся — номера в input и output могут
        не совпадать, а рассинхрон стоил бы вердикта. Но автор набора, написавший
        такие маркеры, думает, что кейсов семь, и должен об этом узнать.
        """
        with pytest.warns(UserWarning, match="не подряд"):
            blocks = parse_testblock_file("# TEST_1:\n5\n# TEST_7:\n7\n")

        assert blocks == ["5", "7"], "предупреждение не должно менять разбор"

    def test_sequential_numbering_is_silent(self) -> None:
        """Обычный набор не должен шуметь: предупреждение про подряд идущие номера — ложное."""
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            parse_testblock_file("# TEST_1:\n5\n# TEST_2:\n7\n# TEST_3:\n9\n")

        assert [w for w in caught if "не подряд" in str(w.message)] == []

    def test_numbering_may_start_from_any_number(self) -> None:
        """Набор с 5 по 7 — тоже подряд: важна непрерывность, а не начало с единицы."""
        import warnings as _warnings

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            blocks = parse_testblock_file("# TEST_5:\na\n# TEST_6:\nb\n# TEST_7:\nc\n")

        assert blocks == ["a", "b", "c"]
        assert [w for w in caught if "не подряд" in str(w.message)] == []
