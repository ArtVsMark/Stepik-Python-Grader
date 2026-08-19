"""shortcut.py — ярлык запуска лаунчера в системе (issue #1185).

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib).

Чтобы поднять веб-интерфейс, сегодня нужно дойти до нужного каталога и вспомнить
команду. Для владельца это трение, для новичка — тупик: человек установил пакет и
не понял, что делать дальше. Ярлык снимает и то, и другое: клик вместо строки.

Три платформы — три штатных механизма, **без новых зависимостей**:

* **Windows** — `.lnk` через PowerShell (`WScript.Shell`). Готовые обёртки
  (`pywin32`, `pyshortcuts`) добавили бы зависимость ради одного вызова;
* **Linux** — `.desktop` в `~/.local/share/applications`: файл появляется в меню
  приложений, а не только на рабочем столе, которого в части окружений нет;
* **macOS** — исполняемый `.command` на рабочем столе: `.app`-бандл требует
  подписи и структуры каталогов ради того же одного клика.

Ярлык ведёт на **gui-script** (``stepik-grader-gui``), а не на путь внутри venv:
путь переживёт переустановку пакета, а абсолютный путь к `python` из старого
окружения — нет.

**Сам по себе ярлык не создаётся никогда.** Только явным действием пользователя —
кнопкой в лаунчере или флагом ``--create-shortcut``. Непрошеный ярлык на рабочем
столе воспринимается как навязчивость, и это ровно та цена, которой инструмент
платить не должен.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["GUI_COMMAND", "ShortcutError", "create_shortcut", "shortcut_target"]

#: Имя gui-script из `[project.gui-scripts]`. Именно на него ведёт ярлык.
GUI_COMMAND = "stepik-grader-gui"

#: Имя ярлыка. Латиница намеренно: кириллица в именах файлов по-разному
#: нормализуется в git на macOS и ломает часть инструментов.
_SHORTCUT_STEM = "Stepik Grader"


class ShortcutError(RuntimeError):
    """Ярлык создать не удалось — с причиной, которую можно показать человеку."""


def shortcut_target() -> Path:
    """Путь к ``stepik-grader-gui``.

    Raises:
        ShortcutError: команда не найдена в PATH — пакет установлен без
            gui-script либо PATH не обновлён после установки.
    """
    found = shutil.which(GUI_COMMAND)
    if found:
        return Path(found)
    raise ShortcutError(
        f"{GUI_COMMAND} не найден в PATH. Установите пакет "
        "(`pipx install stepik-python-grader`) и перезапустите терминал."
    )


def _desktop_dir(home: Path) -> Path:
    """Рабочий стол пользователя; при отсутствии — сам домашний каталог.

    Локализованное имя папки («Рабочий стол») не угадывается: если стандартного
    ``Desktop`` нет, класть ярлык наугад хуже, чем положить в домашний каталог,
    где человек его точно найдёт.
    """
    desktop = home / "Desktop"
    return desktop if desktop.is_dir() else home


def _write_linux_desktop_entry(target: Path, home: Path) -> Path:
    """`.desktop` в `~/.local/share/applications` — ярлык появляется в меню."""
    applications = home / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    path = applications / "stepik-grader.desktop"
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Stepik Grader\n"
        "Comment=Локальная проверка решений Stepik\n"
        f"Exec={target}\n"
        "Terminal=false\n"
        "Categories=Development;Education;\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write_macos_command(target: Path, home: Path) -> Path:
    """Исполняемый `.command` — двойной клик в Finder запускает лаунчер."""
    path = _desktop_dir(home) / f"{_SHORTCUT_STEM}.command"
    path.write_text(f'#!/bin/sh\nexec "{target}"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_windows_lnk(target: Path, home: Path) -> Path:
    """`.lnk` через PowerShell: `WScript.Shell` есть в любой Windows."""
    path = _desktop_dir(home) / f"{_SHORTCUT_STEM}.lnk"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"'{path}'); $s.TargetPath = '{target}'; "
        f"$s.WorkingDirectory = '{home}'; $s.Description = 'Stepik Grader'; $s.Save()"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(f"не удалось запустить PowerShell: {exc}") from exc
    if completed.returncode != 0 or not path.exists():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ShortcutError(f"PowerShell не создал ярлык: {detail or 'причина неизвестна'}")
    return path


def create_shortcut(*, home: Path | None = None, system: str | None = None) -> Path:
    """Создать ярлык запуска лаунчера; вернуть путь созданного файла.

    Повторный вызов **перезаписывает** существующий ярлык, а не плодит копии:
    имя файла фиксированное, поэтому «нажал дважды» даёт один ярлык, а не
    ``Stepik Grader (1)``.

    Args:
        home: домашний каталог; подменяется в тестах, чтобы не трогать
            настоящий рабочий стол пользователя.
        system: имя ОС (``platform.system()``); подменяется в тестах.

    Raises:
        ShortcutError: gui-script не найден, платформа неизвестна или ФС
            отказала в записи.
    """
    target = shortcut_target()
    base = home if home is not None else Path.home()
    name = (system if system is not None else platform.system()).lower()

    writers = {
        "windows": _write_windows_lnk,
        "darwin": _write_macos_command,
        "linux": _write_linux_desktop_entry,
    }
    writer = writers.get(name)
    if writer is None:
        raise ShortcutError(
            f"создание ярлыка не поддержано на {name!r}: запустите {GUI_COMMAND} из терминала."
        )
    try:
        return writer(target, base)
    except OSError as exc:
        raise ShortcutError(f"не удалось записать ярлык: {exc}") from exc


def _self_check() -> int:  # pragma: no cover — ручная проверка на своей ОС
    """`python -m stepik_grader.core.shortcut` — создать ярлык и напечатать путь.

    Нужен для проверки на настоящей ОС: критерий приёмки #1185 прямо требует
    ручного прогона, потому что рабочий стол — поверхность, которую тестами не
    закрыть (в CI его нет, а подменённый `home` проверяет запись файла, но не
    то, что система считает ярлык рабочим).
    """
    try:
        print(create_shortcut())
    except ShortcutError as exc:
        print(f"не получилось: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_check())
