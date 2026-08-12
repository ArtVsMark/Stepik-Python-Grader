"""settings_adapter.py — тонкий web-адаптер над пользовательскими настройками (issue #830).

Архитектурный слой: Application/UI (web-адаптер), как ``reference_adapter.py``.
Своей логики не добавляет — знает только, что настройки лежат в
``workspace / SETTINGS_FILE_NAME``, и умеет читать/переключать конкретный флаг,
не роняя запрос на недоступном диске (``OSError`` гасится: настройка — не
критичный для ответа путь).

Существует, чтобы слой маршрутов (``api_routes.py``) не ходил в
``core.user_settings`` напрямую: роутер — тонкая обёртка над адаптерами и
viewmodels, бизнес-логики не добавляет (docs/dev/architecture.md, ADR-0010).
"""

from __future__ import annotations

import contextlib
import pathlib

from stepik_grader.core.user_settings import (
    SETTINGS_FILE_NAME,
    UserSettings,
    load_settings,
    save_fields,
)

__all__ = ["read_settings", "set_flag"]


def _path_for(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / SETTINGS_FILE_NAME


def read_settings(workspace: pathlib.Path) -> UserSettings:
    """Прочитать настройки рабочей директории (дефолты, если файла нет)."""
    return load_settings(_path_for(workspace))


def set_flag(workspace: pathlib.Path, name: str, value: bool) -> UserSettings:
    """Выставить булев флаг настроек и сохранить, если значение изменилось.

    Возвращает актуальные настройки (уже с новым значением). Запись, упавшая по
    ``OSError``, не мешает ответу: флаг применён в памяти запроса, а следующий
    запуск просто увидит прежнее значение на диске.
    """
    settings = read_settings(workspace)
    if getattr(settings, name) is not value:
        setattr(settings, name, value)
        with contextlib.suppress(OSError):
            # issue #997: пишем один флаг, а не снапшот — иначе веб затирал бы
            # то, что параллельно переключили в интерактивном меню.
            save_fields(_path_for(workspace), **{name: value})
    return settings
