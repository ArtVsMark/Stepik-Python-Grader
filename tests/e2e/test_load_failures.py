"""test_load_failures.py — сбой загрузки виден как сбой, а не как «пусто» (#806).

Два дефекта одной природы: раздел рисуется так, будто данные пришли и они
пустые, хотя на деле запрос не удался.

* ``DESW-02`` — загрузчики разделов глушили любую ошибку в пустой список, и
  «Подучить» на упавшем сервере рапортовал «Пока пусто — и это отлично 🎉»,
  «Прогресс» — «Пока пусто 📊», глоссарий — «Ничего не найдено». Ученик читает
  это как «у тебя всё хорошо», хотя данных просто не дошло.
* ``DEV-01`` — в ``renderTermCards`` параметр стрелочной функции назывался
  ``t`` и затенял функцию перевода: на первом же концепте без карточки вызов
  ``t("terms.no_card")`` падал TypeError, и панель «Функции в коде» не
  рисовалась целиком.

Оба воспроизводятся только в браузере: первый — перехватом ответа сервера,
второй — реальным рендером панели. Не входит в обычный ``pytest tests/`` —
см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.sync_api import expect

_TIMEOUT_MS = 10_000
# Текст кнопки повтора в обеих локалях: язык интерфейса берётся из браузера, а
# он на CI-раннере может быть любым.
_RETRY_RE = re.compile(r"повторить|retry", re.IGNORECASE)


def _fail_route(page: Any, pattern: str) -> None:
    """Отвечать 500 на все запросы по ``pattern`` — сервер «упал»."""
    page.route(
        pattern,
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"kind": "error", "message": "boom"}',
        ),
    )


def test_insights_failure_shows_error_not_congratulations(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """«Подучить» на сбое показывает ошибку и «Повторить», а не «Пока пусто»."""
    _fail_route(page, "**/api/insights")

    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="insights"]')
    page.wait_for_selector("#view-insights:not([hidden])", timeout=_TIMEOUT_MS)

    expect(page.locator("#view-insights [data-load-error]")).to_be_visible(timeout=_TIMEOUT_MS)
    expect(page.locator("#view-insights [data-load-error] button")).to_have_text(
        _RETRY_RE, timeout=_TIMEOUT_MS
    )
    # Поздравительное пустое состояние при этом скрыто — иначе пользователь
    # видел бы оба сообщения разом.
    expect(page.locator("#insights-empty")).to_be_hidden(timeout=_TIMEOUT_MS)


def test_insights_retry_recovers_after_server_returns(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """«Повторить» реально перезагружает раздел, когда сервер ожил.

    Страхует от «фикса», который рисует баннер поверх разметки пустого
    состояния: восстановить её после успешного повтора было бы нечем.

    Считаем **запросы** к ``/api/insights``, а не только состояние экрана (issue
    #921, находка `QA-2-04`). Без счётчика тест проходил и у обработчика,
    который просто прячет баннер и показывает пустое состояние, ничего не
    запрашивая: экран после такого «фикса» выглядит правильно, а данные —
    прежние. Кнопка называется «Повторить», и повтор обязан быть настоящим.
    """
    failing = {"on": True}
    requests: list[str] = []

    def _maybe_fail(route: Any) -> None:
        requests.append(route.request.url)
        if failing["on"]:
            route.fulfill(status=500, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route("**/api/insights", _maybe_fail)

    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="insights"]')
    expect(page.locator("#view-insights [data-load-error]")).to_be_visible(timeout=_TIMEOUT_MS)
    before = len(requests)
    assert before, "раздел не сходил за данными вовсе — проверять нечего"

    failing["on"] = False  # сервер вернулся
    page.click("#view-insights [data-load-error] button")

    # Баннер ушёл, штатное состояние раздела вернулось (пустое или со списком).
    expect(page.locator("#view-insights [data-load-error]")).to_have_count(0, timeout=_TIMEOUT_MS)
    page.wait_for_selector(
        "#insights-empty:not([hidden]), #insights-cards:not([hidden])", timeout=_TIMEOUT_MS
    )
    assert len(requests) > before, (
        "после «Повторить» нового запроса к /api/insights не было — экран "
        "перерисовали по старым данным"
    )


def test_progress_failure_shows_error_not_empty_state(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """«Прогресс» на сбое показывает ошибку, а не «Пока пусто 📊»."""
    _fail_route(page, "**/api/progress")

    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="progress"]')
    page.wait_for_selector("#view-progress:not([hidden])", timeout=_TIMEOUT_MS)

    expect(page.locator("#view-progress [data-load-error]")).to_be_visible(timeout=_TIMEOUT_MS)
    expect(page.locator("#progress-empty")).to_be_hidden(timeout=_TIMEOUT_MS)
    expect(page.locator("#progress-content")).to_be_hidden(timeout=_TIMEOUT_MS)


def test_glossary_failure_shows_error_not_nothing_found(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Глоссарий на сбое показывает ошибку, а не «Ничего не найдено»."""
    _fail_route(page, "**/api/glossary?*")

    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="glossary"]')
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)

    expect(page.locator("#view-glossary [data-load-error]")).to_be_visible(timeout=_TIMEOUT_MS)


def test_terms_panel_renders_concept_without_card(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Панель «Функции в коде» переживает концепт, которого нет в глоссарии.

    ``cmath.polar`` карточки не имеет, а ``cmath`` — имеет; покрытые идут
    первыми, поэтому падение на непокрытом обрывало уже начатый рендер, и
    панель оставалась пустой целиком.
    """
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    page.click('.mode-btn[data-mode="file"]')
    page.wait_for_selector("#file-picker-group:not([hidden])", timeout=_TIMEOUT_MS)
    page.click("#solution-editor .cm-content")
    page.keyboard.type("import cmath\ncmath.polar(1)")

    # Приглушённая карточка без ссылки — та самая, на которой всё падало.
    expect(page.locator("#check-terms .term-card-nocard")).to_have_count(1, timeout=_TIMEOUT_MS)
    titles = [x.lower() for x in page.locator("#check-terms .term-card-title").all_inner_texts()]
    assert "cmath" in titles, titles
    assert "cmath.polar" in titles, titles
