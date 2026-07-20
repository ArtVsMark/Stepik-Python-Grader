"""storage.py — утилиты для чтения и записи JSON-файлов.

Архитектурный слой: Infrastructure / Utilities.
Не имеет зависимостей от других модулей проекта.
Отвечает исключительно за:
  - чтение JSON-файлов с диска,
  - запись dict в JSON-файл,
  - сохранение secrets dict в файл.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
from typing import Any

__all__ = [
    "load_json_file",
    "save_json_file",
    "save_secrets",
]


def load_json_file(file_path: pathlib.Path) -> dict[str, Any]:
    """Читает JSON-файл и возвращает dict.

    Raises:
        IsADirectoryError: если file_path — директория (кросс-платформенно;
            на Windows open() бросает PermissionError вместо IsADirectoryError).
        ValueError: если корень JSON не является объектом.
    """
    if file_path.is_dir():
        raise IsADirectoryError(f"Ожидался файл, получена директория: {file_path}")
    with file_path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_json_file(file_path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Атомарно сохраняет dict как JSON-файл, создавая родительские директории.

    issue #408: пишем во временный файл в ТОЙ ЖЕ директории и заменяем цель
    через ``os.replace`` (атомарный rename на POSIX и Windows) — прерывание,
    краш или конкурентная запись не оставляют усечённый файл, а читатель видит
    либо старую, либо новую полную версию (прежний ``open("w")`` сначала обрезал
    целевой файл). Temp рядом с целью — чтобы replace шёл в пределах одной ФС
    (rename между ФС не атомарен). Общий атомарный писатель для
    cache.py/meta.json/downloader-config (все зовут эту функцию).
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    # mkstemp в той же директории → уникальное имя (параллельные писатели не
    # делят temp) и приватные права 0600 на время записи.
    fd, tmp_name = tempfile.mkstemp(
        dir=file_path.parent, prefix=f".{file_path.name}.", suffix=".tmp"
    )
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        # Сохранить права уже существующего файла (mkstemp даёт 0600); для нового
        # файла остаётся 0600 — не секрет, но owner-only безопаснее и локально
        # достаточно. На Windows chmod по сути no-op (права — NTFS ACL).
        with contextlib.suppress(OSError):
            tmp_path.chmod(file_path.stat().st_mode & 0o777)
        tmp_path.replace(file_path)
    except OSError:
        # best-effort уборка temp при сбое записи/replace — цель не тронута.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def save_secrets(secrets_path: pathlib.Path, data: dict[str, Any]) -> None:
    """Сохраняет secrets dict (OAuth-токены, client_secret) с правами только для владельца.

    На POSIX файл создаётся атомарно в режиме 0600 (``os.open`` с явным
    ``mode``, без окна с более широкими правами между созданием и chmod) —
    и принудительно приводится к 0600, если уже существовал с более
    широкими правами от старой версии. На Windows у ``os.chmod`` нет
    эквивалента Unix-битам group/other (модель доступа — NTFS ACL, а не
    биты режима), поэтому там вызов практически no-op и файл остаётся
    защищён только стандартными правами профиля пользователя ОС
    (issue #243, security audit finding F-04).

    Запись **атомарна** (issue #628): temp-файл в той же директории через
    ``mkstemp`` (создаётся сразу с 0600 — окна с широкими правами нет), затем
    ``os.replace``. Прежний путь ``os.open(..., O_TRUNC)`` писал поверх цели:
    конкурентный читатель мог получить усечённый JSON (``--serve`` работает на
    ``ThreadingHTTPServer``, а обновление токена идёт из потока-обработчика), а
    краш ровно в момент записи затирал ``refresh_token`` — пользователю
    приходилось заново проходить браузерный OAuth. Докстринги
    ``oauth_flow``/``web.auth_adapter``, обещавшие атомарность, теперь верны.
    """
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(
        dir=secrets_path.parent, prefix=f".{secrets_path.name}.", suffix=".tmp"
    )
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        # В отличие от save_json_file права цели НЕ наследуются: секреты всегда
        # приводятся к 0600, даже если старый файл остался с более широкими
        # правами от прежней версии (issue #243). os.replace переносит режим
        # temp-файла на цель, поэтому гарантия сохраняется и после замены.
        with contextlib.suppress(OSError):
            tmp_path.chmod(0o600)
        tmp_path.replace(secrets_path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
