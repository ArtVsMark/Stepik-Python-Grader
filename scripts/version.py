#!/usr/bin/env python3
"""scripts/version.py — вычисляет версию проекта по схеме MAJOR.MINOR.PATCH.

Схема проекта (см. CONTRIBUTING.md §Версионирование, issue #68) — НЕ SemVer:

  * MAJOR.MINOR берутся из последнего git-тега вида ``vX.Y.0``;
  * PATCH = число коммитов после этого тега, БЕЗ автогенерированных
    badge-коммитов CI (``chore(ci): update badges [skip ci]``,
    ``.github/workflows/ci.yml``) — иначе PATCH растёт почти вдвое быстрее
    реальных изменений: этот коммит бот создаёт отдельно почти на каждый push
    в main вдобавок к самому merge-коммиту (issue #231).

До первого тега ``git describe`` завершается ошибкой — тогда MAJOR.MINOR
читаются из ``[project].version`` в pyproject.toml, а PATCH = число коммитов
в истории по той же логике исключения (монотонный счётчик, разумный fallback
уже сейчас).

Запуск::

    python scripts/version.py     # → напр. 1.2.17
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

__all__ = ["project_version"]

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# issue #231: подстрока commit-сообщения badge-бота (см. модульный докстринг) —
# --fixed-strings ищет её буквально, без интерпретации как regex.
_BOT_COMMIT_GREP = "chore(ci): update badges"


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


def _commits_since(rev_range: str) -> str:
    """Число «настоящих» коммитов в rev_range, без badge-бота (issue #231).

    --invert-grep --grep=<подстрока> --fixed-strings исключает коммиты,
    сообщение которых содержит _BOT_COMMIT_GREP буквально (не regex).
    """
    return (
        _git(
            "rev-list",
            "--count",
            rev_range,
            "--invert-grep",
            "--grep",
            _BOT_COMMIT_GREP,
            "--fixed-strings",
        )
        or "0"
    )


def project_version() -> str:
    """Вернуть версию вида '1.2.17' по схеме проекта (см. модульный докстринг)."""
    tag = _git("describe", "--tags", "--abbrev=0")
    if tag is not None:
        major, minor, _patch = tag.lstrip("v").split(".")
        commits = _commits_since(f"{tag}..HEAD")
        return f"{major}.{minor}.{commits}"

    # Fallback до первого тега: MAJOR.MINOR из pyproject, PATCH = все коммиты.
    major, minor = _major_minor_from_pyproject()
    commits = _commits_since("HEAD")
    return f"{major}.{minor}.{commits}"


if __name__ == "__main__":
    print(project_version())
