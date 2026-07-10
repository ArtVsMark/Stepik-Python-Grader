"""test_journeys.py -- Playwright smoke tests for the 4 core web UI journeys
(issue #263, see docs/web-mvp.md J0-J7): mode 2 (folder grading), mode 1
(single-file picker + editable window), glossary search, command palette.

Not part of the default ``pytest``/``pytest tests/`` sweep -- see
``tests/e2e/conftest.py`` and ``norecursedirs`` in ``pyproject.toml``. Run
explicitly: ``pytest tests/e2e/`` (after ``pip install -e ".[e2e]"`` +
``playwright install chromium``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 10_000


def test_mode2_folder_grading_shows_table_and_detail_tab(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J: режим 2 -- грейдинг папки, таблица результатов, вкладка «Детали»."""
    write_task(tmp_path, "print(int(input()) + 1)\n")  # 4 -> 5, matches expected

    page.goto(e2e_server + "/")
    # Mode 2 ("Папка") is the default, but click it explicitly for robustness
    # against a stale localStorage default from a previous test run.
    page.click('.mode-btn[data-mode="tests"]')
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    row_badge = page.locator("#out table.data-table tbody tr").first.locator(".badge")
    row_badge.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert row_badge.text_content().strip() == "OK"

    # Expand the row, then open the case's detail tab.
    page.click('td.file-cell[data-toggle="0"]')
    page.click('tr.case-row[data-row="0"][data-case="0"]')

    page.wait_for_selector("#restab-detail:not([hidden])", timeout=_TIMEOUT_MS)
    detail = page.locator("#detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    detail_text = detail.text_content()
    assert "AC" in detail_text
    assert "5" in detail_text  # the printed/actual output


def test_mode1_file_picker_edit_save_run(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: режим 1 -- выбрать файл, отредактировать код в окне, сохранить, запустить."""
    # Deliberately wrong solution on disk -- proves the *edited* code (not the
    # original file) is what actually gets graded.
    write_task(tmp_path, "print(int(input()) + 99)\n")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="file"]')
    page.fill("#path", str(tmp_path))
    page.click("#find-solutions-btn")

    item = page.locator("#solutions-list li[data-file]", has_text="task.py")
    item.wait_for(state="visible", timeout=_TIMEOUT_MS)
    item.click()

    # issue #265: #solution-editor is a CodeMirror 6 mount (contenteditable
    # .cm-content inside it), not a <textarea> -- no .value/.fill() on the
    # container itself. Content is read via .cm-content's text, and typed
    # via real keyboard events (select-all + type) rather than .fill(),
    # since CodeMirror reconciles its state off real DOM/input events, not
    # a bare textContent assignment. Waiting on "textContent.length > 0" is
    # WRONG here (found live, not just theorized): CodeMirror's placeholder
    # renders as a real .cm-placeholder span *inside* .cm-content when the
    # doc is empty, so length > 0 is already true before the real code
    # loads -- wait for the actual expected substring instead.
    editor_content = page.locator("#solution-editor .cm-content")
    page.wait_for_function(
        "document.querySelector('#solution-editor .cm-content').textContent.includes('99')",
        timeout=_TIMEOUT_MS,
    )
    assert "99" in editor_content.text_content()  # the original (wrong) code loaded

    editor_content.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("print(int(input()) + 1)\n")  # correct code, in the editable window
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    row_badge = page.locator("#out table.data-table tbody tr").first.locator(".badge")
    row_badge.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert row_badge.text_content().strip() == "OK"

    # The edit was actually persisted to disk (save-solution before grading).
    assert (tmp_path / "task.py").read_text(encoding="utf-8") == "print(int(input()) + 1)\n"


def test_glossary_search_and_open_card(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: глоссарий -- поиск по запросу, открытие карточки."""
    page.goto(e2e_server + "/")
    page.click('[data-section="glossary"]')
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)

    page.fill("#glossary-search", "Key")
    card = page.locator("#glossary-cards li[data-id]", has_text="KeyError")
    card.wait_for(state="visible", timeout=_TIMEOUT_MS)
    card.click()

    detail = page.locator("#glossary-detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert "KeyError" in detail.locator("h2").text_content()
    assert page.locator("#glossary-empty").is_hidden()


def test_command_palette_opens_and_executes(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: command palette -- Ctrl+K/триггер открывает палитру, команда исполняется."""
    page.goto(e2e_server + "/")

    # Entry point 1: the palette trigger button.
    page.click("#palette-btn")
    page.wait_for_selector("#palette-overlay:not([hidden])", timeout=_TIMEOUT_MS)
    page.keyboard.press("Escape")
    # state="attached" (not the default "visible") -- a [hidden] element is
    # by definition never "visible", so waiting for visibility here would
    # never resolve; we only need the attribute to be present.
    page.wait_for_selector("#palette-overlay[hidden]", state="attached", timeout=_TIMEOUT_MS)

    # Entry point 2: Ctrl+K -- filter to "toggle theme" and execute it.
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette-overlay:not([hidden])", timeout=_TIMEOUT_MS)
    page.fill("#palette-input", "тема")
    page.locator("#palette-list li", has_text="Переключить тему").wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.keyboard.press("Enter")

    page.wait_for_selector("#palette-overlay[hidden]", state="attached", timeout=_TIMEOUT_MS)
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme == "light"  # system -> light (cycleTheme's first step)
    assert page.locator("#theme-toggle").text_content() == "☀️"
