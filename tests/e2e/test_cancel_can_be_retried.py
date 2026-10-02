"""Неудачная отмена не запирает интерфейс (issue #922, находка ``FE-2-03``).

Кнопка «Отмена» гасла сразу и навсегда, а ответ сервера глушился целиком
(``.catch(() => {})``). Неудачная отмена — 404 (прогон уже свернулся), 409,
обрыв сети — оставляла пользователя в тупике: прогон идёт, кнопка мертва,
нажать ещё раз нечем, и выход один — перезагрузка страницы. Ровно это запрещает
первый критерий приёмки подэпика.

Тот же путь в «Песочнице» починен давно; здесь проверяется, что раздел
«Проверка» ведёт себя так же — расхождение соседей и было дефектом.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from typing import Any

from playwright.sync_api import expect

import stepik_grader
from tests.e2e._helpers import write_task

_TIMEOUT_MS = 15_000


def server_text(key: str) -> str:
    """Строка из серверного каталога — та, что приезжает в поле ``message``."""
    catalogue = json.loads(
        (pathlib.Path(stepik_grader.__file__).parent / "core" / "locales" / "ru.json").read_text(
            encoding="utf-8"
        )
    )
    return catalogue[key]


def _start_a_long_run(page: Any, e2e_server: str, folder: Path) -> None:
    """Довести интерфейс до состояния «идёт прогон, отмена доступна».

    Решение спит: без этого прогон успевает закончиться раньше, чем тест
    доберётся до кнопки, и проверять станет нечего.
    """
    write_task(folder, "import time\ntime.sleep(30)\nprint(5)\n")
    page.goto(e2e_server + "/")
    # Режим «Бенчмарк»: только он идёт асинхронной джобой с кнопкой отмены,
    # обычные тесты уходят синхронным /api/grade (см. test_poller_resilience).
    page.click('.mode-btn[data-mode="bench"]')
    page.fill("#path", str(folder))
    page.click("#run")
    expect(page.locator("#cancel-run")).to_be_enabled(timeout=_TIMEOUT_MS)


def test_a_refused_cancel_leaves_the_button_usable(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Сервер отказал в отмене — кнопка возвращается, и сказано почему."""
    page.route(
        "**/api/v1/runs/*/cancel",
        lambda route: route.fulfill(
            status=409,
            content_type="application/json",
            body='{"kind": "error", "message": "прогон уже завершается"}',
        ),
    )

    _start_a_long_run(page, e2e_server, tmp_path)
    page.click("#cancel-run")

    # Причина названа — сообщением сервера, а не общей формулировкой.
    expect(page.locator("#toast-stack")).to_contain_text(
        "прогон уже завершается", timeout=_TIMEOUT_MS
    )
    # И повторить отмену есть чем: это и есть суть находки.
    expect(page.locator("#cancel-run")).to_be_enabled(timeout=_TIMEOUT_MS)


def test_a_broken_connection_also_returns_the_button(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Обрыв сети — тот же исход: сообщение и работающая кнопка.

    Отдельно от 409: у отказа сервера есть тело с причиной, у обрыва — нет, и
    ветка ``catch`` берёт текст из другого ключа каталога.
    """
    page.route("**/api/v1/runs/*/cancel", lambda route: route.abort())

    _start_a_long_run(page, e2e_server, tmp_path)
    page.click("#cancel-run")

    expect(page.locator("#toast-stack .toast-error")).to_be_visible(timeout=_TIMEOUT_MS)
    expect(page.locator("#cancel-run")).to_be_enabled(timeout=_TIMEOUT_MS)


def test_a_successful_cancel_still_ends_the_run(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """Контроль: штатная отмена по-прежнему завершает прогон и убирает кнопку.

    Страхует от «фикса», который разблокирует кнопку всегда — тогда после
    удачной отмены она предлагала бы отменить уже несуществующий прогон.
    """
    _start_a_long_run(page, e2e_server, tmp_path)
    page.click("#cancel-run")

    # Текст берётся из СЕРВЕРНОГО каталога, а не из ui.json: цикл опроса
    # печатает `data.message`, и подпись фронтенда здесь только запасная.
    expect(page.locator("#out")).to_contain_text(server_text("run_cancelled"), timeout=_TIMEOUT_MS)
    expect(page.locator("#cancel-run")).to_be_hidden(timeout=_TIMEOUT_MS)
