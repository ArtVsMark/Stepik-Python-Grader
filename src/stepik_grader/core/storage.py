"""storage.py — утилиты для чтения и записи JSON-файлов.

Архитектурный слой: Infrastructure / Utilities.
Не имеет зависимостей от других модулей проекта.
Отвечает исключительно за:
  - чтение JSON-файлов с диска,
  - запись dict в JSON-файл,
  - сохранение secrets dict в файл.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any


def load_json_file(file_path: pathlib.Path) -> dict[str, Any]:
    """Читает JSON-файл и возвращает dict.

    Raises:
        IsADirectoryError: если file_path — директория (кросс-платформенно;
            на Windows open() бросает PermissionError вместо IsADirectoryError).
        ValueError: если корень JSON не является объектом.
    """
    if pathlib.Path(file_path).is_dir():
        raise IsADirectoryError(f"Ожидался файл, получена директория: {file_path}")
    with open(file_path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_json_file(file_path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Сохраняет dict как JSON-файл, создавая родительские директории."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


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
    """
    secrets_path = pathlib.Path(secrets_path)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    fd = os.open(secrets_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(payload)
    os.chmod(secrets_path, 0o600)
