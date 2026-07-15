"""core/user_settings.py — персистентные пользовательские настройки CLI (issue #430).

Архитектурный слой: Application / Configuration (leaf-модуль).

Отдельный от ``config.py`` слой. ``config.GraderConfig`` — ``frozen=True`` и
читается ТОЛЬКО из секции ``[tool.stepik-grader]`` в ``pyproject.toml`` (конфиг
проекта, который pipx-ученик не редактирует). Этот модуль хранит настройки,
переключаемые прямо из интерактивного меню (например тумблер записи истории,
issue #430), в файле ``.grader_settings.json`` в рабочей директории — рядом с
``.grader_history.db``/``.grader_stats.jsonl``, к которым эти настройки и
относятся.

Приоритет для меню (issue #430): user-state (этот файл) → ``pyproject.toml``
(``CONFIG.record_history``) → дефолт ``False``. ``record_history is None``
означает «пользователь не переопределял» — тогда меню наследует ``CONFIG``.

Leaf-модуль: не импортирует ничего из проекта (только stdlib), как
``storage.py``/``normalizers.py``/``glossary.py`` (инвариант CLAUDE.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "SETTINGS_FILE_NAME",
    "UserSettings",
    "default_settings_path",
    "load_settings",
    "save_settings",
]

SETTINGS_FILE_NAME = ".grader_settings.json"


@dataclass
class UserSettings:
    """Пользовательские настройки, переключаемые из меню (issue #430).

    ``record_history``: ``None`` — не переопределено (наследовать
    ``CONFIG.record_history``); ``True``/``False`` — явный выбор пользователя,
    сохранённый между запусками.
    """

    record_history: bool | None = None


def default_settings_path() -> Path:
    """Путь к файлу настроек в текущей рабочей директории (issue #430).

    Мирит семантику с ``history_recording.default_history_db_path()``:
    настройка живёт там же, где база истории, которой она управляет.
    """
    return Path.cwd() / SETTINGS_FILE_NAME


def load_settings(path: Path) -> UserSettings:
    """Прочитать настройки из ``path`` (best-effort).

    Отсутствие файла, битый JSON или неверный тип поля → дефолтные
    ``UserSettings`` (никогда не роняет CLI). Неизвестные ключи игнорируются —
    формат forward-compatible.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return UserSettings()
    try:
        data = json.loads(raw)
    except ValueError:
        return UserSettings()
    if not isinstance(data, dict):
        return UserSettings()
    record_history = data.get("record_history")
    return UserSettings(record_history=record_history if isinstance(record_history, bool) else None)


def save_settings(settings: UserSettings, path: Path) -> None:
    """Записать настройки в ``path`` атомарно (temp-файл + ``replace``).

    Пишутся только явно заданные (не-``None``) поля, чтобы файл не фиксировал
    «наследуемые из CONFIG» значения. Атомарная замена исключает частично
    записанный файл при сбое.
    """
    payload: dict[str, object] = {}
    if settings.record_history is not None:
        payload["record_history"] = settings.record_history
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
