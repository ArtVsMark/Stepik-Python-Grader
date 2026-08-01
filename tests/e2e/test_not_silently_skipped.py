"""test_not_silently_skipped.py — e2e-набор не имеет права молча скипнуться (#790).

Все e2e-тесты идут через фикстуру ``page`` → ``browser`` → ``playwright_instance``,
а та начинается с ``pytest.importorskip("playwright.sync_api")``. Значит любая
поломка окружения — не разрешившийся extra, несовместимая версия, сбой кеша
браузеров — превращает весь набор в «N skipped» и код возврата 0. Единственная
проверка веб-UI на уровне DOM перестала бы выполняться, а PR'ы продолжали бы
мержиться с зелёным job'ом: ровно тот класс немого skip, который проект уже
признал проблемой в #420/#558 для песочницы.

Guard включается переменной ``STEPIK_REQUIRE_E2E_TESTS=1`` — её ставит job
``e2e`` в CI. Локально (переменной нет) тесты скипаются, как и весь набор:
разработчик без ``pip install -e ".[e2e]"`` не должен получать красный прогон.

Само-гейт по переменной, а не по платформе: в отличие от песочницы, e2e не
привязан к ОС.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

_REQUIRED = "STEPIK_REQUIRE_E2E_TESTS"


def _guard_enabled() -> bool:
    return bool(os.environ.get(_REQUIRED))


def test_playwright_importable_when_required() -> None:
    """Пакет ``playwright`` установлен — иначе ``importorskip`` съел бы весь набор."""
    if not _guard_enabled():
        pytest.skip(f"guard enforced only with {_REQUIRED}")
    import playwright.sync_api  # noqa: F401 — важен сам факт импорта

    assert True


def test_browser_actually_launches_when_required(browser: Any) -> None:
    """Chromium реально стартует и открывает страницу.

    Импорта пакета мало: браузер ставится отдельным шагом (`playwright install`),
    и именно он отваливался при ложном попадании кеша — тогда набор падал, но с
    невнятным «Executable doesn't exist» у каждого теста. Здесь проверяется
    цепочка целиком, до готового DOM.
    """
    if not _guard_enabled():
        pytest.skip(f"guard enforced only with {_REQUIRED}")
    page = browser.new_page()
    try:
        page.set_content("<h1 id='probe'>ok</h1>")
        assert page.locator("#probe").inner_text() == "ok"
    finally:
        page.close()


def test_suite_is_not_entirely_skipped(request: pytest.FixtureRequest) -> None:
    """Набор реально ВЫПОЛНЯЕТСЯ, а не только собирается.

    Два предыдущих теста ловят «playwright сломан». Этот ловит случай пострашнее:
    окружение в порядке, но набор не запустился — например, из-за опечатки в
    пути ``pytest tests/e2e/`` после переименования каталога, фильтра ``-k``,
    оставшегося в команде, или маркера, отсекающего всё разом. Тогда job'у
    нечего было бы проверять, и он всё равно был бы зелёным.

    Считаем собранные элементы: у самого набора их заведомо больше, чем три
    guard-теста этого файла.
    """
    if not _guard_enabled():
        pytest.skip(f"guard enforced only with {_REQUIRED}")
    collected = len(request.session.items)
    guards_in_this_file = 3
    assert collected > guards_in_this_file, (
        f"собрано {collected} тестов — это только guard'ы: сам e2e-набор не "
        f"попал в прогон, и job проверил бы пустоту"
    )
