"""Тесты конвертера условия HTML -> Markdown (issue #1176).

Модуль ``core/html_to_markdown.py`` — leaf на stdlib, поэтому тесты зовут его
напрямую: ни сети, ни файловой системы, ни моков.

Проверяется то, ради чего конвертер написан, — что условие читается без
разметки, а не что вывод совпал с эталонной строкой байт в байт.

Сам leaf-инвариант (нет project-импортов) сторожит канонический реестр
``_LEAF_MODULES`` в ``tests/test_import_dag.py``; второй такой проверки здесь
нет намеренно — два гейта на один инвариант расходятся.
"""

from __future__ import annotations

import pytest

from stepik_grader.core.html_to_markdown import html_to_markdown


class TestBasicBlocks:
    """Абзацы, заголовки, списки — то, из чего состоит любое условие."""

    def test_empty_input_gives_empty_output(self) -> None:
        assert html_to_markdown("") == ""

    def test_whitespace_only_input_gives_empty_output(self) -> None:
        assert html_to_markdown("<p>  </p>\n") == ""

    def test_paragraph_loses_tags_keeps_text(self) -> None:
        assert html_to_markdown("<p>Привет</p>").strip() == "Привет"

    def test_heading_levels_map_to_hashes(self) -> None:
        assert html_to_markdown("<h1>А</h1>").strip() == "# А"
        assert html_to_markdown("<h3>Б</h3>").strip() == "### Б"

    def test_unordered_list_uses_dashes(self) -> None:
        out = html_to_markdown("<ul><li>раз</li><li>два</li></ul>")
        assert "- раз" in out
        assert "- два" in out

    def test_ordered_list_numbers_from_one(self) -> None:
        out = html_to_markdown("<ol><li>раз</li><li>два</li></ol>")
        assert "1. раз" in out
        assert "2. два" in out

    def test_two_ordered_lists_each_restart_numbering(self) -> None:
        """Счётчик живёт на своём уровне стека, а не в конвертере целиком."""
        out = html_to_markdown("<ol><li>а</li></ol><p>текст</p><ol><li>б</li></ol>")
        assert "1. а" in out
        assert "1. б" in out
        assert "2." not in out

    def test_nested_list_is_indented(self) -> None:
        out = html_to_markdown("<ul><li>внешний<ul><li>вложенный</li></ul></li></ul>")
        assert "- внешний" in out
        nested = [line for line in out.split("\n") if "вложенный" in line][0]
        assert nested.startswith(" "), f"вложенный пункт без отступа: {nested!r}"

    def test_blockquote_prefixes_every_line(self) -> None:
        out = html_to_markdown("<blockquote>Подсказка</blockquote>")
        assert out.strip() == "> Подсказка"

    def test_br_becomes_newline(self) -> None:
        assert html_to_markdown("<p>а<br>б</p>").strip() == "а\nб"

    def test_hr_becomes_rule(self) -> None:
        assert "---" in html_to_markdown("<p>а</p><hr><p>б</p>")


class TestReadableWithoutMarkup:
    """Критерий приёмки: `task.md` читается без разметки."""

    STATEMENT = (
        "<h2>Сумма</h2>"
        "<p>Дано число <code>n</code>. Выведите <code>n + 1</code>.</p>"
        "<p>Ограничение: <strong>0 &lt; n &lt; 100</strong>.</p>"
    )

    def test_no_html_tags_survive(self) -> None:
        out = html_to_markdown(self.STATEMENT)
        for tag in ("<p>", "</p>", "<code>", "<strong>", "<h2>"):
            assert tag not in out, f"в выводе осталась разметка {tag}"

    def test_entities_are_decoded(self) -> None:
        out = html_to_markdown(self.STATEMENT)
        assert "&lt;" not in out
        assert "&gt;" not in out
        assert "0 < n < 100" in out

    def test_inline_code_uses_backticks(self) -> None:
        assert "`n`" in html_to_markdown(self.STATEMENT)

    def test_bold_uses_asterisks(self) -> None:
        assert "**0 < n < 100**" in html_to_markdown(self.STATEMENT)

    def test_italic_uses_single_asterisk(self) -> None:
        assert html_to_markdown("<p><em>косой</em></p>").strip() == "*косой*"

    def test_no_trailing_whitespace_on_any_line(self) -> None:
        out = html_to_markdown(self.STATEMENT)
        assert all(line == line.rstrip() for line in out.split("\n"))

    def test_no_triple_blank_lines(self) -> None:
        out = html_to_markdown("<p>а</p><div><div><p>б</p></div></div>")
        assert "\n\n\n" not in out


