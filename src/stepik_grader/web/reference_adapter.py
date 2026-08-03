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
from stepik_grader.web.downloader_adapter import secrets_path_for

__all__ = ["import_reference"]


def import_reference(
    path: str, *, top: int | None = None, workspace: pathlib.Path | None = None
) -> dict[str, Any]:
    """Импортировать закреплённое решение Stepik в папку задачи — web (issue #55).

    Возвращает ``{"ok", "files", "message"}``. ``ok=False`` — понятная ошибка
    (нет secrets/OAuth/сеть/нет ветки/нет решений); никогда не бросает и не
    роняет сервер в 500 (паттерн ``downloader_adapter.download_task``).
    ``files`` — имена сохранённых ``task{N}_{100+}.py`` при успехе.

    ``top=None`` (или неположительное) — предел ядра ``DEFAULT_MAX_TOP``:
    значение по умолчанию живёт здесь, чтобы слой маршрутов не знал про
    ``core.stepik_reference`` (issue #830, ARCH-07).

    ``workspace`` — рабочая директория сервера, относительно неё ищется
    ``stepik_config.json`` с путём к ``secrets.json`` (issue #723); ``None`` —
    текущая директория процесса.
    """
    max_top = top if top is not None and top > 0 else DEFAULT_MAX_TOP
    base = workspace if workspace is not None else pathlib.Path.cwd()
    secrets_path = secrets_path_for(base)

    try:
        secrets = load_secrets_dict(secrets_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return {
            "ok": False,
            "message": (
                f"Не найден или некорректен secrets.json ({exc}). "
                "См. docs/use/installation.md § Работа с API Stepik (OAuth)."
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
        saved = import_references_from_task_dir(
            pathlib.Path(path), max_top=max_top, session=session
        )
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
