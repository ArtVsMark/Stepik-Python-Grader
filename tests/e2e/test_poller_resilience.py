"""test_poller_resilience.py — раздел «Проверка» переживает потерю прогона (issue #801).

Реестр job'ов живёт в памяти сервера, поэтому перезапуск ``--serve`` во время
прогона превращает следующий опрос в 404-JSON без поля ``progress``. Цикл
опроса читал ``data.progress.done`` безусловно: TypeError внутри колбэка
``setTimeout`` некому было поймать, промис не резолвился, ``_finishGradeUI()``
не вызывался — спиннер крутился вечно, кнопки оставались заблокированными, а
сообщения не появлялось. Выйти можно было только перезагрузкой страницы.

Перезапускать сервер прямо в тесте не нужно: важен ответ, который видит
браузер, поэтому 404 подставляется перехватом маршрута Playwright — так тест
детерминирован и не зависит от гонки со стартом нового процесса.

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 10_000


def test_lost_run_shows_error_and_unlocks_ui(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """404 на опросе завершает прогон сообщением, а не вечным спиннером."""
    write_task(tmp_path, "print(int(input()) + 1)\n")

    # Опрос статуса отвечает так же, как перезапущенный сервер: 404 + JSON
    # без progress. Создание прогона (POST /api/v1/runs, без хвоста) шаблон не
    # задевает — иначе UI не дошёл бы до цикла опроса, и тест проверял бы не то.
    page.route(
        "**/api/v1/runs/*",
        lambda route: route.fulfill(
            status=404,
            content_type="application/json",
            body='{"kind": "error", "message": "run not found"}',
        ),
    )

    page.goto(e2e_server + "/")
    # Режим «Бенчмарк», а не «Тесты»: цикл опроса живёт только в gradeAsync
    # (режимы 1/3/4), а режим 2 идёт синхронным /api/grade и до опроса не
    # доходит вовсе — на нём перехват 404 молча не срабатывал.
    page.click('.mode-btn[data-mode="bench"]')
    page.fill("#path", str(tmp_path))
    page.click("#run")

    # 1) Пользователь видит, что произошло, и знает следующий шаг. Язык
    # интерфейса берётся из браузера, а он на CI-раннере может быть любым —
    # поэтому ищем слово в обеих локалях, а не в той, что стоит у автора.
    expect(page.locator("#out")).to_contain_text(
        re.compile(r"сервер|server", re.IGNORECASE), timeout=_TIMEOUT_MS
    )

    # 2) Интерфейс разблокирован — можно запустить проверку снова, не
    # перезагружая страницу. Это и есть суть issue: раньше кнопка оставалась
    # выключенной навсегда.
    expect(page.locator("#run")).to_be_enabled(timeout=_TIMEOUT_MS)

    # 3) Прогресс и кнопка отмены убраны: прогона больше нет.
    expect(page.locator("#cancel-run")).to_be_hidden(timeout=_TIMEOUT_MS)
    assert page.locator("#bar").inner_html() == ""


def test_normal_run_still_completes(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """Контроль: без перехвата обычный прогон доходит до таблицы результатов.

    Страхует от «фикса», который завершает цикл слишком рано и ломает штатный
    путь — проверка проходит по тому же UI, что и тест выше.
    """
    write_task(tmp_path, "print(int(input()) + 1)\n")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="bench"]')
    page.fill("#path", str(tmp_path))
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    expect(page.locator("#run")).to_be_enabled(timeout=_TIMEOUT_MS)
    expect(page.locator("#cancel-run")).to_be_hidden(timeout=_TIMEOUT_MS)