class TestCodeBlocks:
    """Примеры ввода-вывода должны быть видны блоками, а не слипшимся текстом."""

    def test_pre_becomes_fenced_block(self) -> None:
        out = html_to_markdown("<pre>n = 1\nprint(n)</pre>")
        assert "```\nn = 1\nprint(n)\n```" in out

    def test_pre_preserves_internal_whitespace(self) -> None:
        """Отступы внутри кода значимы — схлопывание пробелов ломало бы Python."""
        out = html_to_markdown("<pre>if x:\n    print(1)</pre>")
        assert "    print(1)" in out

    def test_pre_code_does_not_double_the_fence(self) -> None:
        out = html_to_markdown("<pre><code>x = 1</code></pre>")
        assert "````" not in out
        assert "```\nx = 1\n```" in out

    def test_inline_code_with_backtick_widens_the_fence(self) -> None:
        out = html_to_markdown("<p><code>a`b</code></p>")
        assert "``a`b``" in out

    def test_fence_widens_past_backticks_in_body(self) -> None:
        out = html_to_markdown("<pre>```\nвложенный забор\n```</pre>")
        assert "````" in out

    def test_empty_pre_emits_nothing(self) -> None:
        assert html_to_markdown("<pre>   </pre>") == ""


class TestTables:
    """Критерий приёмки: таблица примеров не разрушена."""

    EXAMPLES = (
        "<table><tbody>"
        "<tr><th>Ввод</th><th>Вывод</th></tr>"
        "<tr><td>4</td><td>5</td></tr>"
        "<tr><td>10</td><td>11</td></tr>"
        "</tbody></table>"
    )

    def test_header_and_separator_present(self) -> None:
        out = html_to_markdown(self.EXAMPLES)
        assert "| Ввод | Вывод |" in out
        assert "| --- | --- |" in out

    def test_every_data_row_survives(self) -> None:
        out = html_to_markdown(self.EXAMPLES)
        assert "| 4 | 5 |" in out
        assert "| 10 | 11 |" in out

    def test_row_count_matches_source(self) -> None:
        out = html_to_markdown(self.EXAMPLES)
        rows = [line for line in out.split("\n") if line.startswith("|")]
        assert len(rows) == 4, "две строки данных, шапка и разделитель"

    def test_table_without_th_still_gets_a_separator(self) -> None:
        """Без строки-разделителя GFM таблицей не считает — вывод слипся бы."""
        out = html_to_markdown(
            "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"
        )
        assert "| --- | --- |" in out

    def test_pipe_in_cell_is_escaped(self) -> None:
        """Неэкранированная черта разрывает строку на лишние колонки."""
        out = html_to_markdown("<table><tr><td>a|b</td><td>c</td></tr></table>")
        assert "a\\|b" in out

    def test_newline_in_cell_becomes_br(self) -> None:
        """Строка GFM-таблицы физически одна — перевод внутри ячейки её рвёт."""
        out = html_to_markdown("<table><tr><td>1<br>2</td><td>x</td></tr></table>")
        assert "1<br>2" in out
        assert "\n" not in out.strip().split("|")[1]

    def test_ragged_rows_are_padded(self) -> None:
        """Строка короче шапки не должна съезжать на колонку влево."""
        out = html_to_markdown("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td></tr></table>")
        widths = {line.count("|") for line in out.split("\n") if line.startswith("|")}
        assert len(widths) == 1, f"строки таблицы разной ширины: {widths}"


