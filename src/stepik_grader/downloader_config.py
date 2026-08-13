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
import sys
from typing import Any

from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.core.storage import load_json_file, save_json_file, save_secrets

__all__ = [
    "DEFAULT_REDIRECT_URI",
    "DEFAULT_ROOT_DIR",
    "STEPIK_OAUTH_APPS_URL",
    "ask_value",
    "create_or_update_config",
    "create_secrets_interactively",
    "input_is_available",
    "load_or_create_config",
    "normalize_config_paths",
    "set_lang",
    "slugify",
]

# -- Язык интерактива (issue #821) -------------------------------------------
#
# Мастер OAuth — самый хрупкий шаг воронки, и он вызывается из меню, которое уже
# знает язык пользователя. Язык хранится модульным состоянием, а не тянется
# параметром через всю цепочку `load_or_create_config → normalize_config_paths →
# create_secrets_interactively`: так публичные сигнатуры остаются прежними
# (обратная совместимость `__all__`), а точка установки одна — `set_lang`,
# которую зовёт `downloader.main`. Тот же приём, что `_LANG` в `cli/__init__`.
_LANG = "ru"
_FALLBACK_LANG = "ru"


def set_lang(lang: str) -> None:
    """Задать язык интерактива загрузчика (issue #821)."""
    global _LANG
    _LANG = lang


def _t(key: str, /, **kwargs: object) -> str:
    """Строка каталога на текущем языке; отсутствующий ключ показывается как есть."""
    messages = load_locale_messages(_LANG) or load_locale_messages(_FALLBACK_LANG)
    template = messages.get(key, key)
    return template.format(**kwargs) if kwargs else template


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


# Зарезервированные имена устройств Windows (issue #838): каталог с таким
# именем там не создать — MS-DOS-наследие живо и в NTFS, причём с любым
# расширением (``con.txt`` тоже зарезервировано). Названия шагов приходят из
# ответа API, то есть их выбирает не пользователь: шаг с названием «CON» ронял
# бы скачивание OSError'ом на ровном месте.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def slugify(text: str) -> str:
    """Преобразует текст в slug для имени директории. Макс 80 символов.

    Имя приходит из ответа API (название курса/урока/шага), поэтому здесь же
    снимаются файловые ловушки: разделители пути и ``..`` не переживают чистку
    (``../../etc`` → ``etc``, ``..`` → ``task``), а зарезервированные имена
    Windows получают подчёркивание (``con`` → ``con_``).
    """
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE)
    text = text.strip(".- ")
    slug = text[:80] or "task"
    return f"{slug}_" if slug in _WINDOWS_RESERVED_NAMES else slug


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
    _print(f"\n{_t('dl_oauth_heading')}")
    _print(_t("dl_oauth_step1", url=STEPIK_OAUTH_APPS_URL))
    _print(_t("dl_oauth_name"))
    _print("   • Client type: Confidential")
    _print("   • Authorization grant type: Authorization code")
    _print(f"   • Redirect uris: {DEFAULT_REDIRECT_URI}")
    _print(f"{_t('dl_oauth_step2')}\n")
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
        _print(_t("dl_oauth_credentials_required"))
        return secrets
    save_secrets(secrets_path, secrets)
    _print(_t("dl_secrets_saved", path=secrets_path))
    return secrets


def create_or_update_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Интерактивно создаёт или перезаписывает stepik_config.json."""
    _print(f"\n{_t('dl_config_heading')}")
    root_dir = ask_value(_t("dl_config_root_dir"), DEFAULT_ROOT_DIR)
    secrets_path = ask_value(_t("dl_config_secrets_path"), "secrets.json")
    config: dict[str, Any] = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    _print(_t("dl_config_saved", path=config_path.resolve()))
    return config


def input_is_available() -> bool:
    """Есть ли у процесса интерактивный ввод (issue #1109).

    Пайп, запуск из скрипта, CI и GUI-обёртка дают закрытый или неинтерактивный
    ``stdin``: спрашивать там нечего и некого. ``OSError``/``ValueError`` —
    подменённый или уже закрытый поток; для наших целей это тоже «ввода нет».
    """
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def load_or_create_config(
    config_path: pathlib.Path,
    *,
    ask_to_change: bool = True,
) -> dict[str, Any]:
    """Загружает конфиг; если не существует — запускает интерактивное создание.

    ``ask_to_change=False`` — не предлагать правку существующего конфига:
    так зовёт неинтерактивный запуск с URL в аргументах (issue #1109). Раньше
    вопрос «Нужно изменить настройку?» задавался ВСЕГДА, поэтому
    ``python -m stepik_grader.downloader <URL>`` в скрипте или из GUI не
    работал вовсе: на закрытом ``stdin`` он падал ``EOFError``, а вызывающий
    получал совет «исправьте файл или удалите его» — про исправный файл.
    """
    if not config_path.exists():
        _print(_t("dl_config_missing"))
        return create_or_update_config(config_path)
    config = load_json_file(config_path)
    if not ask_to_change or not input_is_available():
        return config
    _print(f"\n{_t('dl_config_current')}")
    _print(f"root_dir:     {config.get('root_dir', '')}")
    _print(f"secrets_path: {config.get('secrets_path', '')}")
    try:
        change = input(f"{_t('dl_config_change_prompt')} [y/N]: ").strip().lower()
    except (EOFError, OSError):
        # Ввод пропал уже после проверки (терминал закрыли, поток отобрали).
        # Это не повод объявлять конфиг сломанным — работаем с тем, что есть.
        return config
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
        _print(_t("dl_config_incomplete"))
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
        _print(_t("dl_secrets_missing", path=secrets_path))
        # issue #433: предложить пошаговое создание secrets.json прямо здесь.
        # Прежняя ветка лишь пере-запрашивала ПУТЬ, что при отсутствии готового
        # файла давало цикл path→FileNotFoundError. Отказ сохраняет прежнее
        # поведение (указать другой путь к уже существующему файлу).
        if _confirm_yes(_t("dl_secrets_create_prompt")):
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
