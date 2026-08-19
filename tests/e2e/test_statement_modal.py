"""Окно с условием задачи в настоящем браузере (issue #1178).

Часть контракта этого окна статикой не проверяется вовсе — только движком
событий браузера. Прогон здесь нашёл дефект, которого не видно ни в разметке,
ни в юнит-тестах: фокус не возвращался на кнопку после закрытия **кликом по
подложке**, потому что браузер доигрывает mouseup/click по уже скрытому
оверлею уже после `close()`, и синхронно поставленный фокус тут же сбрасывался
на `body`. После `Escape` тот же код работал правильно — то есть половина
сценариев проходила, а половина молча ломалась.

Здесь же проверяется главное свойство очистки: скрипт из условия не выполняется.
Контекст создаётся с `bypass_csp=True` намеренно — CSP это вторая линия обороны,
и без неё проверка отвечает на вопрос «держит ли САМ санитайзер», а не «держит
ли хоть что-нибудь».
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._helpers import write_task

_PIC_URL = "https://stepik.org/media/attachments/1/pic.png"

#: В условии намеренно лежит и скрипт, и `onclick`: первый отсекается тем, что
#: тег не в whitelist, второй — тем, что атрибут не разрешён. Это разные ветки
#: очистки, и проверять их надо по отдельности.
_STATEMENT_HTML = (
    "<h2>Сумма чисел</h2>"
    "<p>Дано число <code>n</code>. Выведите <code>n + 1</code>.</p>"
    "<script>window.__pwned = true;</script>"
    '<p id="lure" onclick="window.__pwned = true">Кликни меня</p>'
    "<table><tbody><tr><th>Ввод</th><th>Вывод</th></tr>"
    "<tr><td>4</td><td>5</td></tr><tr><td>10</td><td>11</td></tr></tbody></table>"
    + "<p>Строка для прокрутки.</p>"
    * 60
)


@pytest.fixture
def statement_page(browser: Any, e2e_server: str, tmp_path: Path) -> Iterator[tuple[Any, str]]:
    """Страница в режиме 1 с выбранной задачей, у которой есть условие."""
    task = tmp_path / "04-summa"
    task.mkdir()
    write_task(task, "n = int(input())\nprint(n + 1)\n", filename="task4_1.py")
    (task / "task.html").write_text(_STATEMENT_HTML, encoding="utf-8")
    (task / "files.txt").write_text("данные", encoding="utf-8")
    (task / "meta.json").write_text(
        json.dumps(
            {
                "course_title": "Поколение Python",
                "section_title": "Списки",
                "lesson_title": "Урок 3",
                "step_title": "Сумма",
                "step_position": 4,
                "attachments": [
                    {"name": "files.txt", "url": _PIC_URL + "?a", "status": "saved"},
                    {"name": "data.csv", "url": _PIC_URL + "?b", "status": "failed"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # bypass_csp — ради самого харнесса: CSP страницы запрещает eval, которым
    # Playwright вычисляет предикаты. Побочно проверка становится СТРОЖЕ —
    # вторая линия обороны выключена, отвечает один санитайзер.
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(getattr(exc, "stack", None) or str(exc)))

    page.goto(e2e_server, wait_until="networkidle")
    page.click('button[data-mode="file"]')
    page.fill("#path", str(task))
    page.click("#find-solutions-btn")
    page.wait_for_selector("#solutions-list li", timeout=15000)
    page.wait_for_function(
        "() => !document.querySelector('#statement-open').disabled", timeout=15000
    )
    try:
        yield page, str(task)
    finally:
        context.close()
        assert not errors, "непойманные ошибки JS на странице:\n" + "\n".join(errors)


def _open(page: Any) -> None:
    page.click("#statement-open")
    page.wait_for_selector("#statement-overlay:not([hidden])", timeout=10000)


class TestButtonAvailability:
    def test_enabled_when_the_statement_exists(self, statement_page: tuple[Any, str]) -> None:
        page, _ = statement_page
        assert page.evaluate("() => !document.querySelector('#statement-open').disabled")

    def test_hidden_outside_mode_one(self, statement_page: tuple[Any, str]) -> None:
        """Условие относится к одной задаче, а не к папке решений."""
        page, _ = statement_page
        page.click('button[data-mode="tests"]')
        assert not page.is_visible("#statement-open")


class TestSanitizationHolds:
    """Условие — чужой HTML; здесь проверяется, что он остаётся текстом."""

    def test_script_from_the_statement_does_not_run(self, statement_page: tuple[Any, str]) -> None:
        page, _ = statement_page
        _open(page)
        assert page.evaluate("() => window.__pwned === undefined")

    def test_inline_handler_does_not_fire(self, statement_page: tuple[Any, str]) -> None:
        """`onclick` отсекается тем, что атрибут не разрешён, — а не списком `on*`."""
        page, _ = statement_page
        _open(page)
        page.click("#statement-body p >> nth=0")
        assert page.evaluate("() => window.__pwned === undefined")


class TestContent:
    def test_example_table_survives(self, statement_page: tuple[Any, str]) -> None:
        page, _ = statement_page
        _open(page)
        assert page.locator("#statement-body table td").count() >= 4

    def test_breadcrumbs_are_filled(self, statement_page: tuple[Any, str]) -> None:
        page, _ = statement_page
        _open(page)
        assert "Поколение Python" in page.inner_text("#statement-crumbs")

    def test_missing_attachment_is_marked_by_a_word(self, statement_page: tuple[Any, str]) -> None:
        """Цветом одним нельзя: он не читается при дальтонизме и не озвучивается."""
        page, _ = statement_page
        _open(page)
        text = page.inner_text("#statement-attachments")
        assert "data.csv" in text
        assert "не приехал" in text


class TestScrolling:
    def test_long_statement_scrolls_inside_the_window(
        self, statement_page: tuple[Any, str]
    ) -> None:
        page, _ = statement_page
        _open(page)
        moved = page.evaluate(
            """() => {
              const body = document.querySelector('#statement-body');
              const before = window.scrollY;
              body.scrollTop = 400;
              return { inner: body.scrollTop > 0, pageMoved: window.scrollY !== before };
            }"""
        )
        assert moved["inner"], "тело условия не прокручивается"
        assert not moved["pageMoved"], "страница под окном сместилась"


class TestKeyboardAndFocus:
    """Ровно тот класс, который статикой не проверяется."""

    def test_escape_closes_and_returns_focus(self, statement_page: tuple[Any, str]) -> None:
        page, _ = statement_page
        _open(page)
        page.keyboard.press("Escape")
        page.wait_for_selector("#statement-overlay", state="hidden", timeout=5000)
        page.wait_for_timeout(100)
        assert page.evaluate("() => document.activeElement?.id") == "statement-open"

    def test_backdrop_click_closes_and_returns_focus(self, statement_page: tuple[Any, str]) -> None:
        """Дефект, найденный этим прогоном.

        `close()` приходит на `mousedown`, а браузер после нас доигрывает
        mouseup/click по уже скрытому оверлею — синхронно поставленный фокус
        сбрасывался на `body`. Возврат фокуса отложен на следующий такт.
        """
        page, _ = statement_page
        _open(page)
        box = page.locator("#statement-overlay").bounding_box()
        page.mouse.click(box["x"] + 5, box["y"] + 5)
        page.wait_for_selector("#statement-overlay", state="hidden", timeout=5000)
        page.wait_for_timeout(100)

        assert page.evaluate("() => document.activeElement?.id") == "statement-open"

    def test_tab_does_not_leave_the_dialog(self, statement_page: tuple[Any, str]) -> None:
        """Без удержания Tab уходит на страницу под оверлеем, где можно нажимать кнопки."""
        page, _ = statement_page
        _open(page)
        for _ in range(8):
            page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.querySelector('#statement-overlay').contains(document.activeElement)"
        )

    def test_escape_still_works_after_reopening(self, statement_page: tuple[Any, str]) -> None:
        """Слушатели снимаются при закрытии — иначе второе окно копит их."""
        page, _ = statement_page
        _open(page)
        page.keyboard.press("Escape")
        page.wait_for_selector("#statement-overlay", state="hidden", timeout=5000)
        _open(page)
        page.keyboard.press("Escape")
        page.wait_for_selector("#statement-overlay", state="hidden", timeout=5000)

        assert not page.is_visible("#statement-overlay")
