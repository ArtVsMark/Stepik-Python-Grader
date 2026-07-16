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

from stepik_grader.core.storage import load_json_file, save_json_file, save_secrets

__all__ = [
    "DEFAULT_REDIRECT_URI",
    "DEFAULT_ROOT_DIR",
    "STEPIK_OAUTH_APPS_URL",
    "ask_value",
    "create_or_update_config",
    "create_secrets_interactively",
    "load_or_create_config",
    "normalize_config_paths",
    "slugify",
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
        print(text)


DEFAULT_ROOT_DIR = "StepikTasks"
# issue #433 — guided-настройка OAuth: где создать приложение Stepik и дефолтный
# redirect_uri (должен совпадать с полем «Redirect uris» приложения).
STEPIK_OAUTH_APPS_URL = "https://stepik.org/oauth2/applications/"
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"


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


def _confirm_yes(prompt: str) -> bool:
    """Y/n-подтверждение; пустой ввод (Enter) и «да»-варианты → True."""
    answer = input(f"{prompt} [Y/n]: ").strip().lower()
    return answer in {"", "y", "yes", "д", "да"}


def create_secrets_interactively(secrets_path: pathlib.Path) -> dict[str, str]:
    """Пошагово создаёт ``secrets.json`` с OAuth-данными Stepik (issue #433).

    Печатает, где завести OAuth-приложение Stepik и какие поля указать, затем
    спрашивает ``client_id``/``client_secret`` и ``redirect_uri`` (дефолт
    :data:`DEFAULT_REDIRECT_URI`) и пишет файл через
    :func:`~stepik_grader.core.storage.save_secrets` (атомарно, 0600). Токены НЕ
    запрашиваются — их добудет обычный browser-flow ``create_user_session`` при
    первом запуске. Пустые ``client_id``/``client_secret`` → файл НЕ создаётся
    (не пишем заведомо невалидный secrets.json). Возвращает собранный словарь.
    """
    _print("\n🔑 Настройка доступа к Stepik (OAuth).")
    _print(f"1. Откройте {STEPIK_OAUTH_APPS_URL} и создайте приложение:")
    _print("   • Name: любое (например, stepik-grader)")
    _print("   • Client type: Confidential")
    _print("   • Authorization grant type: Authorization code")
    _print(f"   • Redirect uris: {DEFAULT_REDIRECT_URI}")
    _print("2. Скопируйте Client id и Client secret созданного приложения.\n")
    client_id = ask_value("Client id")
    client_secret = ask_value("Client secret")
    redirect_uri = ask_value("Redirect uri", DEFAULT_REDIRECT_URI)
    secrets: dict[str, str] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri or DEFAULT_REDIRECT_URI,
    }
    if not client_id or not client_secret:
        # issue #402-review: не писать заведомо невалидный secrets.json — иначе
        # файл «существует», но load_secrets_dict позже упадёт ValueError. Честнее
        # не создавать файл и дать загрузчику показать дружелюбную ошибку.
        _print(
            "⚠️ Client id и Client secret обязательны — файл не создан. "
            "Заполните их и запустите загрузчик снова."
        )
        return secrets
    save_secrets(secrets_path, secrets)
    _print(f"✅ secrets.json сохранён: {secrets_path} (доступ только владельцу, 0600)")
    return secrets


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
        # issue #433: предложить пошаговое создание secrets.json прямо здесь.
        # Прежняя ветка лишь пере-запрашивала ПУТЬ, что при отсутствии готового
        # файла давало цикл path→FileNotFoundError. Отказ сохраняет прежнее
        # поведение (указать другой путь к уже существующему файлу).
        if _confirm_yes("Создать secrets.json сейчас (пошагово)?"):
            create_secrets_interactively(secrets_path)
        else:
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
