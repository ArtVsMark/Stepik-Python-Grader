#!/usr/bin/env python3
"""scripts/check_version_consistency.py — CI-guard против дрейфа версий (issue #165).

После перехода на динамическую версию из git-тегов (issue #162) единственный
источник истины — git-тег ``vX.Y.0``. Статической строки ``version`` в
``pyproject.toml`` больше нет. Этот скрипт ловит две категории регрессов:

1. **Возврат статического источника истины.** ``[project]`` в ``pyproject.toml``
   не должен снова объявлять ``version = "..."`` — только ``dynamic = ["version"]``.
2. **Дрейф "текущей версии" в документации.** ``CHECKPOINT.md`` (и, мягко,
   таблица метрик ``CLAUDE.md``) и верхняя запись ``CHANGELOG.md`` должны
   соответствовать актуальному релизному baseline — последнему git-тегу ``vX.Y.0``.

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
            "pyproject.toml: [project].version объявлена статически "
            f"({project['version']!r}); ожидалась dynamic-версия из git-тегов "
            '(issue #162). Уберите строку version и оставьте dynamic = ["version"].'
        )
    if "version" not in project.get("dynamic", []):
        errors.append(
            'pyproject.toml: [project].dynamic не содержит "version"; '
            "динамическая версия из git-тегов не подключена (issue #162)."
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
            "CHECKPOINT.md: не найдена строка 'Текущая версия: X.Y.Z' "
            "(обязательный маркер для сверки с релизным baseline)."
        )
        return
    if found[:2] != baseline[:2]:
        errors.append(
            f"CHECKPOINT.md: 'Текущая версия: {found[0]}.{found[1]}.{found[2]}' "
            f"расходится с последним релизным тегом v{baseline[0]}.{baseline[1]}.0 "
            f"(ожидался MAJOR.MINOR = {baseline[0]}.{baseline[1]})."
        )


def _check_changelog(baseline: tuple[int, int, int], errors: list[str]) -> None:
    """CHANGELOG.md: верхняя запись '## [X.Y.Z]' == baseline (X.Y.0)."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    # Первая запись вида '## [X.Y.Z]' — пропускаем '## [Unreleased]'.
    found = _find_first(r"##\s*\[\d+\.\d+\.\d+\]", text)
    if found is None:
        errors.append("CHANGELOG.md: не найдена запись вида '## [X.Y.Z]'.")
        return
    if found != baseline:
        errors.append(
            f"CHANGELOG.md: верхняя релизная запись [{found[0]}.{found[1]}.{found[2]}] "
            f"не совпадает с последним git-тегом v{baseline[0]}.{baseline[1]}.{baseline[2]}."
        )


def _check_claude_metrics(baseline: tuple[int, int, int], warnings: list[str]) -> None:
    """CLAUDE.md таблица метрик '| Версия | X.Y.Z' — MAJOR.MINOR == baseline (мягко)."""
    text = _CLAUDE.read_text(encoding="utf-8")
    found = _find_first(r"\|\s*Версия\s*\|\s*\d+\.\d+\.\d+", text)
    if found is None:
        warnings.append("CLAUDE.md: строка '| Версия | X.Y.Z |' не найдена (пропуск).")
        return
    if found[:2] != baseline[:2]:
        warnings.append(
            f"CLAUDE.md: таблица метрик показывает {found[0]}.{found[1]}.{found[2]}, "
            f"последний тег — v{baseline[0]}.{baseline[1]}.0. Обновите при следующем релизе."
        )


def main() -> int:
    """Вернуть 0, если дрейфа версий нет; 1 — если найден."""
    baseline = _latest_tag_baseline()

    errors: list[str] = []
    warnings: list[str] = []

    # (1) pyproject проверяется всегда — не зависит от git-тегов.
    _check_pyproject_dynamic(errors)

    if baseline is None:
        print("SKIP: git-тег vX.Y.0 не найден (нет git/истории) — сверка с baseline пропущена.")
    else:
        print(f"Релизный baseline (последний git-тег): v{baseline[0]}.{baseline[1]}.{baseline[2]}")
        _check_checkpoint(baseline, errors)
        _check_changelog(baseline, errors)
        _check_claude_metrics(baseline, warnings)

    for w in warnings:
        print(f"  ⚠️  {w}")

    if errors:
        print("\n❌ Обнаружен дрейф версий:")
        for e in errors:
            print(f"  • {e}")
        return 1

    print("✅ Версии согласованы: статического source-of-truth нет, доки совпадают с baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
