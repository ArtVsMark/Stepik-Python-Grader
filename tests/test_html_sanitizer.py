"""Тесты очистки условия перед показом в браузере (issue #1177).

Это защитный код, поэтому тесты написаны как попытки пролезть, а не как сверка
вывода с эталоном. Провал любого из них означает XSS на странице, которая ходит
в локальный API грейдера — а там лежит рабочий токен Stepik.

Модуль ``core/html_sanitizer.py`` — leaf на stdlib, зовётся напрямую.
"""

from __future__ import annotations

import pytest

from stepik_grader.core.html_sanitizer import sanitize_statement_html as clean


class TestScriptingIsRemoved:
    """Всё, что умеет исполняться, до вывода не доходит."""

    def test_script_tag_and_its_body_are_gone(self) -> None:
        out = clean("<p>до</p><script>alert(1)</script><p>после</p>")
        assert "alert" not in out
        assert "script" not in out
        assert "до" in out and "после" in out

    def test_style_body_is_gone(self) -> None:
        """CSS сам по себе вектор, а тело `<style>` приходит обычным handle_data."""
        out = clean("<style>body{background:url(javascript:1)}</style><p>ok</p>")
        assert "javascript" not in out
        assert "background" not in out

    @pytest.mark.parametrize("tag", ["iframe", "object", "embed", "form", "svg", "noscript"])
    def test_embedded_widgets_are_dropped_with_content(self, tag: str) -> None:
        out = clean(f"<{tag}>внутри</{tag}><p>после</p>")
        assert "внутри" not in out
        assert "после" in out

    @pytest.mark.parametrize(
        "attr",
        ["onclick", "onload", "onerror", "onmouseover", "onfocus", "onpointerrawupdate"],
    )
    def test_event_handlers_never_survive(self, attr: str) -> None:
        """Отсекаются не правилом про `on*`, а тем, что их никто не разрешал.

        Поэтому в списке есть и `onpointerrawupdate` — обработчик, которого
        никакой чёрный список не предугадал бы.
        """
        out = clean(f'<p {attr}="alert(1)">текст</p>')
        assert attr not in out
        assert "alert" not in out
        assert "текст" in out

    def test_comments_are_dropped(self) -> None:
        assert "секрет" not in clean("<!-- секрет --><p>ok</p>")

    def test_escaped_markup_stays_escaped(self) -> None:
        """`&lt;script&gt;` в источнике — это ТЕКСТ, и обязан остаться текстом.

        ``convert_charrefs`` раскодирует его в живой `<script>`, поэтому вывод
        экранируется обратно. Без этого источник обходил бы очистку двойным
        кодированием.
        """
        out = clean("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out


class TestUrlSchemes:
    """Схема ссылки проверяется белым списком, а не запретом известных плохих."""

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox",
            "jar:http://x!/y",
            "blob:http://x/y",
            "file:///etc/passwd",
        ],
    )
    def test_dangerous_scheme_loses_the_href(self, url: str) -> None:
        out = clean(f'<a href="{url}">клик</a>')
        assert "href" not in out
        assert "клик" in out, "текст ссылки не должен пропадать вместе с адресом"

    @pytest.mark.parametrize("url", ["https://example.com/x", "http://example.com", "mailto:a@b.c"])
    def test_safe_scheme_survives(self, url: str) -> None:
        assert f'href="{url}"' in clean(f'<a href="{url}">клик</a>')

    @pytest.mark.parametrize("url", ["files.txt", "/media/x", "#anchor", "./a/b"])
    def test_relative_url_survives(self, url: str) -> None:
        """У относительной ссылки схемы нет — значит, нет и `javascript:`."""
        assert f'href="{url}"' in clean(f'<a href="{url}">клик</a>')

    def test_external_link_gets_rel(self) -> None:
        out = clean('<a href="https://example.com">внешняя</a>')
        assert 'rel="noopener noreferrer"' in out

    def test_quote_in_url_cannot_break_out_of_the_attribute(self) -> None:
        """Кавычка внутри значения обязана остаться экранированной.

        ``convert_charrefs`` раскодирует ``&quot;`` в настоящую кавычку ещё до
        нас, поэтому значение атрибута приходит уже «разорванным»:
        ``https://x/" onmouseover="alert(1)``. Если вывести его как есть,
        кавычка закроет ``href`` и всё после неё станет вторым атрибутом.

        Проверяем именно вывод: `&quot;` на месте, а `onmouseover=` не стоит
        рядом с настоящей кавычкой, то есть атрибутом не является.
        """
        out = clean('<a href="https://x/&quot; onmouseover=&quot;alert(1)">клик</a>')

        assert "&quot;" in out, "кавычка в значении вышла наружу неэкранированной"
        assert 'onmouseover="' not in out
        assert out.count('"') % 2 == 0, f"нечётное число кавычек — атрибут разорван: {out}"


