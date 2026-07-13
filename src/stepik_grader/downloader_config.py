"""downloader_config.py — конфиг и интерактив загрузчика задач (issue #302).

Архитектурный слой: Application. Выделено из ``downloader.py`` (SRP): работа с
``stepik_config.json`` (создание/загрузка/нормализация путей) и связанный с
ней консольный интерактив (``input()``), плюс утилита ``slugify`` для имён
директорий. Держится отдельно от ``core/`` намеренно — ``input()``-интерактив
не место в чистых Domain-модулях (leaf-инвариант ядра). ``downloader.py``
реэкспортирует эти имена для обратной совместимости.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from stepik_grader.core.storage import load_json_file, save_json_file

__all__ = [
    "DEFAULT_ROOT_DIR",
    "slugify",
    "ask_value",
    "create_or_update_config",
    "load_or_create_config",
    "normalize_config_paths",
]

# Вывод через rich с graceful fallback на print() (инвариант CLAUDE.md).
# Свой локальный _console, а не core/reporter._console — модуль leaf-совместим
# и не тянет core-UI (тот же приём, что glossary/coverage.py, issue #354).
try:
    from rich.console import Console

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False


def _print(text: str) -> None:
    """Печать статусной строки через rich (markup off — безопасно для путей)."""
    if _RICH and _console is not None:
        _console.print(text, markup=False)
    else:
        _print(text)


DEFAULT_ROOT_DIR = "StepikTasks"


def slugify(text: str) -> str:
    """Преобразует текст в slug для имени директории. Макс 80 символов."""
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE)
    text = text.strip(".- ")
    return text[:80] or "task"


def ask_value(prompt: str, default: str = "") -> str:
    """Запрашивает значение у пользователя с опциональным дефолтом."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def create_or_update_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Интерактивно создаёт или перезаписывает stepik_config.json."""
    _print("\nНастройка конфигурации...")
    root_dir = ask_value(
        "Укажи корневую папку для всех задач Stepik",
        DEFAULT_ROOT_DIR,
    )
    secrets_path = ask_value("Укажи путь к secrets.json", "secrets.json")
    config: dict[str, Any] = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    _print(f"✅ Конфиг сохранён: {config_path.resolve()}")
    return config


def load_or_create_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Загружает конфиг; если не существует — запускает интерактивное создание."""
    if not config_path.exists():
        _print("⚠️ Конфиг не найден. Будет создан новый.")
        return create_or_update_config(config_path)
    config = load_json_file(config_path)
    _print("\nТекущая конфигурация:")
    _print(f"root_dir:     {config.get('root_dir', '')}")
    _print(f"secrets_path: {config.get('secrets_path', '')}")
    change = input("Нужно изменить настройку? [y/N]: ").strip().lower()
    if change in {"y", "yes", "д", "да"}:
        return create_or_update_config(config_path)
    return config


def normalize_config_paths(
    config: dict[str, Any],
    config_path: pathlib.Path,
) -> dict[str, Any]:
    """Нормализует пути в конфиге до абсолютных; повторно запрашивает при ошибках."""
    root_dir_value = str(config.get("root_dir", "")).strip()
    secrets_value = str(config.get("secrets_path", "")).strip()
    if not root_dir_value or not secrets_value:
        _print("⚠️ В конфиге не хватает обязательных полей.")
        config = create_or_update_config(config_path)
        root_dir_value = str(config["root_dir"]).strip()
        secrets_value = str(config["secrets_path"]).strip()
    root_dir = pathlib.Path(root_dir_value)
    secrets_path = pathlib.Path(secrets_value)
    if not root_dir.is_absolute():
        root_dir = pathlib.Path.cwd() / root_dir
    if not secrets_path.is_absolute():
        secrets_path = pathlib.Path.cwd() / secrets_path
    if not secrets_path.exists() or not secrets_path.is_file():
        _print(f"⚠️ Файл secrets не найден: {secrets_path}")
        config = create_or_update_config(config_path)
        root_dir = pathlib.Path(str(config["root_dir"]))
        secrets_path = pathlib.Path(str(config["secrets_path"]))
        if not root_dir.is_absolute():
            root_dir = pathlib.Path.cwd() / root_dir
        if not secrets_path.is_absolute():
            secrets_path = pathlib.Path.cwd() / secrets_path
    normalized: dict[str, Any] = {
        "root_dir": str(root_dir),
        "secrets_path": str(secrets_path),
    }
    save_json_file(config_path, normalized)
    return normalized
