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

from stepik_grader.core.stepik_client import (
    authorize_via_browser,
    create_user_session,
    make_session,
    refresh_access_token,
    token_is_valid,
    wait_for_auth_code,
)
from stepik_grader.core.storage import load_json_file, save_json_file, save_secrets

__all__ = [
    "load_secrets",
    "load_secrets_dict",
    "token_is_valid",
    "wait_for_auth_code",
    "authorize_via_browser",
    "authorize_and_get_token",
    "create_user_session",
    "make_session",
    "refresh_access_token",
    "try_create_session_without_browser",
]

_REQUIRED_FIELDS = ("client_id", "client_secret", "redirect_uri")


def load_secrets_dict(secrets_path: pathlib.Path | str) -> dict[str, Any]:
    """Читает и валидирует secrets.json, возвращает полный словарь.

    Включает токены (access_token / refresh_token / expires_at), если они есть.
    Используется там, где нужен доступ к токенам (например, create_user_session).

    Raises:
        FileNotFoundError: если файл не существует
        IsADirectoryError: если путь указывает на директорию
        ValueError: если корень не JSON-объект или отсутствует обязательное поле
    """
    data = load_json_file(pathlib.Path(secrets_path))
    for field in _REQUIRED_FIELDS:
        if not str(data.get(field, "")).strip():
            raise ValueError(f"В secrets.json должно быть заполнено поле {field!r}")
    return data


def load_secrets(secrets_path: pathlib.Path | str) -> tuple[str, str, str]:
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
        return make_session(str(secrets["access_token"]))

    refresh_token = str(secrets.get("refresh_token", "")).strip()
    if not refresh_token:
        return None

    client_id = str(secrets.get("client_id", ""))
    client_secret = str(secrets.get("client_secret", ""))
    try:
        token_data = refresh_access_token(client_id, client_secret, refresh_token)
    except requests.HTTPError:
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
    secrets_path: pathlib.Path | str = pathlib.Path("secrets.json"),
) -> dict[str, Any]:
    """Выполняет полный OAuth2 flow и сохраняет токены в secrets_path.

    Открывает браузер → перехватывает код → обменивает на токены, затем
    объединяет полученные токены с существующим содержимым secrets_path и
    сохраняет результат.

    Returns:
        Обновлённый словарь secrets (исходные поля + новые токены).
    """
    path = pathlib.Path(secrets_path)
    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)

    secrets: dict[str, Any] = {}
    if path.exists() and path.is_file():
        secrets = load_json_file(path)
    secrets.update(token_data)
    save_json_file(path, secrets)
    return secrets
