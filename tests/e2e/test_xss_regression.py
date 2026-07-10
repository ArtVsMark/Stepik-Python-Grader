"""test_xss_regression.py -- XSS regression test for app.js's ``esc()``
escaping (issue #263, hardening from issue #214).

``app.js`` has ~41 ``innerHTML`` call sites rendering solution-controlled
text (stdout/stderr) -- only guaranteed safe by code review today. This test
runs a real solution whose output is an XSS payload, opens the detail tab
that renders it, and asserts the payload never executes and never becomes a
live DOM element.

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
_PAYLOAD = "<img src=x onerror=window.__xss=1>"


def test_xss_payload_in_stdout_is_escaped_not_executed(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    # The solution's stdout *is* the payload -- expected matches it exactly,
    # so grading is AC and the detail tab's "Вывод" (actual output) field
    # renders it via codeBlock()/esc() (viewmodels._case_view always sets
    # "actual", regardless of verdict -- see web/viewmodels.py).
    write_task(
        tmp_path,
        f"print({_PAYLOAD!r})\n",
        stdin="",
        expected=_PAYLOAD,
    )

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="tests"]')
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    page.click('td.file-cell[data-toggle="0"]')
    page.click('tr.case-row[data-row="0"][data-case="0"]')
    page.wait_for_selector("#restab-detail:not([hidden])", timeout=_TIMEOUT_MS)

    detail = page.locator("#detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    # Wait for the code block holding the rendered payload specifically,
    # rather than racing the detail panel's own render.
    page.wait_for_function(
        "document.querySelector('#detail-content').textContent.includes('onerror')",
        timeout=_TIMEOUT_MS,
    )

    # 1) The onerror handler never fired -- no live <img> was created.
    xss_flag = page.evaluate("window.__xss")
    assert xss_flag is None

    # 2) No <img> element exists anywhere in the detail panel's rendered DOM.
    assert detail.locator("img").count() == 0

    # 3) The raw markup was escaped in the HTML source (literal entities),
    # not inserted as live tags.
    inner_html = detail.inner_html()
    assert "&lt;img" in inner_html
    assert "<img src=x" not in inner_html

    # 4) The visible text content still shows the payload as plain text --
    # the escaping didn't just hide it, it displayed it safely.
    assert _PAYLOAD in detail.text_content()