class TestImages:
    """Картинка выживает, только если указывает на приехавшее вложение."""

    LOCAL = {"https://stepik.org/media/attachments/1/pic.png": "/api/task/image?name=pic.png"}

    def test_local_attachment_is_substituted(self) -> None:
        out = clean(f'<img src="{next(iter(self.LOCAL))}" alt="схема">', self.LOCAL)
        assert 'src="/api/task/image?name=pic.png"' in out
        assert 'alt="схема"' in out

    def test_external_image_becomes_its_alt_text(self) -> None:
        """CSP её всё равно заблокирует; пустота на месте иллюстрации хуже подписи."""
        out = clean('<img src="https://evil/x.png" alt="схема">', self.LOCAL)
        assert "<img" not in out
        assert "evil" not in out
        assert "схема" in out

    def test_external_image_without_alt_disappears_entirely(self) -> None:
        assert clean('<img src="https://evil/x.png">', self.LOCAL) == ""

    def test_no_map_means_no_images(self) -> None:
        assert "<img" not in clean('<img src="https://stepik.org/media/attachments/1/pic.png">')


class TestAllowedMarkupSurvives:
    """Очистка не должна съедать само условие."""

    @pytest.mark.parametrize(
        "tag", ["p", "h2", "ul", "li", "pre", "code", "strong", "em", "blockquote", "table"]
    )
    def test_whitelisted_tag_survives(self, tag: str) -> None:
        assert f"<{tag}>" in clean(f"<{tag}>содержимое</{tag}>")

    def test_table_structure_is_kept(self) -> None:
        out = clean("<table><tbody><tr><th>Ввод</th><td>4</td></tr></tbody></table>")
        for tag in ("<table>", "<tbody>", "<tr>", "<th>", "<td>"):
            assert tag in out

    def test_colspan_survives_only_as_a_number(self) -> None:
        out = clean('<table><tr><td colspan="2">a</td><td colspan="alert(1)">b</td></tr></table>')
        assert 'colspan="2"' in out
        assert "alert" not in out

    def test_unknown_tag_unwraps_keeping_text(self) -> None:
        """Незнакомая вёрстка теряет оформление, но не смысл."""
        out = clean('<div class="widget"><p>внутри</p></div>')
        assert "<div" not in out
        assert "<p>внутри</p>" in out

    @pytest.mark.parametrize("attr", ["class", "id", "style", "data-x"])
    def test_presentation_attributes_are_dropped(self, attr: str) -> None:
        assert attr not in clean(f'<p {attr}="x">текст</p>')


class TestWellFormedOutput:
    """Фрагмент уезжает внутрь чужой страницы — он не вправе ломать её разметку."""

    def test_unclosed_tag_is_closed(self) -> None:
        out = clean("<p>текст")
        assert out.endswith("</p>")

    def test_unclosed_table_does_not_swallow_the_page(self) -> None:
        out = clean("<table><tr><td>a")
        assert out.count("<table>") == out.count("</table>") == 1

    def test_stray_closing_tag_is_ignored(self) -> None:
        """Иначе в выводе появился бы `</div>`, закрывающий чужой контейнер."""
        assert clean("</div></p></table>") == ""

    def test_self_closing_tags_are_not_lost(self) -> None:
        """HTMLParser зовёт для них отдельный хук и НЕ зовёт handle_endtag."""
        out = clean("<br/><hr/>")
        assert "<br>" in out
        assert "<hr>" in out

    def test_void_tags_get_no_closing_pair(self) -> None:
        assert "</br>" not in clean("<p>а<br>б</p>")

    @pytest.mark.parametrize(
        "broken",
        ["<p>текст", "<table><tr><td>a", "<a href=>x</a>", "<<>>", "<p><b>x</p></b>"],
    )
    def test_broken_html_does_not_raise(self, broken: str) -> None:
        clean(broken)


class TestEmptyInput:
    def test_empty_string(self) -> None:
        assert clean("") == ""

    def test_plain_text_passes_through(self) -> None:
        """Откат на `task.md` старого формата не должен ничего портить."""
        assert clean("просто текст") == "просто текст"
