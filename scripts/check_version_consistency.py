#!/usr/bin/env python3
"""scripts/check_version_consistency.py — CI-guard против дрейфа версий (issue #165).

После перехода на динамическую версию из git-тегов (issue #162) единственный
источник истины — git-тег ``vX.Y.0``. Статической строки ``version`` в
``pyproject.toml`` больше нет. Этот скрипт ловит две категории регрессов:

1. **Возврат статического источника истины.** ``[project]`` в ``pyproject.toml``
   не должен снова объявлять ``version = "..."`` — только ``dynamic = ["version"]``.
2. **Дрейф "текущей версии" в документации.** Верхняя запись ``CHANGELOG.md``
   (и, мягко, таблица метрик ``CLAUDE.md`` с таблицей эволюции метрик
   ``HISTORY.md``) должна соответствовать актуальному релизному
   baseline — последнему git-тегу ``vX.Y.0``. Исключение — **готовящийся
   релиз**: верхняя запись ровно на один MINOR впереди тега допускается, потому
   что релизный PR переименовывает ``[Unreleased]`` в ``[X.Y+1.0]`` до того, как
   тег ляжет на его merge-коммит (``_is_release_in_flight``).

Baseline вычисляется из git (``git describe --tags --abbrev=0``). Сравнение
``CLAUDE.md`` ведётся только по ``MAJOR.MINOR`` — PATCH в схеме проекта это
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

import contextlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_CHANGELOG = _ROOT / "CHANGELOG.md"
_CLAUDE = _ROOT / "CLAUDE.md"
_HISTORY = _ROOT / "HISTORY.md"

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
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    m = _SEMVERISH.search(out)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _baseline_is_required() -> bool:
    """Обязан ли baseline быть доступен в этом окружении (issue #988, REV-2-01).

    Прежде отсутствие baseline всегда давало ``SKIP`` и ``exit 0``: любая ошибка
    git — сломанный репозиторий, урезанный чекаут, отсутствующие теги — гасила
    гейт целиком, а следом печаталось ``OK: versions consistent``. Гейт зеленел
    именно тогда, когда проверить ничего не удалось.

    Различать причины по коду выхода ``git describe`` нельзя — «тегов нет» и
    «репозиторий недоступен» дают одинаковый ``CalledProcessError``. Зато точно
    известно окружение, где baseline обязан быть: CI выкачивает историю с
    ``fetch-depth: 0``, поэтому там ``SKIP`` означает поломку, а не сборку из
    tarball. Признак — стандартная переменная ``CI``; локально поведение
    прежнее, чтобы сборка из sdist без истории не падала.
    """
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


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


def _is_release_in_flight(found: tuple[int, int, int], baseline: tuple[int, int, int]) -> bool:
    """Верхняя запись — это ГОТОВЯЩИЙСЯ релиз (ровно следующий MINOR), а не дрейф.

    Релизный PR по определению переименовывает ``[Unreleased]`` в ``[X.Y+1.0]``
    раньше, чем появляется тег: тег ставится на merge-коммит уже смерженного PR
    (так лежат все теги проекта). Без этого допуска гейт валил КАЖДЫЙ релизный
    PR — причём до шага «Run tests», из-за чего тесты в нём не запускались вовсе,
    и красный CI приходилось объяснять руками на каждом выпуске.

    Допуск узкий: ровно ``MINOR + 1`` при том же MAJOR и ``PATCH == 0``. Отставший
    CHANGELOG, прыжок через версию и смена MAJOR остаются ошибкой — то есть
    исходный смысл гейта (ловить дрейф документации) не ослаблен.
    """
    major, minor, patch = found
    return (major, minor, patch) == (baseline[0], baseline[1] + 1, 0)


def _check_changelog(baseline: tuple[int, int, int], errors: list[str]) -> None:
    """CHANGELOG.md: верхняя запись '## [X.Y.Z]' == baseline (X.Y.0) либо следующий MINOR."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    # Первая запись вида '## [X.Y.Z]' — пропускаем '## [Unreleased]'.
    found = _find_first(r"##\s*\[\d+\.\d+\.\d+\]", text)
    if found is None:
        errors.append("CHANGELOG.md: no entry of the form '## [X.Y.Z]' found.")
        return
    if found == baseline:
        return
    if _is_release_in_flight(found, baseline):
        print(
            f"  NOTE: CHANGELOG.md top entry [{found[0]}.{found[1]}.{found[2]}] is one MINOR "
            f"ahead of tag v{baseline[0]}.{baseline[1]}.{baseline[2]} - release in flight, "
            "the tag lands on the merge commit."
        )
        return
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
    # Готовящийся релиз: строка версии обновляется тем же PR, что и CHANGELOG,
    # то есть тоже опережает тег ровно на один MINOR — это не дрейф.
    if _is_release_in_flight((found[0], found[1], 0), baseline):
        return
    if found[:2] != baseline[:2]:
        warnings.append(
            f"CLAUDE.md: metrics table shows {found[0]}.{found[1]}.{found[2]}, "
            f"latest tag is v{baseline[0]}.{baseline[1]}.0. Update it at the next release."
        )


def _check_history_md(baseline: tuple[int, int, int], warnings: list[str]) -> None:
    """HISTORY.md: у последнего релиза есть СТРОКА в таблице эволюции метрик (мягко).

    Warning, а не error: числа в таблице — снимок на момент релиза, и владелец
    подставляет их при постановке тега, а не в релизном PR. Закрывает слепую
    зону аудита #381: без этой проверки канон уже отставал — таблица кончалась
    на v1.7.0 при git-теге v1.8.0.

    Ищется именно строка таблицы (``| v1.10.0 |``), а не вхождение версии где
    угодно: в записи о релизе тег упомянут заголовком, и проверка «есть ли
    подстрока» проходила бы на пустой таблице (issue #1181 — документ переехал
    из ``docs/use/versions.md``, где релизы шли колонками).
    """
    tag = f"v{baseline[0]}.{baseline[1]}.0"
    text = _HISTORY.read_text(encoding="utf-8")
    if not re.search(rf"^\|\s*{re.escape(tag)}\s*\|", text, re.MULTILINE):
        warnings.append(
            f"HISTORY.md: metrics table has no row for the latest release {tag}. "
            f"Add a {tag} row at the next MINOR release."
        )


def main() -> int:
    """Вернуть 0, если дрейфа версий нет; 1 — если найден."""
    baseline = _latest_tag_baseline()

    errors: list[str] = []
    warnings: list[str] = []

    # (1) pyproject проверяется всегда — не зависит от git-тегов.
    _check_pyproject_dynamic(errors)

    if baseline is None:
        if _baseline_is_required():
            errors.append(
                "no git tag vX.Y.0 found, but CI=1: history and tags must be available here "
                "(actions/checkout with fetch-depth: 0). Without a baseline this gate checks "
                "nothing but pyproject - it must fail, not print OK (issue #988)."
            )
        else:
            print("SKIP: no git tag vX.Y.0 found (no git/history) - baseline comparison skipped.")
    else:
        print(f"Release baseline (latest git tag): v{baseline[0]}.{baseline[1]}.{baseline[2]}")
        _check_changelog(baseline, errors)
        _check_claude_metrics(baseline, warnings)
        _check_history_md(baseline, warnings)

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
