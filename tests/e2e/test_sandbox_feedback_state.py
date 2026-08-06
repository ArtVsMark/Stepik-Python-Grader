"""test_sandbox_feedback_state.py — состояние кнопок и ссылок (issue #803).

Находки `FES-01`/`FES-02`: отмена прогона песочницы гасила кнопку до ответа
сервера и глотала отказ; кнопка «Открыть форму на GitHub» держала
prefilled-URL прежнего черновика.

`FES-03` (гонка ответов черновика) проверяется не здесь: доставить поздний
ответ на ранний запрос из Playwright детерминированно не выходит —
придержанный `route.fulfill()` до страницы уже не доезжает, и тест остаётся
зелёным без фикса. Его сторожит статическая проверка в
`tests/test_web_feedback_draft_guard.py`.

Ответы сервера подставляются перехватом маршрутов Playwright — важен ровно тот
ответ, который видит фронт, а не как он получен.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000


def _open_sandbox(page: Any, e2e_server: str) -> None:
    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)


def test_failed_cancel_returns_the_button(page: Any, e2e_server: str) -> None:
    """FES-01: отказ на отмену возвращает кнопку и говорит об этом.

    До фикса кнопка гасла до ответа, а 500 уходил в пустой `catch` — «Отмена»
    оставалась неактивной навсегда, повторить было нечем.
    """
    page.route(
        "**/api/v1/runs",
        lambda r: r.fulfill(
            status=202, content_type="application/json", body=json.dumps({"run_id": "run-1"})
        ),
    )
    page.route(
        "**/api/v1/runs/run-1/cancel",
        lambda r: r.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"message": "отмена не прошла"}),
        ),
    )
    page.route(
        "**/api/v1/runs/run-1",
        lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"status": "running"})
        ),
    )

    _open_sandbox(page, e2e_server)
    page.click("#sandbox-editor .cm-content")
    page.keyboard.type("print(1)")
    page.click("#sandbox-run")

    cancel = page.locator("#sandbox-cancel")
    expect(cancel).to_be_visible(timeout=_TIMEOUT_MS)
    cancel.click()

    expect(page.locator(".toast-error")).to_contain_text("отмена не прошла", timeout=_TIMEOUT_MS)
    # Прогон продолжается — отменить можно ещё раз.
    expect(cancel).to_be_enabled(timeout=_TIMEOUT_MS)


def _draft_body(marker: str, n: int) -> str:
    """Ответ POST /api/feedback в том же виде, что отдаёт `core/feedback.py`."""
    return json.dumps(
        {
            "url": f"https://example.invalid/issues/new?n={n}",
            "discussions_url": "https://example.invalid/discussions",
            "fields": [{"id": "what-happened", "value": marker}],
            "truncated": [],
            "dropped": [],
        }
    )


def _route_draft(page: Any) -> dict[str, int]:
    """Отдавать корректный черновик; считать запросы."""
    calls = {"n": 0}

    def on_draft(route: Any) -> None:
        calls["n"] += 1
        route.fulfill(
            status=200, content_type="application/json", body=_draft_body("черновик", calls["n"])
        )

    page.route("**/api/feedback*", on_draft)
    return calls


def _open_feedback(page: Any, e2e_server: str) -> None:
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    page.click("#feedback-open")
    page.wait_for_selector("#feedback-overlay:not([hidden])", timeout=_TIMEOUT_MS)


def test_stale_link_is_dropped_while_the_draft_is_rebuilt(page: Any, e2e_server: str) -> None:
    """FES-02: сменили тип — прежняя ссылка гаснет, а не ведёт в старую форму."""
    _route_draft(page)
    _open_feedback(page, e2e_server)

    link = page.locator("#feedback-open-github")
    expect(link).not_to_have_attribute("aria-disabled", "true", timeout=_TIMEOUT_MS)
    first_href = link.get_attribute("href")
    assert first_href

    # Черновик нового типа задерживаем — в этот промежуток ссылка обязана быть
    # неактивной, иначе она уводит в форму прежнего вида со старым текстом.
    page.unroute("**/api/feedback*")
    page.route("**/api/feedback*", lambda r: None)  # запрос повисает
    page.click('[data-fbkind="idea"]')
    expect(link).to_have_attribute("aria-disabled", "true", timeout=_TIMEOUT_MS)
    assert link.get_attribute("href") is None