class TestLinksAndImages:
    def test_link_becomes_markdown(self) -> None:
        out = html_to_markdown('<p><a href="https://example.com/x">текст</a></p>')
        assert "[текст](https://example.com/x)" in out

    def test_bare_url_stays_bare(self) -> None:
        """`[url](url)` вдвое длиннее и ничего не добавляет."""
        url = "https://example.com/x"
        out = html_to_markdown(f'<p><a href="{url}">{url}</a></p>')
        assert out.strip() == url

    def test_link_without_href_keeps_only_text(self) -> None:
        assert html_to_markdown("<p><a>якорь</a></p>").strip() == "якорь"

    def test_image_becomes_markdown(self) -> None:
        out = html_to_markdown('<p><img src="pic.png" alt="схема"></p>')
        assert "![схема](pic.png)" in out

    def test_image_without_src_emits_nothing(self) -> None:
        assert html_to_markdown('<p><img alt="нет"></p>') == ""


class TestLocalAttachments:
    """Ссылки на приехавшие вложения ведут на файл рядом, а не в сеть (#1112)."""

    URL = "https://stepik.org/media/attachments/lesson/1/files.txt"

    def test_saved_attachment_link_is_rewritten(self) -> None:
        out = html_to_markdown(
            f'<p><a href="{self.URL}">files.txt</a></p>', {self.URL: "files.txt"}
        )
        assert "(files.txt)" in out
        assert self.URL not in out

    def test_rewritten_link_stays_a_link(self) -> None:
        """Подпись совпала с именем файла — но это по-прежнему ссылка.

        Иначе `files.txt` превращается в обычное слово и открыть соседний файл
        из условия уже нельзя.
        """
        out = html_to_markdown(
            f'<p><a href="{self.URL}">files.txt</a></p>', {self.URL: "files.txt"}
        )
        assert "[files.txt](files.txt)" in out

    def test_missing_attachment_keeps_network_url(self) -> None:
        """Не приехало — врать про локальный файл хуже, чем оставить ссылку."""
        out = html_to_markdown(f'<p><a href="{self.URL}">files.txt</a></p>', {})
        assert self.URL in out

    def test_image_src_is_rewritten_too(self) -> None:
        out = html_to_markdown(f'<p><img src="{self.URL}" alt="a"></p>', {self.URL: "files.txt"})
        assert "![a](files.txt)" in out

    def test_no_map_given_is_same_as_empty_map(self) -> None:
        assert html_to_markdown(f'<a href="{self.URL}">f</a>') == html_to_markdown(
            f'<a href="{self.URL}">f</a>', {}
        )


class TestRobustness:
    """Скачивание не имеет права падать из-за вёрстки условия."""

    @pytest.mark.parametrize(
        "broken",
        [
            "<p>текст",
            "<table><tr><td>a",
            "<ul><li>раз",
            "<pre>код",
            "</p></div></table>",
            "<p>a<b>b<i>c</p>",
            "<a href=>текст</a>",
        ],
    )
    def test_broken_html_does_not_raise(self, broken: str) -> None:
        html_to_markdown(broken)

    def test_unclosed_tag_keeps_the_text(self) -> None:
        """Разметки у такого куска не будет, а текст обязан уцелеть."""
        assert "текст" in html_to_markdown("<p>текст<div><table><tr><td>ещё")

    def test_unknown_tag_unwraps_to_its_text(self) -> None:
        assert "внутри" in html_to_markdown("<marquee>внутри</marquee>")

    def test_script_body_never_reaches_output(self) -> None:
        """HTMLParser отдаёт тело <script> обычным handle_data (ср. #838)."""
        out = html_to_markdown('<p>условие</p><script>var x = "мусор";</script>')
        assert "мусор" not in out
        assert "условие" in out

    def test_style_body_never_reaches_output(self) -> None:
        out = html_to_markdown("<style>.a { color: red }</style><p>условие</p>")
        assert "color" not in out
        assert "условие" in out


class TestNonGoals:
    """Границы, объявленные в issue: их нарушение — регрессия, а не улучшение."""

    def test_latex_passes_through_unchanged(self) -> None:
        """Рендер формул — решение по данным Фазы L, не этого PR."""
        out = html_to_markdown("<p>Формула: $$x^2 + y^2$$</p>")
        assert "$$x^2 + y^2$$" in out

    def test_prose_is_not_over_escaped(self) -> None:
        """Сплошные `\\#` ради round-trip убивают ровно ту читаемость, ради
        которой конвертер и написан."""
        out = html_to_markdown("<p>Решётка # и звёздочка * в тексте</p>")
        assert "\\#" not in out
        assert "\\*" not in out
