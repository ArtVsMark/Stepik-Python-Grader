"""Сведения о сборке из ``_build_info.json`` — логическая версия для интерфейса.

Зачем отдельный файл. Версия пакета (``importlib.metadata``) — это PEP 440 от
``setuptools-scm``: на теге ``1.11.0``, вне тега ``1.11.0.post494+g76bb98c85``.
Вторая форма нужна (по хешу сборка сопоставляется с коммитом), но человеку она
не отвечает на вопрос «какая у меня версия». Логическую версию проекта
(``MAJOR.MINOR.PATCH``, где PATCH — число принятых PR после тега) считает
``scripts/version.py`` **по git-истории** — а в пакете, поставленном через pipx,
истории нет вовсе; дёргать git при старте окна тоже нельзя (на macOS + 3.14
git-подпроцесс подвисает на десятки секунд, issue #1166/#1149). Поэтому версия
считается один раз при сборке (``scripts/generate_build_info.py``) и кладётся в
пакет файлом, а здесь только читается.

Модуль — leaf: ни одного проектного импорта, только stdlib, никаких подпроцессов.
Файла нет (запуск из клона без сборки) — все функции отдают ``None``, и
вызывающая сторона просто не показывает строку версии.
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

__all__ = [
    "BUILD_INFO_NAME",
    "display_version",
    "read_build_info",
]

BUILD_INFO_NAME = "_build_info.json"


def read_build_info() -> dict[str, Any] | None:
    """Прочитать ``_build_info.json`` из пакета; ``None`` — файла нет или он битый.

    Returns:
        Разобранный словарь сборки либо ``None``. Битый файл — тот же ``None``,
        а не исключение: строка версии в окне не стоит упавшего запуска.
    """
    try:
        resource = importlib.resources.files(__package__ or "stepik_grader") / BUILD_INFO_NAME
        raw = resource.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError, ModuleNotFoundError, TypeError):
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def display_version(info: dict[str, Any] | None = None) -> str | None:
    """Логическая версия для показа человеку; ``None`` — показывать нечего.

    У релизной сборки PATCH всегда ноль — константа, которая ничего не
    сообщает, — поэтому показывается ``1.11``, а не ``1.11.0``. У промежуточной
    PATCH и есть содержательная часть: ``1.10.234`` — «234 принятых изменения
    после тега».

    Args:
        info: уже прочитанные сведения; ``None`` — прочитать самому.

    Returns:
        Строку вида ``1.10.234`` / ``1.11`` либо ``None``.
    """
    data = read_build_info() if info is None else info
    if not data:
        return None
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        return None
    version = version.strip()
    if data.get("released") is True:
        major_minor = version.rsplit(".", 1)[0]
        return major_minor or version
    return version
