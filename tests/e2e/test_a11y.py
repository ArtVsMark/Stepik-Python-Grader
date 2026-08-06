"""test_a11y.py — доступность веб-оболочки (issue #805, находки DESW-03/05/06/07).

Контраст токенов проверяет `scripts/check_contrast.py` (и `tests/test_contrast.py`)
— здесь только то, что видно лишь в живом браузере: возвращается ли skip-link в
поток по Tab, ходят ли вкладки стрелками, переживает ли тост свой таймер и
исчезает ли лишняя остановка табуляции у смонтированного редактора.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000


def _toast(page: Any, message: str, kind: str) -> None:
    """Показать тост, вызвав `toast()` из модуля страницы.

    Через живой сценарий тост не выманить детерминированно (нужен конкретный
    сбой сети или сервера), а глобал ради теста в продакшн-код добавлять
    незачем: `core.js` — обычный ES-модуль, и CSP `default-src 'self'`
    динамический импорт своего же origin разрешает.
    """
    page.evaluate(
        "async ([msg, kind]) => { const m = await import('/static/core.js'); m.toast(msg, kind); }",
        [message, kind],
    )


def test_skip_link_becomes_visible_on_tab(page: Any, e2e_server: str) -> None:
    """DESW-03: первый Tab показывает «Перейти к содержимому», а не фокус на 1×1 px."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    link = page.locator('a[href="#main-content"]')
    box_before = link.bounding_box()
    assert box_before is not None
    assert box_before["width"] <= 2 and box_before["height"] <= 2, box_before

    link.focus()
    box_after = link.bounding_box()
    assert box_after is not None
    # Ссылка вернулась в поток: реальный размер вместо клипа 1×1.
    assert box_after["width"] > 40 and box_after["height"] > 10, box_after


def test_result_tabs_expose_their_panels(page: Any, e2e_server: str) -> None:
    """DESW-05: `role="tab"` доведён до конца — есть tabpanel и связь в обе стороны."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    for tab, panel in (("table", "restab-table"), ("detail", "restab-detail")):
        btn = page.locator(f'[data-restab="{tab}"]')
        assert btn.get_attribute("aria-controls") == panel
        target = page.locator("#" + panel)
        assert target.get_attribute("role") == "tabpanel"
        assert target.get_attribute("aria-labelledby") == f"restab-{tab}-btn"


def test_result_tabs_move_with_arrow_keys(page: Any, e2e_server: str) -> None:
    """DESW-05: стрелки переключают вкладку и уносят фокус за собой."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    page.locator('[data-restab="table"]').focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator('[data-restab="detail"]')).to_have_attribute(
        "aria-selected", "true", timeout=_TIMEOUT_MS
    )
    assert page.evaluate("() => document.activeElement.dataset.restab") == "detail"

    page.keyboard.press("ArrowLeft")
    expect(page.locator('[data-restab="table"]')).to_have_attribute(
        "aria-selected", "true", timeout=_TIMEOUT_MS
    )
    # Home/End — те же концы списка из двух вкладок, проверяем End как отдельную ветку.
    page.keyboard.press("End")
    assert page.evaluate("() => document.activeElement.dataset.restab") == "detail"


def test_result_tabs_use_roving_tabindex(page: Any, e2e_server: str) -> None:
    """DESW-05: Tab заходит в группу один раз — невыбранная вкладка вне обхода."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    assert page.locator('[data-restab="table"]').get_attribute("tabindex") == "0"
    assert page.locator('[data-restab="detail"]').get_attribute("tabindex") == "-1"

    page.click('[data-restab="detail"]')
    assert page.locator('[data-restab="detail"]').get_attribute("tabindex") == "0"
    assert page.locator('[data-restab="table"]').get_attribute("tabindex") == "-1"


def test_editor_mount_drops_its_placeholder_tabindex(page: Any, e2e_server: str) -> None:
    """DESW-07: смонтированный редактор не оставляет лишней остановки табуляции."""
    page.goto(e2e_server + "/")
    # `state="attached"`: редактор монтируется на старте, но в режиме «tests»
    # скрыт — ждём появления в DOM, а не видимости.
    page.wait_for_selector("#solution-editor .cm-content", state="attached", timeout=_TIMEOUT_MS)
    assert page.locator("#solution-editor").get_attribute("tabindex") is None


def test_error_toast_waits_for_the_user(page: Any, e2e_server: str) -> None:
    """DESW-06: тост об ошибке не уезжает по таймеру и закрывается кнопкой.

    Сообщение об ошибке часто единственный канал диагностики: его нужно успеть
    перечитать и скопировать в issue.
    """
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    _toast(page, "всё сломалось", "error")
    toast = page.locator(".toast-error")
    expect(toast).to_be_visible(timeout=_TIMEOUT_MS)

    # Прежний потолок автозакрытия — 6 с; ждём дольше и убеждаемся, что тост жив.
    page.wait_for_timeout(6500)
    expect(toast).to_be_visible()

    toast.locator(".toast-close").click()
    expect(toast).to_have_count(0, timeout=_TIMEOUT_MS)


def test_hovered_toast_does_not_disappear(page: Any, e2e_server: str) -> None:
    """DESW-06: пока на тост смотрят, таймер стоит — иначе текст не выделить."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    _toast(page, "сохранено", "success")
    toast = page.locator(".toast-success")
    expect(toast).to_be_visible(timeout=_TIMEOUT_MS)

    toast.hover()
    page.wait_for_timeout(3000)  # дольше штатных 2.5 с
    expect(toast).to_be_visible()

    # Курсор ушёл — таймер взводится заново и тост уходит сам.
    page.mouse.move(0, 0)
    expect(toast).to_have_count(0, timeout=_TIMEOUT_MS)
