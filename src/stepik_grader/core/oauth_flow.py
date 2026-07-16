"""oauth_flow.py — публичный OAuth2-фасад.

Архитектурный слой: Infrastructure / Auth.
Предоставляет единую точку входа для OAuth2-авторизации Stepik:
  - load_secrets / load_secrets_dict — чтение OAuth-учётных данных
  - token_is_valid — проверка актуальности токена
  - wait_for_auth_code — HTTP-сервер для перехвата кода авторизации
  - authorize_via_browser — открыть браузер → получить код → обменять на токен
  - authorize_and_get_token — полный OAuth2 flow с сохранением токенов в файл
  - create_user_session / make_session — создание авторизованной requests.Session
  - refresh_access_token — обмен refresh_token на новую пару токенов

Вся HTTP/OAuth логика делегируется stepik_client.py.
Этот модуль — тонкий фасад, устраняющий дублирование между downloader.py и
diagnostic_stepik.py. Источник истины — stepik_client.py.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import requests

from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.stepik_client import (
    authorize_via_browser,
    create_user_session,
    make_session,
    refresh_access_token,
    token_is_valid,
    wait_for_auth_code,
)
from stepik_grader.core.storage import load_json_file, save_secrets

_log = get_logger("oauth_flow")  # issue #149: диагностический лог OAuth (opt-in)

__all__ = [
    "authorize_and_get_token",
    "authorize_via_browser",
    "create_user_session",
    "load_secrets",
    "load_secrets_dict",
    "make_session",
    "refresh_access_token",
    "token_is_valid",
    "try_create_session_without_browser",
    "wait_for_auth_code",
]

_REQUIRED_FIELDS = ("client_id", "client_secret", "redirect_uri")


def load_secrets_dict(secrets_path: pathlib.Path) -> dict[str, Any]:
    """Читает и валидирует secrets.json, возвращает полный словарь.

    Включает токены (access_token / refresh_token / expires_at), если они есть.
    Используется там, где нужен доступ к токенам (например, create_user_session).

    Raises:
        FileNotFoundError: если файл не существует
        IsADirectoryError: если путь указывает на директорию
        ValueError: если корень не JSON-объект или отсутствует обязательное поле
    """
    data = load_json_file(secrets_path)
    for field in _REQUIRED_FIELDS:
        if not str(data.get(field, "")).strip():
            raise ValueError(f"В secrets.json должно быть заполнено поле {field!r}")
    return data


def load_secrets(secrets_path: pathlib.Path) -> tuple[str, str, str]:
    """Читает OAuth-учётные данные из secrets.json.

    Returns:
        (client_id, client_secret, redirect_uri) — значения очищены от пробелов.

    Raises:
        FileNotFoundError: если файл не существует
        IsADirectoryError: если путь указывает на директорию
        ValueError: если корень не JSON-объект или отсутствует обязательное поле
    """
    data = load_secrets_dict(secrets_path)
    return (
        str(data["client_id"]).strip(),
        str(data["client_secret"]).strip(),
        str(data["redirect_uri"]).strip(),
    )


def try_create_session_without_browser(
    secrets: dict[str, Any],
    secrets_path: pathlib.Path,
) -> requests.Session | None:
    """Аутентифицированная ``requests.Session`` БЕЗ похода в браузер (issue #186).

    Реализует только первые 2 приоритета ``create_user_session`` (валидный
    ``access_token`` / обновление по ``refresh_token``) — третья, browser-ветка
    (``authorize_via_browser``) сознательно не выполняется: она открывает
    браузер и блокирует поток до 120с в ожидании колбэка, что недопустимо
    внутри обработчика HTTP-запроса веб-сервера (issue #186, web-адаптер
    downloader'а). Возвращает ``None``, если ни валидного токена, ни рабочего
    refresh_token нет — вызывающая сторона должна показать понятную ошибку и
    направить на первичную CLI-авторизацию (``python -m stepik_grader.downloader``).
    """
    if token_is_valid(secrets):
        _log.debug("access_token валиден — сессия без браузера")
        return make_session(str(secrets["access_token"]))

    refresh_token = str(secrets.get("refresh_token", "")).strip()
    if not refresh_token:
        _log.info("нет валидного access_token и нет refresh_token — нужна CLI-авторизация")
        return None

    client_id = str(secrets.get("client_id", ""))
    client_secret = str(secrets.get("client_secret", ""))
    try:
        _log.info("access_token истёк — обновляю по refresh_token")
        token_data = refresh_access_token(client_id, client_secret, refresh_token)
    except requests.HTTPError as exc:
        _log.warning("обновление по refresh_token не удалось: %s", exc)
        return None
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    secrets.update(token_data)
    save_secrets(secrets_path, secrets)
    return make_session(str(secrets["access_token"]))


# NOTE: utility, not called in production paths
def authorize_and_get_token(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    secrets_path: pathlib.Path = pathlib.Path("secrets.json"),
) -> dict[str, Any]:
    """Выполняет полный OAuth2 flow и сохраняет токены в secrets_path.

    Открывает браузер → перехватывает код → обменивает на токены, затем
    объединяет полученные токены с существующим содержимым secrets_path и
    сохраняет результат.

    Returns:
        Обновлённый словарь secrets (исходные поля + новые токены).
    """
    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)

    secrets: dict[str, Any] = {}
    if secrets_path.exists() and secrets_path.is_file():
        secrets = load_json_file(secrets_path)
    secrets.update(token_data)
    # issue #400: токены — через save_secrets (атомарно, 0600), а не
    # save_json_file (0644, world/group-readable), иначе обходится фикс #243.
    save_secrets(secrets_path, secrets)
    return secrets
