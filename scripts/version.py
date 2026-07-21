#!/usr/bin/env python3
"""scripts/version.py — вычисляет версию проекта по схеме MAJOR.MINOR.PATCH.

Схема проекта (см. CONTRIBUTING.md §Версионирование, issue #68) — НЕ SemVer:

  * MAJOR.MINOR берутся из последнего git-тега вида ``vX.Y.0``;
  * PATCH = число ПРИНЯТЫХ изменений после этого тега — коммитов на first-parent
    линии (``--first-parent``: один смерженный PR даёт ровно один коммit — свой
    merge-commit, без внутренних коммитов PR и merge-дублей), БЕЗ
    автогенерированных badge-коммитов CI (``chore(ci): update badges [skip ci]``,
    ``.github/workflows/ci.yml``, issue #231). Так счётчик не зависит от того,
    как автор дробил PR на коммиты, и не завышается ни badge-ботом (коммитит
    прямо в main почти на каждый push), ни merge-коммитами.

До первого тега ``git describe`` завершается ошибкой — тогда MAJOR.MINOR
читаются из версии установленного пакета (``importlib.metadata`` поверх
setuptools-scm; статической ``[project].version`` в pyproject нет —
``dynamic = ["version"]``, issue #162/#183), а PATCH = число first-parent
изменений в истории по той же логике исключения (монотонный счётчик).

Запуск::

    python scripts/version.py     # → напр. 1.2.17
"""

from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

__all__ = ["project_version"]

# Имя дистрибутива (pyproject [project].name) — для чтения версии установленного
# пакета из метаданных (setuptools-scm; статической версии нет, issue #162/#183).
_DIST_NAME = "stepik-python-grader"

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


def _major_minor_from_metadata() -> tuple[str, str]:
    """Вернуть (MAJOR, MINOR) из версии установленного пакета (setuptools-scm).

    Проект НЕ хранит статическую версию (``dynamic = ["version"]``, setuptools-scm,
    issue #162/#183) — читаем её из метаданных установленного дистрибутива
    (``importlib.metadata``), формат ``X.Y.0.postN+g<hash>`` → берём X.Y. Прежняя
    версия читала удалённый ``[project].version`` и всегда деградировала в ``0.0``.

    ("0", "0") — только последний резерв (пакет не установлен / версия не
    парсится); в норме этот fallback срабатывает лишь до первого git-тега, когда
    пакет уже установлен для сборки бейджа.
    """
    try:
        raw = _dist_version(_DIST_NAME)
    except PackageNotFoundError:
        return "0", "0"
    parts = raw.split(".")
    major = parts[0] if parts and parts[0].isdigit() else "0"
    minor = parts[1] if len(parts) > 1 and parts[1].isdigit() else "0"
    return major, minor


def _commits_since(rev_range: str) -> str:
    """Число «принятых изменений» в rev_range: first-parent, без badge-бота.

    ``--first-parent`` (объективная метрика: один смерженный PR = один коммит на
    first-parent линии main — его merge-commit; внутренние коммиты PR и
    merge-дубли не считаются, поэтому счётчик не зависит от дробления PR на
    коммиты). ``--invert-grep --grep=<подстрока> --fixed-strings`` дополнительно
    исключает badge-коммиты бота (``chore(ci): update badges``, issue #231),
    которые попадают на first-parent линию (коммитятся прямо в main).
    """
    return (
        _git(
            "rev-list",
            "--count",
            "--first-parent",
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

    # Fallback до первого тега: MAJOR.MINOR из метаданных пакета, PATCH = все коммиты.
    major, minor = _major_minor_from_metadata()
    commits = _commits_since("HEAD")
    return f"{major}.{minor}.{commits}"


if __name__ == "__main__":
    print(project_version())
