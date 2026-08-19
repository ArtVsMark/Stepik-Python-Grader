"""Панель навигации по скачанным задачам в браузере (issue #1179).

Главное, что здесь проверяется и чего не видно ни в разметке, ни в юнит-тестах:
**порядок листания**. Алфавит каталогов врёт — `task10` встаёт перед `task9`, —
поэтому дерево строится по иерархии из `meta.json`. Тест на это ставит `task2`,
`task9`, `task10` в один урок: при сортировке по имени папки он покраснеет.

Второе — что стрелка проходит **сквозь границу главы**: шаги листаются сквозным
потоком, и смена главы не теряется, потому что видна в подписи контекста.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._helpers import write_task

_COURSE = {"course_id": 1, "course_title": "Поколение Python"}
_CHAPTER_1 = {
    "section_id": 10,
    "section_title": "Списки",
    "lesson_id": 100,
    "lesson_title": "Урок 3",
}
_CHAPTER_2 = {
    "section_id": 20,
    "section_title": "Словари",
    "lesson_id": 200,
    "lesson_title": "Урок 5",
}


def _make_task(root: Path, folder: str, **meta: Any) -> Path:
    task = root / folder
    task.mkdir(parents=True, exist_ok=True)
    write_task(task, "print(1)\n", stdin="1", expected="1", filename="task1.py")
    (task / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return task


@pytest.fixture
def nav_page(browser: Any, e2e_server: str, tmp_path: Path) -> Iterator[Any]:
    """Страница с панелью навигации над деревом из четырёх задач.

    Порядок папок намеренно спорит с порядком шагов: `task10` по алфавиту
    встаёт перед `task9`, а по иерархии — после.
    """
    downloads = tmp_path / "StepikTasks"
    _make_task(
        downloads,
        "c/s1/l/task2",
        **_COURSE,
        **_CHAPTER_1,
        step_id=2,
        step_position=2,
        step_title="Второй",
    )
    _make_task(
        downloads,
        "c/s1/l/task9",
        **_COURSE,
        **_CHAPTER_1,
        step_id=9,
        step_position=9,
        step_title="Девятый",
    )
    _make_task(
        downloads,
        "c/s1/l/task10",
        **_COURSE,
        **_CHAPTER_1,
        step_id=10,
        step_position=10,
        step_title="Десятый",
    )
    _make_task(
        downloads,
        "c/s2/l/task1",
        **_COURSE,
        **_CHAPTER_2,
        step_id=21,
        step_position=1,
        step_title="Первый словарь",
    )
    (tmp_path / "stepik_config.json").write_text(
        json.dumps({"root_dir": "StepikTasks"}), encoding="utf-8"
    )

    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(getattr(exc, "stack", None) or str(exc)))

    page.goto(e2e_server, wait_until="networkidle")
    page.click("#nav-tab-browse")
    page.wait_for_function("() => !document.querySelector('#nav-next').disabled", timeout=15000)
    try:
        yield page
    finally:
        context.close()
        assert not errors, "непойманные ошибки JS на странице:\n" + "\n".join(errors)


def _advance(page: Any, times: int) -> None:
    for _ in range(times):
        page.click("#nav-next")
        page.wait_for_timeout(500)


class TestOrderComesFromHierarchy:
    """Алфавит каталогов врёт — порядок берётся из `meta.json`."""

    def test_step_nine_comes_before_step_ten(self, nav_page: Any) -> None:
        """При сортировке по имени папки `task10` встал бы перед `task9`."""
        _advance(nav_page, 2)
        assert nav_page.input_value("#path").endswith("task9")

        _advance(nav_page, 1)
        assert nav_page.input_value("#path").endswith("task10")

    def test_paging_crosses_the_chapter_boundary(self, nav_page: Any) -> None:
        """Шаги — сквозной поток; смена главы видна в подписи, а не теряется."""
        _advance(nav_page, 4)

        assert "Словари" in nav_page.inner_text("#nav-context")
        assert "Урок 5" in nav_page.inner_text("#nav-context")


class TestEdgesAreDisabled:
    def test_next_goes_dead_at_the_end(self, nav_page: Any) -> None:
        _advance(nav_page, 4)
        assert nav_page.is_disabled("#nav-next")

    def test_prev_goes_dead_at_the_start(self, nav_page: Any) -> None:
        _advance(nav_page, 1)
        assert nav_page.is_disabled("#nav-prev")

    def test_disabled_arrow_explains_itself(self, nav_page: Any) -> None:
        """Серая кнопка без объяснения читается как «сломалось»."""
        _advance(nav_page, 1)
        assert nav_page.get_attribute("#nav-prev", "title")


class TestColdStart:
    """Панель обязана работать в тот момент, когда она нужна.

    С холодного старта поле пути указывает на рабочую папку, а не на задачу.
    Первая редакция гасила в этом состоянии обе стрелки — и войти в дерево
    было нечем: панель для выбора задачи не позволяла выбрать задачу.
    """

    def test_arrow_enters_the_tree(self, nav_page: Any) -> None:
        assert not nav_page.is_disabled("#nav-next")
        _advance(nav_page, 1)
        assert nav_page.input_value("#path").endswith("task2")

    def test_outside_the_course_is_said_in_words(self, nav_page: Any) -> None:
        assert "вне скачанного" in nav_page.inner_text("#nav-context")


class TestLevels:
    def test_chapter_level_changes_the_counter(self, nav_page: Any) -> None:
        _advance(nav_page, 1)
        nav_page.select_option("#nav-level", "section")
        nav_page.wait_for_timeout(300)

        assert "глава" in nav_page.inner_text("#nav-counter")

    def test_chapter_arrow_jumps_to_the_next_chapter(self, nav_page: Any) -> None:
        """«Вперёд» на уровне главы — первый шаг следующей, а не соседний шаг."""
        _advance(nav_page, 1)
        nav_page.select_option("#nav-level", "section")
        nav_page.wait_for_timeout(300)
        _advance(nav_page, 1)

        assert "Словари" in nav_page.inner_text("#nav-context")


class TestList:
    def test_list_shows_step_statuses(self, nav_page: Any) -> None:
        """Статус назван словом: цвет не читается при дальтонизме."""
        nav_page.click("#nav-list-toggle")
        nav_page.wait_for_timeout(300)

        assert nav_page.is_visible("#nav-list")
        assert "не начата" in nav_page.inner_text("#nav-list")

    def test_list_shows_group_progress_above_step_level(self, nav_page: Any) -> None:
        nav_page.select_option("#nav-level", "section")
        nav_page.click("#nav-list-toggle")
        nav_page.wait_for_timeout(300)

        assert "решено" in nav_page.inner_text("#nav-list")

    def test_clicking_an_item_selects_the_task(self, nav_page: Any) -> None:
        nav_page.click("#nav-list-toggle")
        nav_page.wait_for_timeout(300)
        nav_page.locator("#nav-list li").first.click()
        nav_page.wait_for_timeout(500)

        assert nav_page.input_value("#path").endswith("task2")

    def test_toggle_reports_expanded_state(self, nav_page: Any) -> None:
        """`aria-expanded` — единственное, по чему скринридер узнаёт о раскрытии."""
        assert nav_page.get_attribute("#nav-list-toggle", "aria-expanded") == "false"
        nav_page.click("#nav-list-toggle")
        nav_page.wait_for_timeout(200)
        assert nav_page.get_attribute("#nav-list-toggle", "aria-expanded") == "true"


class TestPathFieldStaysUsable:
    """Поле пути — не заменяется панелью: работа с папкой вне курса рабочая."""

    def test_switching_to_path_hides_the_panel(self, nav_page: Any) -> None:
        nav_page.click("#nav-tab-path")
        nav_page.wait_for_timeout(200)

        assert nav_page.is_hidden("#nav-panel")
        assert nav_page.is_visible("#path")

    def test_navigation_fills_the_path_field(self, nav_page: Any) -> None:
        """Источник истины остаётся путём; панель — лишь способ его выбрать."""
        _advance(nav_page, 1)
        assert nav_page.input_value("#path").endswith("task2")
