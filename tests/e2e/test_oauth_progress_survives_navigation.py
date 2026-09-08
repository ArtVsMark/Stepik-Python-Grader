"""Индикатор идущего OAuth переживает уход из раздела (issue #922, ``FE-3-03``).

Панель доступа пересобирается целиком при каждом заходе в «Загрузчик»
(``registerSectionHook`` зовёт ``loadAuthStatus``), а ``renderAuthPanel``
рисует индикатор скрытым и кнопку активной. Значит уход в другой раздел и
возврат стирал **единственный** признак идущей авторизации. Сам опрос при этом
продолжался (#802), и человек видел панель «ничего не происходит» поверх
работающего процесса: следующее нажатие «Авторизоваться» заводило второй прогон
и вытесняло первый.

Сценарий подставляется перехватом маршрутов Playwright: живой OAuth (браузер,
Stepik, ожидание кода) в тесте невозможен, а важен ровно тот ответ, который
видит фронт.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000


def _route_running_auth(page: Any) -> dict[str, int]:
    """Авторизация, которая идёт и не заканчивается; вернуть счётчик опросов."""
    calls = {"start": 0, "poll": 0}

    page.route(
        "**/api/auth/status",
        lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            # `no_token` — ветка «креды есть, токен протух»: одна кнопка
            # «Авторизоваться» и `#auth-progress` рядом, то самое место.
            body=json.dumps({"authorized": False, "reason": "no_token"}),
        ),
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
            status=200, content_type="application/json", body=json.dumps({"status": "running"})
        )

    page.route("**/api/auth/start", on_start)
    page.route("**/api/v1/runs/*", on_poll)
    return calls


def _open_downloader(page: Any, e2e_server: str) -> None:
    page.click('.sidebar-item[data-section="downloader"]')
    page.wait_for_selector("#view-downloader:not([hidden])", timeout=_TIMEOUT_MS)


def _leave_and_come_back(page: Any) -> None:
    """Уйти в «Проверку» и вернуться — тот самый путь пользователя."""
    page.click('.sidebar-item[data-section="check"]')
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    page.click('.sidebar-item[data-section="downloader"]')
    page.wait_for_selector("#view-downloader:not([hidden])", timeout=_TIMEOUT_MS)


def test_the_indicator_is_still_there_after_coming_back(page: Any, e2e_server: str) -> None:
    """Вернулись в раздел — по-прежнему видно, что авторизация идёт."""
    _route_running_auth(page)
    page.goto(e2e_server + "/")
    _open_downloader(page, e2e_server)

    page.click("#auth-start")
    expect(page.locator("#auth-progress")).to_be_visible(timeout=_TIMEOUT_MS)
    running_text = page.locator("#auth-progress").inner_text()

    _leave_and_come_back(page)

    expect(page.locator("#auth-progress")).to_be_visible(timeout=_TIMEOUT_MS)
    assert page.locator("#auth-progress").inner_text() == running_text


def test_the_button_stays_locked_while_the_poll_runs(page: Any, e2e_server: str) -> None:
    """И кнопка остаётся заблокированной: второй прогон вытеснил бы первый."""
    calls = _route_running_auth(page)
    page.goto(e2e_server + "/")
    _open_downloader(page, e2e_server)

    page.click("#auth-start")
    expect(page.locator("#auth-start")).to_be_disabled(timeout=_TIMEOUT_MS)

    _leave_and_come_back(page)

    expect(page.locator("#auth-start")).to_be_disabled(timeout=_TIMEOUT_MS)
    assert calls["start"] == 1, f"авторизация запускалась повторно: {calls}"


def test_a_finished_auth_does_not_leave_the_panel_locked(page: Any, e2e_server: str) -> None:
    """Граница с другой стороны: авторизация кончилась — панель снова живая.

    Иначе «починка» превратилась бы в новый тупик: признак занятости, который
    некому снять, запирает кнопку навсегда — ровно тот дефект, от которого
    подэпик и защищает.
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
        body = (
            {"status": "running"}
            if calls["poll"] == 1
            else {"status": "error", "message": "провал авторизации"}
        )
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/v1/runs/*", on_poll)

    page.goto(e2e_server + "/")
    _open_downloader(page, e2e_server)
    page.click("#auth-start")

    expect(page.locator("#auth-progress")).to_contain_text(
        "провал авторизации", timeout=_TIMEOUT_MS
    )
    expect(page.locator("#auth-start")).to_be_enabled(timeout=_TIMEOUT_MS)

    # И после возврата в раздел кнопка тоже живая — «занятости» больше нет.
    _leave_and_come_back(page)
    expect(page.locator("#auth-start")).to_be_enabled(timeout=_TIMEOUT_MS)
