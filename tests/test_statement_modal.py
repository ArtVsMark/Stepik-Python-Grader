"""Окно с условием задачи — разметка, стили, локали (issue #1178).

Браузера в облачной сессии нет постоянно, поэтому здесь проверяется то, что
проверяемо статически: разметка объявлена, строки не захардкожены в JS, стили
опираются на существующие токены, а общий помощник модалок экспортирован и
используется вместо четвёртой копии focus-trap.

**Чего эти тесты не доказывают:** что окно ведёт себя правильно в браузере.
Прогон в Chromium делался отдельно (см. PR) и нашёл дефект, который статикой не
виден вовсе — фокус не возвращался на кнопку после закрытия кликом по подложке,
потому что браузер доигрывал последовательность клика уже после `close()`.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

_STATIC = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "static"


@pytest.fixture(scope="module")
def index_html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_css() -> str:
    return (_STATIC / "app.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def core_js() -> str:
    return (_STATIC / "core.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def statement_js() -> str:
    return (_STATIC / "statement.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ui_locale() -> dict[str, dict[str, str]]:
    return json.loads((_STATIC / "locales" / "ui.json").read_text(encoding="utf-8"))


class TestMarkup:
    def test_button_exists_and_starts_disabled(self, index_html: str) -> None:
        """До того как известна задача, показывать нечего."""
        match = re.search(r'<button id="statement-open"[^>]*>', index_html, re.DOTALL)
        assert match, "кнопки «Условие задачи» нет в разметке"
        assert "disabled" in match.group(0)

    def test_button_lives_in_the_mode_one_block(self, index_html: str) -> None:
        """Рядом с «Отправить в Stepik»: этот блок виден только в режиме 1.

        Если кнопка уедет в шапку, она появится во всех режимах — а условие
        показывается для одной выбранной задачи.
        """
        submit_at = index_html.index('id="stepik-submit"')
        statement_at = index_html.index('id="statement-open"')
        assert abs(statement_at - submit_at) < 1200, "кнопка ушла из блока режима 1"

    def test_dialog_has_the_aria_contract(self, index_html: str) -> None:
        block = index_html[index_html.index('id="statement-overlay"') :][:800]
        assert 'role="dialog"' in block
        assert 'aria-modal="true"' in block
        assert 'aria-labelledby="statement-title"' in block

    def test_close_button_has_an_accessible_name(self, index_html: str) -> None:
        block = index_html[index_html.index('id="statement-close-x"') :][:300]
        assert "aria-label" in block
        assert "data-i18n-aria-label" in block, "подпись закрытия не переводится"

    @pytest.mark.parametrize(
        "element", ["statement-crumbs", "statement-body", "statement-attachments"]
    )
    def test_containers_exist(self, index_html: str, element: str) -> None:
        assert f'id="{element}"' in index_html

    def test_attachments_start_hidden(self, index_html: str) -> None:
        """У задачи без вложений пустой блок с рамкой выглядел бы обрывком."""
        block = index_html[index_html.index('id="statement-attachments"') :][:200]
        assert "hidden" in block


class TestStyles:
    def test_modifier_is_wider_than_the_base_modal(self, app_css: str) -> None:
        """Базовые 560px — ширина диалога согласия; таблицы примеров в неё не лезут."""
        assert ".modal.statement" in app_css
        block = app_css[app_css.index(".modal.statement") :][:400]
        assert "860px" in block

    def test_scroll_lives_inside_the_window(self, app_css: str) -> None:
        """Иначе длинное условие растягивает диалог и смещает страницу под ним."""
        block = app_css[app_css.index(".statement-body") :][:300]
        assert "overflow-y: auto" in block

    def test_images_do_not_overflow(self, app_css: str) -> None:
        block = app_css[app_css.index(".statement-body img") :][:200]
        assert "max-width: 100%" in block

    def test_example_table_scrolls_on_its_own(self, app_css: str) -> None:
        """Единственное, что реально приезжает шире окна, — таблица примеров."""
        block = app_css[app_css.index(".statement-body table") :][:200]
        assert "overflow-x: auto" in block

    def test_every_colour_comes_from_a_token(self, app_css: str) -> None:
        """Хардкод цвета ломает тёмную тему — она собрана на токенах."""
        block = app_css[app_css.index("УСЛОВИЕ ЗАДАЧИ") :]
        literals = re.findall(r":\s*(#[0-9a-fA-F]{3,8}|rgb\(|oklch\()", block)
        assert not literals, f"цвет мимо токенов: {literals}"

    def test_tokens_used_are_declared(self, app_css: str) -> None:
        """Выдуманный токен молча даёт пустое значение, а не ошибку."""
        block = app_css[app_css.index("УСЛОВИЕ ЗАДАЧИ") :]
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", block))
        missing = sorted(tok for tok in used if f"{tok}:" not in app_css)
        assert not missing, f"токены не объявлены: {missing}"


class TestSharedModalHelper:
    """Четвёртой копии focus-trap быть не должно (issue #1225)."""

    def test_helper_is_exported(self, core_js: str) -> None:
        assert "function openModal(" in core_js
        assert re.search(r"^\s*openModal,\s*$", core_js, re.MULTILINE), "openModal не в экспорте"

    def test_statement_uses_the_helper(self, statement_js: str) -> None:
        assert "openModal" in statement_js

    def test_statement_has_no_own_focus_trap(self, statement_js: str) -> None:
        """Свой перехват Tab/Escape здесь означал бы, что помощник не используется."""
        assert "shiftKey" not in statement_js
        assert 'key === "Escape"' not in statement_js

    def test_helper_listens_on_document_not_overlay(self, core_js: str) -> None:
        """Фикс #804: после клика по подложке фокус на body, и до оверлея
        keydown уже не всплывает — Escape и Tab становятся мёртвыми."""
        block = core_js[core_js.index("function openModal(") :][:2600]
        assert 'document.addEventListener("keydown"' in block
        assert 'overlay.addEventListener("keydown"' not in block

    def test_focus_is_restored_asynchronously(self, core_js: str) -> None:
        """Синхронный возврат фокуса съедается доигрыванием клика по подложке.

        Найдено прогоном в браузере: после Escape фокус возвращался, после
        клика по подложке — нет.
        """
        block = core_js[core_js.index("function openModal(") :][:2600]
        assert "setTimeout(() => returnFocus.focus(), 0)" in block

    def test_stops_are_computed_at_keypress(self, core_js: str) -> None:
        """Содержимое окна дорисовывается позже — условие приезжает по сети."""
        block = core_js[core_js.index("function openModal(") :][:2600]
        assert "const stops = () =>" in block, "список остановок зафиксирован при открытии"


class TestLocales:
    KEYS = (
        "statement.open",
        "statement.open_title",
        "statement.none_title",
        "statement.title",
        "statement.close_aria",
        "statement.step",
        "statement.attachments",
        "statement.attachment_missing",
    )

    @pytest.mark.parametrize("key", KEYS)
    def test_key_exists_in_both_languages(self, ui_locale: dict, key: str) -> None:
        for lang in ("ru", "en"):
            assert key in ui_locale[lang], f"{key} нет в локали {lang}"

    def test_translations_differ(self, ui_locale: dict) -> None:
        """Скопированная русская строка в `en` — не перевод, а забытый пункт."""
        same = [
            key
            for key in self.KEYS
            if ui_locale["ru"][key] == ui_locale["en"][key] and not ui_locale["ru"][key].isascii()
        ]
        assert not same, f"не переведено: {same}"

    # Проверки «нет текста в JS» здесь намеренно нет: этот инвариант сторожит
    # `scripts/check_ui_locale_guardrails.py` — он разбирает литералы, а не
    # ищет кириллицу регуляркой. Первая редакция теста делала второе и краснела
    # на русских КОММЕНТАРИЯХ, которых проект как раз требует.


class TestWiring:
    def test_module_is_reachable_from_grade(self) -> None:
        """Иначе кнопка есть в разметке, но её никто не включает."""
        grade = (_STATIC / "grade.js").read_text(encoding="utf-8")
        assert "./statement.js" in grade
        assert "refreshStatementButton" in grade

    def test_changing_task_resets_the_statement(self) -> None:
        """Прежнее условие к новой задаче не относится."""
        grade = (_STATIC / "grade.js").read_text(encoding="utf-8")
        reset_at = grade.index("function resetFilePicker(")
        assert "resetStatement()" in grade[reset_at : reset_at + 500]

    def test_body_is_not_escaped_twice(self, statement_js: str) -> None:
        """Тело приходит уже очищенным сервером (#1177).

        `esc()` на нём показал бы разметку текстом — таблицы примеров и код
        превратились бы в мешанину из `&lt;table&gt;`. Второй whitelist на
        клиенте тоже не нужен: две реализации разойдутся, и JS-версия обходится
        проще.
        """
        assert "body.innerHTML = data.html" in statement_js
        assert "esc(data.html)" not in statement_js
