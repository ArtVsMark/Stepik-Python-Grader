"""Списки выбора управляются клавиатурой (issue #922, ``FE-2-08`` · ``FE-3-02``).

Список решений, карточки глоссария и правила рисовались как ``<li>`` с
обработчиком клика — и всё. Ни ``tabindex``, ни роли, ни клавиш: до выбора
решения нельзя было добраться вообще, а значит и до проверки. Это не
неудобство, а недоступность основного пути.

Проверяется поведение, а не разметка: атрибуты можно расставить и не сделать
список работающим. Поэтому тесты нажимают клавиши и смотрят на результат
выбора — и лишь один смотрит на roving tabindex, потому что его следствие
(сколько остановок Tab даёт список) иначе не увидеть.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 10_000


def _open_check_with_solutions(page: Any, e2e_server: str, folder: Path) -> None:
    """Раздел «Проверка» со списком из трёх решений."""
    for i in (1, 2, 3):
        write_task(folder, f"print({i})\n", filename=f"task_{i}.py")
    page.goto(e2e_server + "/")
    # Режим 1 («файл»): только в нём показан список решений и кнопка поиска.
    page.click('.mode-btn[data-mode="file"]')
    page.fill("#path", str(folder))
    page.click("#find-solutions-btn")
    expect(page.locator("#solutions-list li[data-file]")).to_have_count(3, timeout=_TIMEOUT_MS)


class TestSolutionList:
    """Выбор решения — вход в основной путь приложения."""

    def test_the_list_is_one_tab_stop_not_forty(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """В поток табуляции входит ровно один пункт (roving tabindex).

        Иначе список из сорока карточек становится сорока остановками Tab, и
        пройти его насквозь нельзя — доступность списка обернулась бы
        недоступностью всего, что за ним.
        """
        _open_check_with_solutions(page, e2e_server, tmp_path)

        stops = page.locator('#solutions-list li[data-file][tabindex="0"]')

        expect(stops).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_arrows_move_the_selection(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Стрелка вниз выбирает следующее решение — без мыши и без Enter."""
        _open_check_with_solutions(page, e2e_server, tmp_path)
        first = page.locator("#solutions-list li[data-file]").first
        first.focus()

        page.keyboard.press("ArrowDown")

        chosen = page.locator("#solutions-list li.selected")
        expect(chosen).to_have_count(1, timeout=_TIMEOUT_MS)
        assert chosen.get_attribute("data-file") == page.locator(
            "#solutions-list li[data-file]"
        ).nth(1).get_attribute("data-file")

    def test_the_keyboard_keeps_working_after_the_list_is_redrawn(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """Второе нажатие работает так же, как первое.

        Выбор перерисовывает список целиком: без возврата фокуса навигация
        обрывалась бы на первой же стрелке — фокус уходил бы на ``<body>``.
        """
        _open_check_with_solutions(page, e2e_server, tmp_path)
        page.locator("#solutions-list li[data-file]").first.focus()

        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")

        names = page.locator("#solutions-list li[data-file]")
        assert page.locator("#solutions-list li.selected").get_attribute("data-file") == names.nth(
            2
        ).get_attribute("data-file")

    def test_one_enter_selects_once(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Enter выбирает ровно один раз, сколько бы раз список ни рисовали.

        Слушатели живут на контейнере, а он переживает перерисовку: навесить их
        заново на каждый рендер значило бы выполнять выбор столько раз, сколько
        было рендеров.
        """
        _open_check_with_solutions(page, e2e_server, tmp_path)
        # Три перерисовки подряд — столько же было бы и лишних слушателей.
        for _ in range(3):
            page.evaluate(
                "async () => { const m = await import('/static/grade.js');"
                " if (m.renderSolutions) m.renderSolutions(); }"
            )
        page.locator("#solutions-list li[data-file]").nth(1).focus()

        page.keyboard.press("Enter")

        expect(page.locator("#solutions-list li.selected")).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_the_mouse_still_works(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Граница с другой стороны: клик выбирает, как и раньше."""
        _open_check_with_solutions(page, e2e_server, tmp_path)

        page.locator("#solutions-list li[data-file]").nth(2).click()

        expect(page.locator("#solutions-list li.selected")).to_have_count(1, timeout=_TIMEOUT_MS)

    def test_a_click_leaves_the_focus_on_the_chosen_item(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """После клика фокус на выбранном пункте, а не на ``<body>``.

        Так ведёт себя нативный ``<select>`` и того же требует WAI-ARIA APG:
        выбранная опция — точка, от которой продолжают стрелками. Ронять фокус
        в никуда нельзя: человек, начавший мышью и продолжающий клавиатурой,
        оказался бы в начале страницы.
        """
        _open_check_with_solutions(page, e2e_server, tmp_path)

        page.locator("#solutions-list li[data-file]").nth(1).click()

        inside = page.evaluate("() => !!document.activeElement.closest('#solutions-list')")
        assert inside, "фокус ушёл из списка после клика"


class TestGlossaryCards:
    """Тот же механизм — тот же результат в другом разделе.

    Глоссарий берётся настоящий, без перехвата: в нём больше тысячи карточек,
    и это лучшая иллюстрация того, зачем нужен roving tabindex — без него
    раздел стал бы тысячей остановок Tab.
    """

    def _open(self, page: Any, e2e_server: str) -> None:
        page.goto(e2e_server + "/")
        page.click('.sidebar-item[data-section="glossary"]')
        page.wait_for_selector("#glossary-cards li[data-id]", timeout=_TIMEOUT_MS)

    def test_arrows_choose_a_card(self, page: Any, e2e_server: str) -> None:
        self._open(page, e2e_server)
        items = page.locator("#glossary-cards li[data-id]")
        second = items.nth(1).get_attribute("data-id")
        items.first.focus()

        page.keyboard.press("ArrowDown")

        expect(page.locator("#glossary-cards li.selected")).to_have_attribute(
            "data-id", second, timeout=_TIMEOUT_MS
        )

    def test_the_whole_list_is_one_tab_stop(self, page: Any, e2e_server: str) -> None:
        """Больше тысячи карточек — и ровно одна остановка табуляции."""
        self._open(page, e2e_server)

        assert page.locator("#glossary-cards li[data-id]").count() > 100
        expect(page.locator('#glossary-cards li[data-id][tabindex="0"]')).to_have_count(
            1, timeout=_TIMEOUT_MS
        )

    def test_the_container_announces_itself_as_a_listbox(self, page: Any, e2e_server: str) -> None:
        """Скринридеру нужно знать, что это список выбора, а не просто список."""
        self._open(page, e2e_server)

        assert page.get_attribute("#glossary-cards", "role") == "listbox"
        assert page.get_attribute("#glossary-cards li[data-id]", "role") == "option"
