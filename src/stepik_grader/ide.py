"""ide.py — генерация конфигов интеграции с IDE (эпик #80 Tier 2 / issue #58).

VS Code: пишет ``.vscode/tasks.json`` с задачами «проверить текущий файл /
папку / бенчмарк / веб-интерфейс» — грейдинг одной кнопкой (``Ctrl+Shift+B``
для дефолтной задачи, либо Terminal → Run Task). PyCharm настраивается
вручную (External Tool) — инструкция в README.

Leaf-модуль: зависит только от stdlib (``json``, ``pathlib``), не импортирует
project-модули.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

__all__ = ["VSCODE_TASKS", "write_vscode_tasks"]

# Запускаем через интерпретатор, выбранный в VS Code (Python-расширение),
# а не через консольную команду `stepik-grader`: она есть в PATH только при
# активированном venv, что для новичка неочевидно и ломало запуск задач.
# ${command:python.interpreterPath} + `-m stepik_grader.grader` работает с тем
# же интерпретатором, где установлен пакет, без ручной активации venv.
# type=process (а не shell) — путь к интерпретатору может содержать пробелы;
# process исполняет команду напрямую, без шелл-квотинга аргументов.
_PYTHON = "${command:python.interpreterPath}"
_MODULE = ["-m", "stepik_grader.grader"]


def _task(
    label: str, args: list[str], *, default: bool = False, clear: bool = True
) -> dict[str, Any]:
    """Собрать одну VS Code-задачу, запускающую грейдер через интерпретатор VS Code."""
    task: dict[str, Any] = {
        "label": label,
        "type": "process",
        "command": _PYTHON,
        "args": [*_MODULE, *args],
        "presentation": {"reveal": "always", "panel": "dedicated", "clear": clear},
        "problemMatcher": [],
    }
    if default:
        task["group"] = {"kind": "build", "isDefault": True}
    return task


# ${file} — текущий открытый файл, ${fileDirname} — его папка (переменные VS Code).
VSCODE_TASKS: dict[str, Any] = {
    "version": "2.0.0",
    "tasks": [
        _task("Stepik: проверить текущий файл", ["--mode", "1", "--file", "${file}"], default=True),
        _task("Stepik: проверить папку", ["--mode", "2", "--dir", "${fileDirname}"]),
        _task("Stepik: бенчмарк папки", ["--mode", "3", "--dir", "${fileDirname}"]),
        _task("Stepik: веб-интерфейс", ["--serve"], clear=False),
    ],
}


def write_vscode_tasks(
    target_dir: pathlib.Path = pathlib.Path("."),
    *,
    overwrite: bool = False,
) -> tuple[bool, pathlib.Path]:
    """Записать ``.vscode/tasks.json`` в target_dir.

    Возвращает ``(written, path)``. ``written == False``, если файл уже
    существует и ``overwrite=False`` — чужой конфиг не перезатираем молча.
    """
    path = target_dir / ".vscode" / "tasks.json"
    if path.exists() and not overwrite:
        return False, path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(VSCODE_TASKS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True, path
