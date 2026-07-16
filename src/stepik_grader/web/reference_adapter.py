"""reference_adapter.py — тонкий web-адаптер над импортом reference (issue #55).

Архитектурный слой: Application/UI (web-адаптер), как ``downloader_adapter.py``.
Никакой новой бизнес-логики — пас-through над
``core.stepik_reference.import_references_from_task_dir`` с web-специфичной
аутентификацией: ``try_create_session_without_browser`` (никогда не открывает
браузер/не блокирует поток HTTP-обработчика — см. её docstring). DAG:
``web → core`` ациклично.

Путь папки задачи конфайнится в workspace на уровне роутера
(``server.py`` через ``_confined_path``), сюда приходит уже безопасным.
"""

from __future__ import annotations

import pathlib
from typing import Any

import requests

from stepik_grader.core.oauth_flow import load_secrets_dict, try_create_session_without_browser
from stepik_grader.core.stepik_reference import (
    DEFAULT_MAX_TOP,
    import_references_from_task_dir,
)
from stepik_grader.web.downloader_adapter import _resolve_config

__all__ = ["import_reference"]


def import_reference(path: str, *, top: int = DEFAULT_MAX_TOP) -> dict[str, Any]:
    """Импортировать закреплённое решение Stepik в папку задачи — web (issue #55).

    Возвращает ``{"ok", "files", "message"}``. ``ok=False`` — понятная ошибка
    (нет secrets/OAuth/сеть/нет ветки/нет решений); никогда не бросает и не
    роняет сервер в 500 (паттерн ``downloader_adapter.download_task``).
    ``files`` — имена сохранённых ``task{N}_{100+}.py`` при успехе.
    """
    _root_dir, secrets_path = _resolve_config(None)

    try:
        secrets = load_secrets_dict(secrets_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return {
            "ok": False,
            "message": (
                f"Не найден или некорректен secrets.json ({exc}). "
                "См. docs/installation.md § Работа с API Stepik (OAuth)."
            ),
        }

    session = try_create_session_without_browser(secrets, secrets_path)
    if session is None:
        return {
            "ok": False,
            "message": (
                "Нужна авторизация Stepik: токен недействителен, и обновить его "
                "не удалось без браузера. Выполните `python -m stepik_grader.downloader` "
                "в терминале один раз для входа через браузер, затем повторите здесь."
            ),
        }

    try:
        saved = import_references_from_task_dir(pathlib.Path(path), max_top=top, session=session)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "message": f"Сетевая ошибка при обращении к Stepik: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"Непредвиденная ошибка: {exc}"}

    return {
        "ok": True,
        "files": [p.name for p in saved],
        "message": f"Импортировано reference-решений: {len(saved)}",
    }
