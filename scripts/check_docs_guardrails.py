#!/usr/bin/env python3
"""scripts/check_docs_guardrails.py — CI-guard документации (issue #173).

Три машинные защиты, чтобы README снова не разросся, ссылки между Markdown-
файлами не протухли, а индекс docs/ не отставал от фактического состава
каталога (эпик #167 «README как витрина»):

1. **README line-budget.** ``README.md`` не должен превышать ``README_LINE_BUDGET``
   строк (см. константу ниже). README — короткая витрина, подробности живут в
   ``docs/`` (см. CONTRIBUTING.md §«Документация: README как витрина»).
2. **Markdown relative link-check.** Локальные ссылки ``[текст](путь)`` и
   ``[текст](путь#якорь)`` между README ↔ docs ↔ корневыми ``*.md`` должны вести
   на существующий файл, а якоря — на существующий заголовок в целевом
   Markdown-файле. Внешние ссылки (http/https/mailto и т.п.) осознанно НЕ
   проверяются — сетевые проверки делают CI флаки.
3. **Docs index completeness (issue #300).** Каждый файл ``docs/*.md`` (кроме
   самого ``docs/README.md``) должен быть упомянут в ``docs/README.md`` — иначе
   индекс расходится с фактическим составом каталога (как произошло с
   ``changelog-archive.md``). Не рекурсивно: ``docs/adr/*.md`` каталогизируются
   собственным индексом ``docs/adr/README.md``, на который ``docs/README.md``
   уже ссылается одной строкой — перечислять каждый ADR отдельно не требуется.

Никаких внешних зависимостей: чистый ``re`` + ``pathlib``, детерминированно и
кроссплатформенно (Windows/Linux/macOS).

Запуск::

    python scripts/check_docs_guardrails.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

__all__ = [
    "CHANGELOG_MAX_VERSIONS",
    "README_LINE_BUDGET",
    "check_changelog_version_budget",
    "check_docs_index_completeness",
    "check_markdown_links",
    "check_readme_budget",
    "collect_markdown_files",
    "github_slug",
    "main",
]

_ROOT = Path(__file__).resolve().parent.parent

# Лимит строк README (issue #173). Держим в синхроне с CONTRIBUTING.md
# §«Документация: README как витрина» — при изменении править оба места.
README_LINE_BUDGET = 220

# Лимит числа версионных заголовков `## [X.Y.0]` в живом CHANGELOG.md (issue
# #373). Держим только [Unreleased] + три последних MINOR; более старые релизы
# ротируются в docs/changelog-archive.md. Синхронизировать с CLAUDE.md
# §«Обновление CHANGELOG.md» и CONTRIBUTING.md §«Версионирование».
CHANGELOG_MAX_VERSIONS = 3

# [текст](target) — не изображение (нет ведущего "!"), target без пробелов/скобок.
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
# Заголовки ATX: "# ...", "## ..." и т.д.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Внешние схемы, которые не проверяем (сеть/почта/якоря протоколов).
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#?mailto)", re.IGNORECASE)
# Версионный заголовок релиза в CHANGELOG.md: "## [1.8.0] - ДАТА" (issue #373).
# "[Unreleased]" и до-версионные "## [unreleased] / <дата>" не матчатся.
_CHANGELOG_VERSION_RE = re.compile(r"^## \[\d+\.\d+\.\d+\]")


def collect_markdown_files() -> list[Path]:
    """Все Markdown-файлы под контролем: корневые ``*.md`` + ``docs/**/*.md``."""
    files = sorted(_ROOT.glob("*.md"))
    files += sorted(p for p in (_ROOT / "docs").rglob("*.md"))
    return files


def github_slug(heading: str) -> str:
    """Slug якоря в стиле GitHub: lowercase, выкидываем пунктуацию, пробел→дефис.

    Юникод-буквы (в т.ч. кириллица) сохраняются — как это делает GitHub.
    """
    text = heading.strip().lower()
    # GitHub-anchor: оставить буквы/цифры/пробелы/дефис/подчёркивание; всё
    # остальное (пунктуация, символы вроде §, (, ), `, *, точки, эм-дэш) — прочь.
    # Подчёркивание GitHub сохраняет (напр. `stepik_config`), пробел → дефис.
    text = "".join(ch for ch in text if ch.isalnum() or ch in " -_")
    return text.replace(" ", "-")


def _heading_slugs(path: Path) -> set[str]:
    """Множество slug-якорей всех заголовков файла (с дублями -1, -2, ...)."""
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        base = github_slug(m.group(2))
        n = counts.get(base, 0)
        slugs.add(base if n == 0 else f"{base}-{n}")
        counts[base] = n + 1
    return slugs


def check_readme_budget(errors: list[str]) -> None:
    """README.md не должен превышать README_LINE_BUDGET строк."""
    readme = _ROOT / "README.md"
    line_count = len(readme.read_text(encoding="utf-8").splitlines())
    if line_count > README_LINE_BUDGET:
        errors.append(
            f"README.md: {line_count} lines exceed the budget of {README_LINE_BUDGET} "
            "(issue #173). Move detailed sections into docs/ and link to them "
            "(see CONTRIBUTING.md 'README as a showcase')."
        )
    else:
        print(f"README.md: {line_count}/{README_LINE_BUDGET} lines (within budget).")


def check_markdown_links(errors: list[str]) -> None:
    """Локальные ссылки между Markdown-файлами ведут на существующие файлы/якоря."""
    files = collect_markdown_files()
    slug_cache: dict[Path, set[str]] = {}
    checked = 0

    for md in files:
        text = md.read_text(encoding="utf-8")
        for match in _LINK_RE.finditer(text):
            target = match.group(1)
            if _EXTERNAL_RE.match(target):
                continue

            path_part, _, anchor = target.partition("#")
            rel = md.parent if not path_part else (md.parent / path_part)
            dest = rel.resolve()
            checked += 1

            if path_part:
                if not dest.exists():
                    errors.append(
                        f"{md.relative_to(_ROOT)}: broken link -> '{target}' "
                        f"(file not found: {path_part})."
                    )
                    continue
                anchor_target: Path | None = dest if dest.suffix == ".md" else None
            else:
                # Ссылка вида '#якорь' — якорь в этом же файле.
                anchor_target = md

            if anchor and anchor_target is not None:
                if anchor_target not in slug_cache:
                    slug_cache[anchor_target] = _heading_slugs(anchor_target)
                if anchor.lower() not in slug_cache[anchor_target]:
                    errors.append(
                        f"{md.relative_to(_ROOT)}: broken anchor -> '{target}' "
                        f"(no heading '#{anchor}' in {anchor_target.relative_to(_ROOT)})."
                    )

    print(f"Markdown links: checked {checked} local link(s) across {len(files)} file(s).")


def check_docs_index_completeness(errors: list[str]) -> None:
    """Каждый docs/*.md (кроме docs/README.md) упомянут в docs/README.md.

    Не рекурсивно — docs/adr/*.md каталогизируются собственным индексом
    (docs/adr/README.md), см. докстринг модуля, пункт 3.
    """
    readme = _ROOT / "docs" / "README.md"
    index_text = readme.read_text(encoding="utf-8")
    checked = 0
    for md in sorted((_ROOT / "docs").glob("*.md")):
        if md.name == "README.md":
            continue
        checked += 1
        if md.name not in index_text:
            errors.append(
                f"docs/README.md: '{md.name}' exists in docs/ but is not referenced "
                "in the navigation index (issue #300)."
            )
    print(f"docs/ index: checked {checked} file(s) against docs/README.md.")


def check_changelog_version_budget(errors: list[str]) -> None:
    """CHANGELOG.md держит не более ``CHANGELOG_MAX_VERSIONS`` версионных релизов.

    Считаются заголовки вида ``## [X.Y.Z] - ДАТА`` (issue #373). ``[Unreleased]``
    и до-версионные ``## [unreleased] / <дата>`` из архива не в счёт. Перебор —
    сигнал ротировать самую старую версию в ``docs/changelog-archive.md``.
    """
    changelog = _ROOT / "CHANGELOG.md"
    versions = [
        ln
        for ln in changelog.read_text(encoding="utf-8").splitlines()
        if _CHANGELOG_VERSION_RE.match(ln)
    ]
    if len(versions) > CHANGELOG_MAX_VERSIONS:
        errors.append(
            f"CHANGELOG.md: {len(versions)} versioned releases exceed the budget "
            f"of {CHANGELOG_MAX_VERSIONS} (issue #373). Rotate the oldest into "
            "docs/changelog-archive.md (keep [Unreleased] + the newest "
            f"{CHANGELOG_MAX_VERSIONS} MINOR releases)."
        )
    else:
        print(
            f"CHANGELOG.md: {len(versions)}/{CHANGELOG_MAX_VERSIONS} versioned "
            "release(s) (within budget)."
        )


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_readme_budget(errors)
    check_markdown_links(errors)
    check_docs_index_completeness(errors)
    check_changelog_version_budget(errors)

    if errors:
        print("\nFAIL: documentation guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: README within budget, all local Markdown links resolve, docs/ index complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
