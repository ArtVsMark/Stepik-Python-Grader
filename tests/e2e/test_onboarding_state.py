"""test_onboarding_state.py — стартовый экран помнит выбор пользователя (issue #934).

Не входит в обычный ``pytest tests/`` — см. ``tests/e2e/conftest.py`` и
``norecursedirs`` в ``pyproject.toml``. Запуск: ``pytest tests/e2e/`` (после
``pip install -e ".[e2e]"`` + ``playwright install chromium``).

Почему браузером, а не unit-тестом: дефект был в связке «разметка + JS +
запрос к серверу». Галка стояла ``checked`` в HTML, JS не читал сохранённое
состояние, и закрытие модалки всегда слало ``seen=true`` — то есть настройка
переворачивалась сама. Проверить это можно только там, где все три части
работают вместе.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from stepik_grader.core import user_settings
from stepik_grader.web import server as web_server

_TIMEOUT_MS = 10_000


def _server_with_onboarding_pending(tmp_path: Path) -> tuple[str, Any]:
    """Сервер, для которого онбординг ещё не закрыт (``onboarding_seen`` не задан)."""
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0), web_server._Handler, workspace=tmp_path, confine=True
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    return f"http://{host}:{port}", httpd


def _saved_seen(tmp_path: Path) -> object:
    raw = user_settings.default_settings_path(tmp_path).read_text(encoding="utf-8")
    return json.loads(raw).get("onboarding_seen")


def test_unchecked_box_keeps_autoshow_on(page: Any, tmp_path: Path) -> None:
    """Снял галку и закрыл → сервер получает ``seen=false``, окно вернётся.

    Прежде уходил безусловный ``true``: пользователь просил показывать экран
    дальше, а первое же закрытие отменяло его выбор.
    """
    base, httpd = _server_with_onboarding_pending(tmp_path)
    try:
        page.goto(base + "/")
        page.wait_for_selector("#onboarding-overlay:not([hidden])", timeout=_TIMEOUT_MS)

        checkbox = page.locator("#onboarding-dont-show")
        assert checkbox.is_checked() is False, "галка отмечена, хотя настройка не сохранялась"

        page.click("#onboarding-close")
        page.wait_for_timeout(300)  # запрос POST /api/v1/settings уходит асинхронно
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert _saved_seen(tmp_path) is False


def test_checked_box_disables_autoshow(page: Any, tmp_path: Path) -> None:
    """Отметил галку и закрыл → ``seen=true``, окно больше не всплывает само."""
    base, httpd = _server_with_onboarding_pending(tmp_path)
    try:
        page.goto(base + "/")
        page.wait_for_selector("#onboarding-overlay:not([hidden])", timeout=_TIMEOUT_MS)

        page.check("#onboarding-dont-show")
        page.click("#onboarding-close")
        page.wait_for_timeout(300)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert _saved_seen(tmp_path) is True


def test_saved_choice_is_shown_on_reopen(page: Any, tmp_path: Path) -> None:
    """Открыл экран заново — галка показывает СОХРАНЁННОЕ состояние.

    Именно здесь дефект был виден глазами: настройка сохранена как «показывать»,
    а окно каждый раз предлагало «не показывать» — и достаточно было закрыть его,
    чтобы выбор молча перевернулся.
    """
    user_settings.save_fields(user_settings.default_settings_path(tmp_path), onboarding_seen=True)
    base, httpd = _server_with_onboarding_pending(tmp_path)
    try:
        page.goto(base + "/")
        # Авто-показа нет — открываем кнопкой в topbar.
        page.click("#onboarding-open")
        page.wait_for_selector("#onboarding-overlay:not([hidden])", timeout=_TIMEOUT_MS)

        assert page.locator("#onboarding-dont-show").is_checked() is True
    finally:
        httpd.shutdown()
        httpd.server_close()
