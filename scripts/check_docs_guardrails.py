#!/usr/bin/env python3
"""scripts/check_docs_guardrails.py — CI-guard документации (issue #173).

Две машинные защиты, чтобы README снова не разросся, а ссылки между Markdown-
файлами не протухли (эпик #167 «README как витрина»):

1. **README line-budget.** ``README.md`` не должен превышать ``README_LINE_BUDGET``
   строк (см. константу ниже). README — короткая витрина, подробности живут в
   ``docs/`` (см. CONTRIBUTING.md §«Документация: README как витрина»).
2. **Markdown relative link-check.** Локальные ссылки ``[текст](путь)`` и
   ``[текст](путь#якорь)`` между README ↔ docs ↔ корневыми ``*.md`` должны вести
   на существующий файл, а якоря — на существующий заголовок в целевом
   Markdown-файле. Внешние ссылки (http/https/mailto и т.п.) осознанно НЕ
   проверяются — сетевые проверки делают CI флаки.

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
    "README_LINE_BUDGET",
    "check_readme_budget",
    "check_markdown_links",
    "collect_markdown_files",
    "github_slug",
    "main",
]

_ROOT = Path(__file__).resolve().parent.parent

# Лимит строк README (issue #173). Держим в синхроне с CONTRIBUTING.md
# §«Документация: README как витрина» — при изменении править оба места.
README_LINE_BUDGET = 220

# [текст](target) — не изображение (нет ведущего "!"), target без пробелов/скобок.
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
# Заголовки ATX: "# ...", "## ..." и т.д.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Внешние схемы, которые не проверяем (сеть/почта/якоря протоколов).
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#?mailto)", re.IGNORECASE)


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


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_readme_budget(errors)
    check_markdown_links(errors)

    if errors:
        print("\nFAIL: documentation guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: README within budget and all local Markdown links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
