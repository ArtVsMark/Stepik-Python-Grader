#!/usr/bin/env python3
"""scripts/check_version_consistency.py — CI-guard против дрейфа версий (issue #165).

После перехода на динамическую версию из git-тегов (issue #162) единственный
источник истины — git-тег ``vX.Y.0``. Статической строки ``version`` в
``pyproject.toml`` больше нет. Этот скрипт ловит две категории регрессов:

1. **Возврат статического источника истины.** ``[project]`` в ``pyproject.toml``
   не должен снова объявлять ``version = "..."`` — только ``dynamic = ["version"]``.
2. **Дрейф "текущей версии" в документации.** ``CHECKPOINT.md`` (и, мягко,
   таблица метрик ``CLAUDE.md`` и таблица эволюции ``docs/versions.md``) и
   верхняя запись ``CHANGELOG.md`` должны соответствовать актуальному релизному
   baseline — последнему git-тегу ``vX.Y.0``.

Baseline вычисляется из git (``git describe --tags --abbrev=0``). Сравнение
CHECKPOINT/CLAUDE ведётся только по ``MAJOR.MINOR`` — PATCH в схеме проекта это
счётчик коммитов (см. CONTRIBUTING.md §Версионирование), он меняется на каждом
коммите и в доках не фиксируется построчно. CHANGELOG сверяется целиком (там
записи — только релизные ``X.Y.0``).

Границы (осознанно, чтобы не быть хрупким):
  * Нет git / нет тегов → скрипт печатает SKIP и выходит 0 (например, сборка из
    tarball без истории). В CI теги доступны (``fetch-depth: 0``).
  * Маркер в CLAUDE.md отсутствует → предупреждение, не ошибка (таблица метрик —
    свободный формат и может быть реструктурирована).
  * Проверяются только перечисленные выше файлы — это НЕ полный сканер всех
    вхождений "X.Y.Z" в репозитории (иначе ложные срабатывания на исторических
    номерах в CHANGELOG/CLAUDE-примечаниях неизбежны).

Запуск::

    python scripts/check_version_consistency.py     # exit 0 — ок, 1 — дрейф
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_CHECKPOINT = _ROOT / "CHECKPOINT.md"
_CHANGELOG = _ROOT / "CHANGELOG.md"
_CLAUDE = _ROOT / "CLAUDE.md"
_VERSIONS = _ROOT / "docs" / "versions.md"

_SEMVERISH = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _latest_tag_baseline() -> tuple[int, int, int] | None:
    """(MAJOR, MINOR, PATCH) последнего git-тега ``vX.Y.0`` или None.

    None — если git недоступен или тегов нет (вызывающая сторона трактует как
    SKIP: сверять не с чем).
    """
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"],
            cwd=_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    m = _SEMVERISH.search(out)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _check_pyproject_dynamic(errors: list[str]) -> None:
    """[project] должен объявлять version как dynamic, а не статической строкой."""
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    project = data.get("project", {})
    if "version" in project:
        errors.append(
            "pyproject.toml: [project].version is declared statically "
            f"({project['version']!r}); a dynamic version from git tags is expected "
            '(issue #162). Remove the version line and keep dynamic = ["version"].'
        )
    if "version" not in project.get("dynamic", []):
        errors.append(
            'pyproject.toml: [project].dynamic does not contain "version"; '
            "the dynamic version from git tags is not wired up (issue #162)."
        )


def _find_first(pattern: str, text: str) -> tuple[int, int, int] | None:
    """Первое совпадение X.Y.Z для pattern (с группой версии) в тексте."""
    m = re.search(pattern, text)
    if not m:
        return None
    v = _SEMVERISH.search(m.group(0))
    if not v:
        return None
    return int(v.group(1)), int(v.group(2)), int(v.group(3))


def _check_checkpoint(baseline: tuple[int, int, int], errors: list[str]) -> None:
    """CHECKPOINT.md 'Текущая версия: X.Y.Z' — MAJOR.MINOR == baseline."""
    text = _CHECKPOINT.read_text(encoding="utf-8")
    found = _find_first(r"Текущая версия:\s*\d+\.\d+\.\d+", text)
    if found is None:
        errors.append(
            "CHECKPOINT.md: line 'Tekushchaya versiya: X.Y.Z' not found "
            "(required marker for comparison against the release baseline)."
        )
        return
    if found[:2] != baseline[:2]:
        errors.append(
            f"CHECKPOINT.md: current version {found[0]}.{found[1]}.{found[2]} "
            f"disagrees with the latest release tag v{baseline[0]}.{baseline[1]}.0 "
            f"(expected MAJOR.MINOR = {baseline[0]}.{baseline[1]})."
        )


def _check_changelog(baseline: tuple[int, int, int], errors: list[str]) -> None:
    """CHANGELOG.md: верхняя запись '## [X.Y.Z]' == baseline (X.Y.0)."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    # Первая запись вида '## [X.Y.Z]' — пропускаем '## [Unreleased]'.
    found = _find_first(r"##\s*\[\d+\.\d+\.\d+\]", text)
    if found is None:
        errors.append("CHANGELOG.md: no entry of the form '## [X.Y.Z]' found.")
        return
    if found != baseline:
        errors.append(
            f"CHANGELOG.md: top release entry [{found[0]}.{found[1]}.{found[2]}] "
            f"does not match the latest git tag v{baseline[0]}.{baseline[1]}.{baseline[2]}."
        )


