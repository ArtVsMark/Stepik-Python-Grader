"""web/auth_adapter.py — тонкий web-адаптер браузерного OAuth (issue #402).

Архитектурный слой: Application/UI (web-адаптер), как ``downloader_adapter.py``.
Оборачивает ``core/oauth_flow`` для веб-мастера первого запуска: проверка статуса
токена (``auth_status``) и полный браузерный flow (``perform_browser_auth``).

DAG: web → core (импортирует только ``core/*``), без обратных зависимостей.
``perform_browser_auth`` блокирует до 120с (``wait_for_auth_code`` внутри
``authorize_and_get_token``) и открывает браузер на МАШИНЕ СЕРВЕРА — корректно
только для локального single-user сценария (``--serve`` слушает localhost).
Поэтому вызывается из async-job (``web/runs.py``, ``kind="auth"``), а не прямо из
HTTP-обработчика. Для удалённого сервера (#151, не в scope) не применимо.
"""

from __future__ import annotations

import pathlib
from typing import Any

from stepik_grader.core.oauth_flow import authorize_and_get_token, token_is_valid
from stepik_grader.core.storage import load_json_file, save_secrets

__all__ = [
    "DEFAULT_REDIRECT_URI",
    "STEPIK_OAUTH_APPS_URL",
    "auth_status",
    "perform_browser_auth",
    "stored_credentials",
]

# Дублируется с downloader_config (issue #433) намеренно — web-слой не должен
# тянуть downloader (Application) ради двух констант; значение одно и то же.
STEPIK_OAUTH_APPS_URL = "https://stepik.org/oauth2/applications/"
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"

_CRED_FIELDS = ("client_id", "client_secret", "redirect_uri")


def auth_status(secrets_path: pathlib.Path) -> dict[str, Any]:
    """Статус авторизации по ``secrets.json`` для веб-мастера (issue #402).

    Возвращает ``{"authorized": bool, "reason": str}`` — ``reason`` в терминах,
    которые фронт локализует сам: ``ok`` (валидный токен), ``no_token`` (креды
    есть, токена нет/истёк — нужен браузерный flow), ``no_secrets`` (нет файла
    или неполные креды — нужна форма). Best-effort: битый/нечитаемый файл →
    ``no_secrets`` (не роняем сервер).
    """
    if not secrets_path.exists() or not secrets_path.is_file():
        return {"authorized": False, "reason": "no_secrets"}
    try:
        secrets = load_json_file(secrets_path)
    except (OSError, ValueError):
        return {"authorized": False, "reason": "no_secrets"}
    try:
        valid = token_is_valid(secrets)
    except (ValueError, TypeError):
        # Битый expires_at/access_token (напр. строка вместо числа) — трактуем как
        # «валидного токена нет», а не роняем сервер (best-effort, см. docstring).
        valid = False
    if valid:
        return {"authorized": True, "reason": "ok"}
    has_creds = all(str(secrets.get(field, "")).strip() for field in _CRED_FIELDS)
    return {"authorized": False, "reason": "no_token" if has_creds else "no_secrets"}


def stored_credentials(secrets_path: pathlib.Path) -> dict[str, str]:
    """OAuth-креды из существующего ``secrets.json`` (issue #723).

    Нужны, чтобы повторная авторизация (истёк токен) не требовала снова вводить
    ``client_id``/``client_secret``: они уже лежат в файле, а мастер первого
    запуска — не тот экран, который человек хочет видеть на второй раз.
    Возвращает только непустые строковые поля; отсутствие/битый файл — ``{}``
    (best-effort, как ``auth_status``). Секреты **не логируются** и наружу через
    API не отдаются — используются только для запуска flow на сервере.
    """
    if not secrets_path.exists() or not secrets_path.is_file():
        return {}
    try:
        secrets = load_json_file(secrets_path)
    except (OSError, ValueError):
        return {}
    return {
        field: str(secrets.get(field, "")).strip()
        for field in _CRED_FIELDS
        if str(secrets.get(field, "")).strip()
    }


def perform_browser_auth(
    secrets_path: pathlib.Path,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Записать креды в ``secrets.json`` (0600) и провести браузерный OAuth (issue #402).

    Сначала обновляет ``client_id``/``client_secret``/``redirect_uri`` в
    ``secrets.json`` через ``save_secrets`` (атомарно, 0600), СОХРАНЯЯ прочие поля
    (напр. ``refresh_token`` при повторной авторизации), затем
    ``authorize_and_get_token`` открывает браузер, ловит код на loopback-callback
    и дописывает токены в тот же файл. Порядок важен: без предварительной записи
    кредов файл остался бы без ``client_id``/``client_secret``. Блокирует до 120с
    — только из job.
    """
    existing: dict[str, Any] = {}
    if secrets_path.exists() and secrets_path.is_file():
        try:
            existing = load_json_file(secrets_path)
        except (OSError, ValueError):
            existing = {}
    existing.update(
        {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}
    )
    save_secrets(secrets_path, existing)
    authorize_and_get_token(client_id, client_secret, redirect_uri, secrets_path)
    return {"authorized": True, "reason": "ok"}
