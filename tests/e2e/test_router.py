"""test_router.py — URL адресует раздел (issue #804, находка FER-03).

`setSection` менял только `state`/`localStorage`, а `location.hash` оставался
от предыдущей навигации. Отсюда три следствия: ссылкой на раздел нельзя было
поделиться, F5 возвращал не туда (репро аудита: открыть карточку глоссария →
уйти сайдбаром в «Проверку» → F5 → снова глоссарий), а «Назад» при пустом
хэше молчал.

Формат адреса — ровно тот, что уже стоит в `href` пунктов сайдбара (`#check`);
второй формат (`#/check`) разошёлся бы с разметкой. Ранее задокументированный
`#/insights` продолжает работать. Карточки (`#/glossary/<id>`, `#/rules/<code>`)
адресуют точнее раздела и переписываться разделом не должны — это отдельная
проверка ниже, потому что именно её ломает наивная реализация.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000


def _hash(page: Any) -> str:
    """Хэш текущего URL вместе с `#` (пустая строка, если хэша нет)."""
    parsed = urlparse(page.url)
    return "#" + parsed.fragment if parsed.fragment else ""


def test_open_page_addresses_the_restored_section(page: Any, e2e_server: str) -> None:
    """Старт без хэша: раздел из localStorage дописывается в URL."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    assert _hash(page) == "#check"


def test_sidebar_click_writes_section_to_url(page: Any, e2e_server: str) -> None:
    """Переключение раздела сайдбаром отражается в адресной строке."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    page.click('.sidebar-item[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)
    assert _hash(page) == "#sandbox"


def test_section_link_opens_that_section(page: Any, e2e_server: str) -> None:
    """Прямая ссылка `#<раздел>` открывает его, а не последний из localStorage."""
    page.goto(e2e_server + "/#sandbox")
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)
    assert page.locator("#view-check").is_hidden()


def test_legacy_slash_insights_link_still_works(page: Any, e2e_server: str) -> None:
    """`#/insights` — задокументированная ссылка, ломать её нельзя."""
    page.goto(e2e_server + "/#/insights")
    page.wait_for_selector("#view-insights:not([hidden])", timeout=_TIMEOUT_MS)
    # Хэш не нормализуем: ссылка уже адресует раздел верно.
    assert _hash(page) == "#/insights"


def test_reload_keeps_the_current_section(page: Any, e2e_server: str) -> None:
    """F5 остаётся в разделе — репро аудита целиком.

    Карточка глоссария → сайдбаром в «Проверку» → F5. До фикса хэш оставался
    `#/glossary/keyerror`, и роутер после перезагрузки уводил обратно в глоссарий.
    """
    page.goto(e2e_server + "/#/glossary/keyerror")
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)

    page.click('.sidebar-item[data-section="check"]')
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    assert _hash(page) == "#check"

    page.reload()
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    assert page.locator("#view-glossary").is_hidden()


def test_card_deeplink_survives_section_sync(page: Any, e2e_server: str) -> None:
    """Раздел не затирает более точный адрес карточки глоссария."""
    page.goto(e2e_server + "/#/glossary/keyerror")
    expect(page.locator("#glossary-detail-content")).to_be_visible(timeout=_TIMEOUT_MS)
    assert _hash(page) == "#/glossary/keyerror"


def test_rule_deeplink_survives_section_sync(page: Any, e2e_server: str) -> None:
    """То же для карточки правила: `#/rules/<code>` переживает открытие раздела."""
    page.goto(e2e_server + "/#/rules/E501")
    page.wait_for_selector("#view-rules:not([hidden])", timeout=_TIMEOUT_MS)
    expect(page.locator("#rules-detail-content")).to_contain_text("E501", timeout=_TIMEOUT_MS)
    assert _hash(page) == "#/rules/E501"


def test_skip_link_anchor_is_not_treated_as_a_section(page: Any, e2e_server: str) -> None:
    """`#main-content` — якорь skip-ссылки, а не раздел: роутер его не трогает."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    page.evaluate("() => { location.hash = '#main-content'; }")
    page.wait_for_function("() => location.hash === '#main-content'", timeout=_TIMEOUT_MS)
    # Раздел не поменялся и хэш не переписан обратно на `#check`.
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    assert _hash(page) == "#main-content"
