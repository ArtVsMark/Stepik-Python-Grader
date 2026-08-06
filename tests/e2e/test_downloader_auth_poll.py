"""test_downloader_auth_poll.py — опрос OAuth отменяется (issue #802, находка FED-01).

`pollAuth()` захватывал `#auth-progress` и `#auth-start` один раз при старте и
крутил `setTimeout` без всякого признака отмены. `renderAuthPanel()` собирает
панель заново, поэтому после любой перерисовки итог OAuth уходил в узлы вне
документа, а повторный клик по «Авторизоваться» заводил второй цикл поверх
первого.

Сценарии подставляются перехватом маршрутов Playwright: гонять живой OAuth
(браузер, Stepik, ожидание кода) в тесте нельзя, а важен ровно ответ, который
видит фронт.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000


def _route_auth(page: Any, *, status_reason: str = "no_token") -> dict[str, int]:
    """Подставить статус авторизации и старт OAuth; вернуть счётчик опросов.

    ``no_token`` — ветка «креды есть, токен протух»: панель рисует одну кнопку
    «Авторизоваться» и `#auth-progress`, то есть ровно то место, где жил дефект.
    """
    calls = {"start": 0, "poll": 0}

    def on_status(route: Any) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"authorized": False, "reason": status_reason}),
        )

    def on_start(route: Any) -> None:
        calls["start"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"run_id": f"run-{calls['start']}"}),
        )

    def on_poll(route: Any) -> None:
        calls["poll"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "running"}),
        )

    page.route("**/api/auth/status", on_status)
    page.route("**/api/auth/start", on_start)
    page.route("**/api/v1/runs/*", on_poll)
    return calls


def _open_downloader(page: Any, e2e_server: str) -> None:
    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="downloader"]')
    page.wait_for_selector("#view-downloader:not([hidden])", timeout=_TIMEOUT_MS)


def test_auth_result_reaches_a_repainted_panel(page: Any, e2e_server: str) -> None:
    """Итог OAuth доходит до панели, пересобранной во время опроса.

    Раньше `#auth-progress` был захвачен один раз: после перерисовки сообщение
    уходило в узел вне документа, и пользователь не видел ни успеха, ни ошибки.
    """
    calls = {"poll": 0}

    page.route(
        "**/api/auth/status",
        lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"authorized": False, "reason": "no_token"}),
        ),
    )
    page.route(
        "**/api/auth/start",
        lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"run_id": "run-1"})
        ),
    )

    def on_poll(route: Any) -> None:
        calls["poll"] += 1
        # Первый опрос — «идёт»; за это время панель перерисовываем, и итог
        # второго опроса обязан попасть уже в новые узлы.
        body = (
            {"status": "running"}
            if calls["poll"] == 1
            else {"status": "error", "message": "провал авторизации"}
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/v1/runs/*", on_poll)

    _open_downloader(page, e2e_server)
    page.click("#auth-start")
    page.wait_for_function("() => window.__pollCalls === undefined", timeout=100)  # no-op sync

    # Перерисовать панель тем же путём, каким это делает приложение.
    page.evaluate(
        "async () => { const m = await import('/static/downloader.js'); await m.loadAuthStatus(); }"
    )
    expect(page.locator("#auth-progress")).to_contain_text(
        "провал авторизации", timeout=_TIMEOUT_MS
    )
    # Кнопка разблокирована — из тупика есть выход.
    expect(page.locator("#auth-start")).to_be_enabled(timeout=_TIMEOUT_MS)


def test_second_click_does_not_start_a_second_poll(page: Any, e2e_server: str) -> None:
    """Повторный «Авторизоваться» вытесняет прежний цикл, а не удваивает опрос."""
    calls = _route_auth(page)
    _open_downloader(page, e2e_server)

    page.click("#auth-start")
    page.wait_for_timeout(1000)
    after_first = calls["poll"]
    assert after_first >= 1, calls

    # Кнопка после старта заблокирована — снимаем блокировку так же, как это
    # делает ветка ошибки, и кликаем ещё раз.
    page.evaluate("() => { document.querySelector('#auth-start').disabled = false; }")
    page.click("#auth-start")
    page.wait_for_timeout(2000)

    # Два независимых цикла с интервалом 800 мс дали бы примерно вдвое больше
    # опросов за то же время; один вытесняет другой — темп остаётся прежним.
    total = calls["poll"]
    elapsed_ticks = total - after_first
    assert elapsed_ticks <= 4, f"опрос ускорился вдвое — циклов больше одного: {calls}"


def test_cancel_stops_the_poll(page: Any, e2e_server: str) -> None:
    """«Отмена» в мастере заканчивает опрос, а не оставляет его крутиться."""
    calls = _route_auth(page, status_reason="no_secrets")
    _open_downloader(page, e2e_server)

    # Развилка первого запуска → мастер → «Авторизоваться». Мастер без кредов
    # до старта не доходит (`auth.need_credentials`), поэтому поля заполняем.
    page.click("#auth-choice-wizard")
    page.wait_for_selector("#auth-start", timeout=_TIMEOUT_MS)
    page.fill("#auth-client-id", "id")
    page.fill("#auth-client-secret", "secret")
    page.click("#auth-start")
    page.wait_for_timeout(1000)
    assert calls["poll"] >= 1, calls

    page.click("#auth-back")
    page.wait_for_timeout(200)
    after_cancel = calls["poll"]
    page.wait_for_timeout(2500)
    assert calls["poll"] == after_cancel, f"опрос пережил отмену: {calls}"
