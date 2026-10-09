#!/usr/bin/env python3
"""scripts/check_repo_root.py — корень репозитория по белому списку (issue #1587).

``data.json`` и ``data.pkl`` пролежали в корне больше месяца: пример карточки
глоссария записал файлы в текущий каталог, а коммит забрал их вместе с правкой.
Сторож прогона (``tests/conftest.py``) такое не видит — файл появился не во
время тестов, а ревью не замечает лишнюю строку в длинном списке файлов.

Проверяется то, что уедет в ``main``: записи верхнего уровня из
``git ls-files``. Новая запись в корне — осознанное решение, и оно делается
одной строкой в :data:`ALLOWED`, а не молча.

Исходы: ``0`` — чисто · ``1`` — в корне есть запись вне списка · ``2`` —
проверка не отработала (git не ответил).

Запуск::

    python scripts/check_repo_root.py
"""

import contextlib
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["ALLOWED", "main", "tracked_root_entries", "unexpected"]

_ROOT = Path(__file__).resolve().parent.parent

#: Записи верхнего уровня, которым место в корне. Каталоги и файлы вместе:
#: ``git ls-files`` отдаёт пути, и первая часть пути — запись корня.
ALLOWED: frozenset[str] = frozenset(
    {
        ".claude",
        ".gitattributes",
        ".github",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".rules",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "HISTORY.md",
        "LICENSE",
        "README.en.md",
        "README.md",
        "SECURITY.md",
        "changelog.d",
        "conftest.py",
        "corpus",
        "docs",
        "launcher.cmd",
        "launcher.sh",
        "pyproject.toml",
        "scripts",
        "secrets.json.example",
        "src",
        "stepik_config.json.example",
        "tests",
    }
)


def tracked_root_entries(root: Path = _ROOT) -> set[str]:
    """Записи верхнего уровня среди файлов под git.

    ``-z`` — потому что имя с не-ASCII символами git иначе экранирует, и
    запись выпала бы из сверки (правило 165).
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
        encoding="utf-8",
    ).stdout
    return {path.split("/", 1)[0] for path in out.split("\0") if path}


def unexpected(entries: set[str], allowed: frozenset[str] = ALLOWED) -> list[str]:
    """Записи корня вне белого списка, по алфавиту."""
    return sorted(entries - allowed)


def main(argv: list[str] | None = None) -> int:
    """Точка входа: находка — код 1, неотработавший git — код 2."""
    del argv
    try:
        entries = tracked_root_entries()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"проверка не отработала: git ls-files — {exc}", file=sys.stderr)
        return 2
    found = unexpected(entries)
    if not found:
        print(f"корень репозитория чист: {len(entries)} записей, все в белом списке")
        return 0
    for name in found:
        print(
            f"в корне репозитория лишняя запись: {name} — уберите её "
            "или добавьте в ALLOWED (scripts/check_repo_root.py), если ей место в корне"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
