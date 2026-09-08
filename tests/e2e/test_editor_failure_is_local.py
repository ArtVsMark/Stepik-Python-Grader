"""Пропажа редактора стоит редактора, а не всей страницы (issue #922, ``FE-3-05``).

Бандл CodeMirror импортировался статически в ``core.js``, а ``core.js``
импортируют все остальные модули. Значит один недостающий файл останавливал
выполнение всего приложения: боковое меню оставалось на экране (оно в
статическом HTML), но не реагировало ни на что — ни глоссарий, ни правила, ни
навигация. Причём молча: ни сообщения, ни подсказки, что делать.

Редактор нужен ровно двум разделам, и его отсутствие обязано стоить ровно этих
двух разделов. Даже в них работа не прекращается: код вводится в запасное поле,
проверка и запуск идут как обычно.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 15_000


def _break_the_bundle(page: Any) -> None:
    """Бандл редактора недоступен — так же, как при битой установке."""
    page.route(
        "**/vendor/codemirror-bundle@6.mjs",
        lambda route: route.fulfill(status=404, content_type="text/plain", body="not found"),
    )


class TestTheRestOfTheAppSurvives:
    """Главное: приложение поднимается и работает."""

    def test_other_sections_still_open(self, page: Any, e2e_server: str) -> None:
        """Глоссарий открывается и наполняется без редактора.

        До правки клик по разделу не делал ничего: JS не выполнился вовсе.
        """
        _break_the_bundle(page)
        page.goto(e2e_server + "/")

        page.click('.sidebar-item[data-section="glossary"]')

        page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)
        expect(page.locator("#glossary-cards li[data-id]").first).to_be_visible(timeout=_TIMEOUT_MS)

    def test_a_check_still_runs(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """И основной путь — проверка решения — доходит до результата."""
        _break_the_bundle(page)
        write_task(tmp_path, "print(int(input()) + 1)\n")
        page.goto(e2e_server + "/")

        page.fill("#path", str(tmp_path))
        page.click("#run")

        expect(page.locator("#out table").first).to_be_visible(timeout=_TIMEOUT_MS)


class TestTheEditorDegradesInsteadOfDying:
    """В самих двух разделах вводить код по-прежнему можно."""

    def _open_file_mode(self, page: Any, e2e_server: str) -> None:
        page.goto(e2e_server + "/")
        page.click('.mode-btn[data-mode="file"]')

    def test_a_plain_input_takes_its_place(self, page: Any, e2e_server: str) -> None:
        _break_the_bundle(page)
        self._open_file_mode(page, e2e_server)

        area = page.locator("textarea.plain-editor")
        expect(area).to_be_visible(timeout=_TIMEOUT_MS)
        area.fill("print(42)\n")
        assert area.input_value() == "print(42)\n"

    def test_the_swap_is_said_out_loud(self, page: Any, e2e_server: str) -> None:
        """Молчаливая замена читалась бы как «редактор сломался и стал хуже».

        Человеку надо знать, что именно пропало и что остальное работает.
        """
        _break_the_bundle(page)
        self._open_file_mode(page, e2e_server)

        expect(page.locator(".plain-editor-note")).to_be_visible(timeout=_TIMEOUT_MS)


class TestNothingChangedWhenTheBundleIsThere:
    """Граница с другой стороны: обычная установка работает как раньше."""

    def test_the_real_editor_is_mounted(self, page: Any, e2e_server: str) -> None:
        page.goto(e2e_server + "/")
        page.click('.mode-btn[data-mode="file"]')

        expect(page.locator("#solution-editor .cm-content")).to_be_visible(timeout=_TIMEOUT_MS)
        assert page.locator("textarea.plain-editor").count() == 0