def _check_claude_metrics(baseline: tuple[int, int, int], warnings: list[str]) -> None:
    """CLAUDE.md таблица метрик '| Версия | X.Y.Z' — MAJOR.MINOR == baseline (мягко)."""
    text = _CLAUDE.read_text(encoding="utf-8")
    found = _find_first(r"\|\s*Версия\s*\|\s*\d+\.\d+\.\d+", text)
    if found is None:
        warnings.append("CLAUDE.md: metrics row '| Versiya | X.Y.Z |' not found (skipped).")
        return
    if found[:2] != baseline[:2]:
        warnings.append(
            f"CLAUDE.md: metrics table shows {found[0]}.{found[1]}.{found[2]}, "
            f"latest tag is v{baseline[0]}.{baseline[1]}.0. Update it at the next release."
        )


def _check_versions_md(baseline: tuple[int, int, int], warnings: list[str]) -> None:
    """docs/versions.md: таблица эволюции имеет колонку последнего релиза (мягко).

    Таблица «Эволюция версий» — про качественные скачки, свободный формат;
    поэтому warning, а не error (владелец может решить не выделять релиз в
    отдельную колонку). Закрывает слепую зону аудита #381: без этой проверки
    канон уже отставал — таблица кончалась на v1.7.0 при git-теге v1.8.0.
    """
    tag = f"v{baseline[0]}.{baseline[1]}.0"
    if tag not in _VERSIONS.read_text(encoding="utf-8"):
        warnings.append(
            f"docs/versions.md: evolution table has no column for the latest "
            f"release {tag}. Add a {tag} column at the next MINOR release."
        )


def main() -> int:
    """Вернуть 0, если дрейфа версий нет; 1 — если найден."""
    baseline = _latest_tag_baseline()

    errors: list[str] = []
    warnings: list[str] = []

    # (1) pyproject проверяется всегда — не зависит от git-тегов.
    _check_pyproject_dynamic(errors)

    if baseline is None:
        print("SKIP: no git tag vX.Y.0 found (no git/history) - baseline comparison skipped.")
    else:
        print(f"Release baseline (latest git tag): v{baseline[0]}.{baseline[1]}.{baseline[2]}")
        _check_checkpoint(baseline, errors)
        _check_changelog(baseline, errors)
        _check_claude_metrics(baseline, warnings)
        _check_versions_md(baseline, warnings)

    for w in warnings:
        print(f"  WARNING: {w}")

    if errors:
        print("\nFAIL: version drift detected:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: versions consistent - no static source-of-truth, docs match the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
