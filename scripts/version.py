#!/usr/bin/env python3
"""scripts/version.py — вычисляет версию проекта по схеме MAJOR.MINOR.PATCH.

Схема проекта (см. CONTRIBUTING.md §Версионирование, issue #68) — НЕ SemVer:

  * MAJOR.MINOR берутся из последнего git-тега вида ``vX.Y.0``;
  * PATCH = число коммитов после этого тега (``git describe --tags --long``).

До первого тега ``git describe`` завершается ошибкой — тогда MAJOR.MINOR
читаются из ``[project].version`` в pyproject.toml, а PATCH = полное число
коммитов в истории (монотонный счётчик, разумный fallback уже сейчас).

Запуск::

    python scripts/version.py     # → напр. 1.2.17
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

__all__ = ["project_version"]

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _git(*args: str) -> str | None:
    """Вернуть stdout git-команды без хвостового перевода строки.

    None при любой ошибке (git недоступен, не git-репозиторий, нет тегов) —
    вызывающая сторона трактует None как «данных нет» и уходит в fallback.
    """
    try:
        out = subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip()


def _major_minor_from_pyproject() -> tuple[str, str]:
    """Вернуть (MAJOR, MINOR) из ``[project].version`` pyproject.toml.

    ("0", "0") — если файл недоступен или версия не читается.
    """
    try:
        with _PYPROJECT.open("rb") as f:
            version = tomllib.load(f).get("project", {}).get("version", "0.0.0")
    except OSError:
        return "0", "0"
    parts = str(version).split(".")
    major = parts[0] if len(parts) > 0 else "0"
    minor = parts[1] if len(parts) > 1 else "0"
    return major, minor


def project_version() -> str:
    """Вернуть версию вида '1.2.17' по схеме проекта (см. модульный докстринг)."""
    described = _git("describe", "--tags", "--long")
    if described is not None:
        # vX.Y.0-N-gHASH → MAJOR.MINOR из тега, PATCH = N (коммитов после тега).
        tag, commits, _ = described.rsplit("-", 2)
        major, minor, _patch = tag.lstrip("v").split(".")
        return f"{major}.{minor}.{commits}"

    # Fallback до первого тега: MAJOR.MINOR из pyproject, PATCH = все коммиты.
    major, minor = _major_minor_from_pyproject()
    commits = _git("rev-list", "--count", "HEAD") or "0"
    return f"{major}.{minor}.{commits}"


if __name__ == "__main__":
    print(project_version())
