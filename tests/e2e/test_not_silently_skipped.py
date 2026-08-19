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

Здесь живёт не весь guard: «набор не только собран, но и выполнен» проверяет
``pytest_sessionfinish`` в ``conftest.py`` — исходы всех тестов известны лишь в
конце сессии (issue #921, находка `QA-2-02`). Там же импорт ``playwright``
становится жёстким, когда переменная выставлена: иначе сломанное окружение
пропускало и сами эти guard'ы (`QA-2-03`).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from ._helpers import REQUIRE_E2E_ENV as _REQUIRED


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

    Фикстура ``browser`` разворачивается ДО тела теста, поэтому проверка флага
    внутри тела ничего бы не спасла: под жёстким режимом ``conftest.py`` сам
    импортирует ``playwright`` без ``importorskip`` — сломанное окружение даёт
    ошибку, а не пропуск этого самого guard'а.
    """
    if not _guard_enabled():
        pytest.skip(f"guard enforced only with {_REQUIRED}")
    page = browser.new_page()
    try:
        page.set_content("<h1 id='probe'>ok</h1>")
        assert page.locator("#probe").inner_text() == "ok"
    finally:
        page.close()


def test_suite_is_not_reduced_to_the_guards(request: pytest.FixtureRequest) -> None:
    """В прогон СОБРАН не только этот файл — ловит `-k`, маркер, опечатку в пути.

    Прежде тест назывался «набор реально выполняется» и обещал в докстринге
    именно это, а доказывал другое: ``request.session.items`` — собранные
    элементы, и собираются они одинаково, выполнится тест или пропустится
    следом. Фикс, из-за которого набор целиком уходит в skip, оставался
    зелёным — issue #921, находка `QA-2-02`.

    Что собрано, здесь по-прежнему проверяется: это дешёвая и ранняя проверка
    против фильтра, забытого в команде. А что **выполнено** — проверяет
    ``pytest_sessionfinish`` в ``conftest.py``: только в конце сессии известны
    исходы всех тестов, тест из середины о соседях такого знать не может.
    """
    if not _guard_enabled():
        pytest.skip(f"guard enforced only with {_REQUIRED}")
    collected = len(request.session.items)
    guards_in_this_file = 3
    assert collected > guards_in_this_file, (
        f"собрано {collected} тестов — это только guard'ы: сам e2e-набор не "
        f"попал в прогон, и job проверил бы пустоту"
    )
