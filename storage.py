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
import pathlib
from typing import Any


def load_json_file(file_path: pathlib.Path) -> dict[str, Any]:
    """Читает JSON-файл и возвращает dict. Бросает ValueError если корень не объект."""
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
    """Сохраняет secrets dict в файл."""
    save_json_file(secrets_path, data)
