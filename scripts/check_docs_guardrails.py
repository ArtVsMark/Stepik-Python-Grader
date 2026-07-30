#!/usr/bin/env python3
"""scripts/check_docs_guardrails.py — CI-guard документации (issue #173).

Пять машинных защит, чтобы README снова не разросся, ссылки между Markdown-
файлами не протухли, документация не расползлась мимо направлений, индексы не
отставали от состава каталогов, а объясняющие документы не превратились в журнал
работ (эпик #167 «README как витрина»):

1. **README line-budget.** ``README.md`` не должен превышать ``README_LINE_BUDGET``
   строк (см. константу ниже). README — короткая витрина, подробности живут в
   ``docs/`` (см. CONTRIBUTING.md §«Документация: README как витрина»).
2. **Markdown relative link-check.** Локальные ссылки ``[текст](путь)`` и
   ``[текст](путь#якорь)`` между README ↔ docs ↔ корневыми ``*.md`` должны вести
   на существующий файл, а якоря — на существующий заголовок в целевом
   Markdown-файле. Внешние ссылки (http/https/mailto и т.п.) осознанно НЕ
   проверяются — сетевые проверки делают CI флаки.
3. **Docs directions.** В корне ``docs/`` лежит только ``README.md``-развилка;
   документы живут в направлениях по читателю — ``use/`` (как пользоваться),
   ``dev/`` (как устроено, с ``dev/design/`` для спроектированного без кода),
   ``agent/`` (служебное для Claude Code), ``audit/`` (находки незакрытых
   аудитов), ``archive/`` (история). Новый ``.md`` в корне ``docs/`` — ошибка:
   он не попадает ни в одно направление.
4. **Docs index completeness (issue #300/#562).** Каждый файл ``<dir>/*.md``
   (кроме самого ``<dir>/README.md``) должен быть упомянут в ``<dir>/README.md``
   — иначе индекс расходится с фактическим составом каталога (как произошло с
   ``changelog-archive.md``). **Рекурсивно (issue #562):** проверка применяется
   к ``docs/`` и к КАЖДОМУ подкаталогу с собственным ``README.md``-индексом
   (``docs/dev/adr/`` → ``docs/dev/adr/README.md``, ``docs/archive/`` →
   ``docs/archive/README.md``). Подкаталог без своего ``README.md`` отдельно не
   индексируется — родитель ссылается на него одной строкой (``role-*.md``-
   приложения к сводному аудиту, ADR-набор до появления adr/README.md).
5. **Issue-tail policy.** Объясняющий документ отвечает «как это работает
   сейчас», поэтому ссылок на задачи в нём быть не должно: ``docs/use/``,
   ``docs/dev/*.md``, README, SECURITY и CONTRIBUTING держат ноль.
   Логи (``CHANGELOG.md``, ``docs/archive/``), находки (``docs/audit/``) и ADR
   не проверяются вовсе — там номер уместен. ``docs/dev/design/`` и агентские
   документы живут по бюджету: номер там работает как идентификатор
   согласованного требования, а не как датировка.

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
    "check_docs_directions",
    "check_docs_index_completeness",
    "check_issue_tail_policy",
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

# Направления документации: docs/ разложена по читателю, и в корне docs/ лежит
# только README.md-развилка. Синхронизировать с docs/README.md и CONTRIBUTING.md
# §«Документация: README как витрина».
_DOCS_DIRECTIONS = ("use", "dev", "agent", "audit", "archive")

# Ссылка на задачу/PR: "#123", "#157.4". Ловим только цифровые — "#заголовок"
# (внутренний якорь) и "#!/usr/bin" не матчатся.
_ISSUE_TAIL_RE = re.compile(r"#\d+(?:\.\d+)?")

# Бюджеты issue-ссылок для зон, где номер допустим как идентификатор, а не как
# журнал. docs/dev/design/ держит #156 (контракт /api/v1/*) и реестр
# #157.1-#157.6 (требования к sandbox, ADR-0008 ссылается поштучно); CLAUDE.md и
# docs/agent/ — указатели вроде roadmap-issue. Снижать при чистке, не повышать:
# рост бюджета означает, что журнал снова пополз в объясняющий текст.
_DESIGN_TAIL_BUDGET = 22
_AGENT_TAIL_BUDGET = 6

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
    """Каждый ``<dir>/*.md`` упомянут в ``<dir>/README.md`` — для ``docs/`` и
    каждого подкаталога с собственным ``README.md``-индексом (issue #300/#562).

    Рекурсивно (issue #562): проверяются ``docs/`` (``docs/README.md``),
    ``docs/dev/adr/`` (``docs/dev/adr/README.md``), ``docs/archive/``
    (``docs/archive/README.md``) и т.д. Подкаталог БЕЗ собственного ``README.md``
    отдельно не индексируется — его файлы каталогизируются родительским индексом
    одной строкой (как ``role-*.md``-приложения к сводному аудиту).
    """
    docs_root = _ROOT / "docs"
    index_dirs = [docs_root] + sorted(
        p for p in docs_root.rglob("*") if p.is_dir() and (p / "README.md").is_file()
    )
    checked = 0
    for d in index_dirs:
        index_text = (d / "README.md").read_text(encoding="utf-8")
        idx_rel = (d / "README.md").relative_to(_ROOT)
        for md in sorted(d.glob("*.md")):
            if md.name == "README.md":
                continue
            checked += 1
            if md.name not in index_text:
                errors.append(
                    f"{idx_rel}: '{md.name}' exists in {d.relative_to(_ROOT)}/ but is not "
                    "referenced in the navigation index (issue #300/#562)."
                )
    print(f"docs/ index: checked {checked} file(s) against per-directory README indexes.")


def check_changelog_version_budget(errors: list[str]) -> None:
    """CHANGELOG.md держит не более ``CHANGELOG_MAX_VERSIONS`` версионных релизов.

    Считаются заголовки вида ``## [X.Y.Z] - ДАТА`` (issue #373). ``[Unreleased]``
    и до-версионные ``## [unreleased] / <дата>`` из архива не в счёт. Перебор —
    сигнал ротировать самую старую версию в ``docs/archive/changelog-archive.md``.
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


def check_docs_directions(errors: list[str]) -> None:
    """В корне ``docs/`` живёт только ``README.md`` — остальное по направлениям.

    Документация разложена на четыре направления по читателю: ``use/`` (как
    пользоваться), ``dev/`` (как устроено, включая ``dev/design/`` — то, что
    спроектировано без кода), ``agent/`` (служебное для Claude Code) и
    ``archive/`` (всё историческое). Новый ``.md``, положенный прямо в корень
    ``docs/``, ломает эту навигацию: именно так корень дорос до 23 файлов, где
    инструкция по установке лежала рядом с дизайном seccomp-профиля.
    """
    docs_root = _ROOT / "docs"
    stray = sorted(p.name for p in docs_root.glob("*.md") if p.name != "README.md")
    if stray:
        errors.append(
            f"docs/: {', '.join(stray)} lie in the docs/ root. Documentation is "
            "split by reader - put the file into docs/use/ (how to use), "
            "docs/dev/ (how it works), docs/agent/ (Claude-only), docs/audit/ "
            "(open audit findings) or docs/archive/ (history), and index it in "
            "that direction's README.md."
        )
        return

    missing = [d for d in _DOCS_DIRECTIONS if not (docs_root / d / "README.md").is_file()]
    if missing:
        errors.append(
            "docs/: missing direction index README.md in "
            + ", ".join(f"docs/{d}/" for d in missing)
        )
        return

    print(
        "docs/ directions: root holds README.md only; "
        + ", ".join(f"{d}/" for d in _DOCS_DIRECTIONS)
        + " indexed."
    )


def check_issue_tail_policy(errors: list[str]) -> None:
    """В объясняющих документах нет журнала работ — ссылок вида ``#NNN``.

    Документ, который отвечает «как это работает сейчас», не должен сообщать, в
    каком issue/эпике это появилось: читателю номер не нужен, а справочник
    превращается в лог. «Что сделано» живёт в ``CHANGELOG.md``, «как шло» — в
    ``docs/archive/``, «что предстоит» — в GitHub Issues.

    Зоны, где номер УМЕСТЕН и потому не проверяются:

    * ``CHANGELOG.md`` и ``docs/archive/`` — это и есть логи;
    * ``docs/audit/`` — находки аудита привязаны к задачам по определению;
    * ``docs/dev/adr/`` — ADR отвечает «почему решили так», задача часть ответа;
    * ``docs/dev/design/`` — там номер работает как идентификатор согласованного
      требования (``#156`` — контракт ``/api/v1/*``, ``#157.1``–``#157.6`` —
      требования к sandbox, на которые ADR-0008 ссылается поштучно). Лимит
      ``_DESIGN_TAIL_BUDGET`` не даёт этой зоне снова обрасти журналом;
    * ``CLAUDE.md`` и ``docs/agent/`` — агентский контракт; лимит
      ``_AGENT_TAIL_BUDGET`` оставляет место указателям вроде roadmap-issue.

    Всё остальное (``docs/use/``, ``docs/dev/*.md``, ``README``, ``SECURITY``,
    ``CONTRIBUTING``) должно держать ноль.
    """
    free_zones = ("CHANGELOG.md", "docs/archive/", "docs/audit/", "docs/dev/adr/")
    budgeted = {
        "docs/dev/design/": _DESIGN_TAIL_BUDGET,
        "CLAUDE.md": _AGENT_TAIL_BUDGET,
        "docs/agent/": _AGENT_TAIL_BUDGET,
    }

    zone_totals: dict[str, int] = {}
    checked = 0
    for md in collect_markdown_files():
        rel = md.relative_to(_ROOT).as_posix()
        if any(rel == z or rel.startswith(z) for z in free_zones):
            continue
        tails = _ISSUE_TAIL_RE.findall(md.read_text(encoding="utf-8"))
        zone = next((z for z in budgeted if rel == z or rel.startswith(z)), None)
        if zone is not None:
            zone_totals[zone] = zone_totals.get(zone, 0) + len(tails)
            continue
        checked += 1
        if tails:
            errors.append(
                f"{md.relative_to(_ROOT)}: {len(tails)} issue reference(s) "
                f"({', '.join(sorted(set(tails))[:5])}) in an explanatory document. "
                "Such a document answers 'how it works now' - move the work log to "
                "CHANGELOG.md, history to docs/archive/, plans to GitHub Issues."
            )

    for zone, budget in budgeted.items():
        found = zone_totals.get(zone, 0)
        if found > budget:
            errors.append(
                f"{zone}: {found} issue reference(s) exceed the budget of {budget}. "
                "Only stable requirement/contract identifiers belong here - the rest "
                "is a work log."
            )
    print(
        f"issue-tail policy: {checked} explanatory file(s) at zero; "
        + ", ".join(f"{z} {zone_totals.get(z, 0)}/{b}" for z, b in budgeted.items())
    )


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_readme_budget(errors)
    check_markdown_links(errors)
    check_docs_directions(errors)
    check_docs_index_completeness(errors)
    check_changelog_version_budget(errors)
    check_issue_tail_policy(errors)

    if errors:
        print("\nFAIL: documentation guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(
        "OK: README within budget, all local Markdown links resolve, docs/ split "
        "by direction, indexes complete."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
