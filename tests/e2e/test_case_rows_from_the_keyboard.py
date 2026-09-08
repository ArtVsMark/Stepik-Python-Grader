"""Таблица результатов проходима с клавиатуры (issue #922, ``FE-3-02``).

Раскрытие строки висело на ``<td class="file-cell" data-toggle>``, а выбор
кейса — на ``<tr class="case-row">``. Ни то, ни другое не получает фокус, не
понимает Enter и ничего не сообщает скринридеру: разбор упавшего кейса —
центральная работа приложения — был доступен только мышью.

Проверяется поведение: нажать Tab до элемента и Enter по нему, а затем
посмотреть, раскрылась ли строка и открылся ли разбор. Разметку смотрит ровно
один тест — ``aria-expanded``, потому что его следствие (что именно слышит
человек) иначе не увидеть.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 15_000


def _run_a_check(page: Any, e2e_server: str, folder: Path) -> None:
    """Прогнать проверку и дождаться таблицы результатов."""
    write_task(folder, "print(int(input()) + 1)\n")
    page.goto(e2e_server + "/")
    page.fill("#path", str(folder))
    page.click("#run")
    expect(page.locator("button.row-toggle")).to_have_count(1, timeout=_TIMEOUT_MS)


class TestExpandingARow:
    """Список кейсов раскрывается без мыши."""

    def test_enter_opens_the_cases(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        _run_a_check(page, e2e_server, tmp_path)
        toggle = page.locator("button.row-toggle").first
        toggle.focus()

        page.keyboard.press("Enter")

        expect(page.locator("tr.caserow.is-open")).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_the_state_is_announced_not_just_drawn(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """``aria-expanded`` меняется вместе с картинкой.

        Без него скринридер читает кнопку одинаково в обоих состояниях, и
        человек не знает, раскрыл он строку или свернул.
        """
        _run_a_check(page, e2e_server, tmp_path)
        toggle = page.locator("button.row-toggle").first

        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "false"


class TestChoosingACase:
    """Разбор кейса — то, ради чего приложение и открывают."""

    def _open_cases(self, page: Any, e2e_server: str, folder: Path) -> None:
        _run_a_check(page, e2e_server, folder)
        page.locator("button.row-toggle").first.click()
        expect(page.locator("button.case-pick")).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_enter_opens_the_breakdown(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        self._open_cases(page, e2e_server, tmp_path)
        pick = page.locator("button.case-pick").first
        pick.focus()

        page.keyboard.press("Enter")

        expect(page.locator("tr.case-row.selected")).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_the_chosen_case_is_marked_for_a_screen_reader(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """Выбранный кейс помечен ``aria-current``, а не только подсветкой."""
        self._open_cases(page, e2e_server, tmp_path)

        page.locator("button.case-pick").first.click()

        expect(page.locator('button.case-pick[aria-current="true"]')).to_have_count(
            1, timeout=_TIMEOUT_MS
        )

    def test_a_click_selects_once_not_twice(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """Кнопка внутри строки гасит всплытие.

        Обработчик остался и на строке — мышью так удобнее. Без остановки
        всплытия один клик выбирал бы кейс дважды и дважды перерисовывал панель
        разбора.
        """
        self._open_cases(page, e2e_server, tmp_path)
        page.evaluate(
            """() => {
                window.__picks = 0;
                document.querySelectorAll('tr.case-row').forEach(tr =>
                    tr.addEventListener('click', () => { window.__picks += 1; }, true));
            }"""
        )

        page.locator("button.case-pick").first.click()

        assert page.evaluate("() => window.__picks") == 1


class TestTheTableIsStillATable:
    """Граница с другой стороны: семантика таблицы не принесена в жертву."""

    def test_rows_did_not_become_buttons(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """У строк нет чужих ролей — кнопка живёт ВНУТРИ ячейки.

        Проще всего было бы навесить ``role="button"`` и ``tabindex`` на
        ``<tr>``, и клавиатура заработала бы — ценой того, что таблица
        перестала бы быть таблицей для тех, кто читает её скринридером.
        """
        _run_a_check(page, e2e_server, tmp_path)

        assert page.locator("tr[role]").count() == 0
        assert page.locator("tr[tabindex]").count() == 0
