"""Tests for diagnostic_stepik.py — OAuth-диагностика шага (issue #1017).

Диагностику запускают именно тогда, когда «что-то не так с токеном», поэтому
проверяется главное: она поднимает сессию тем же путём, что загрузчик и стенд
корпуса (валидный ``access_token`` → обмен ``refresh_token`` → и только потом
браузер), а не требует браузерной авторизации при живых токенах.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import pytest

from stepik_grader import diagnostic_stepik
from stepik_grader.core import oauth_flow


def _live_secrets() -> dict[str, Any]:
    """Секреты с заведомо валидным access_token — браузер не нужен."""
    return {
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "http://localhost:8080/callback",
        "access_token": "live-token",
        "refresh_token": "refresh",
        "expires_at": time.time() + 3600,
    }


def test_diagnostic_uses_shared_session_factory() -> None:
    """У диагностики нет своей реализации авторизации (issue #1017).

    Вторая реализация — это второй набор приоритетов: прежняя версия звала
    ``authorize_via_browser`` сразу и не знала о сохранённых токенах, поэтому
    при полностью рабочем ``secrets.json`` диагностика падала по таймауту
    ожидания кода, тогда как загрузчик той же машины работал молча.
    """
    assert diagnostic_stepik.create_user_session is oauth_flow.create_user_session


def test_valid_token_does_not_open_browser(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой access_token → сессия без браузера, ни одного OAuth-редиректа."""

    def _explode(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("браузерная авторизация при валидном токене")

    monkeypatch.setattr("stepik_grader.core.stepik_client.authorize_via_browser", _explode)

    session = diagnostic_stepik.create_user_session(_live_secrets(), tmp_path / "secrets.json")

    assert session.headers["Authorization"] == "Bearer live-token"


def test_expired_token_is_refreshed_without_browser(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Истёкший access_token обменивается по refresh_token, тоже без браузера.

    Ровно тот случай, что воспроизведён на машине владельца: ``expires_at`` в
    прошлом, ``refresh_token`` живой — загрузчик работал, диагностика открывала
    браузер и ждала 120 секунд.
    """

    def _explode(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("браузерная авторизация при живом refresh_token")

    monkeypatch.setattr("stepik_grader.core.stepik_client.authorize_via_browser", _explode)
    monkeypatch.setattr(
        "stepik_grader.core.stepik_client.refresh_access_token",
        lambda *_a, **_k: {"access_token": "fresh-token", "expires_in": 3600},
    )
    secrets = _live_secrets()
    secrets["expires_at"] = time.time() - 1
    secrets_path = tmp_path / "secrets.json"

    session = diagnostic_stepik.create_user_session(secrets, secrets_path)

    assert session.headers["Authorization"] == "Bearer fresh-token"
    assert secrets_path.is_file()  # новая пара токенов сохранена
