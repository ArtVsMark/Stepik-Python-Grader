#!/usr/bin/env python3
"""scripts/check_docs_guardrails.py — CI-guard документации (issue #173).

Десять машинных защит, чтобы README снова не разросся, ссылки между Markdown-
файлами не протухли ни целью, ни подписью, документация не расползлась мимо направлений, индексы не
отставали от состава каталогов, а объясняющие документы и пользовательские
строки интерфейса не превратились в журнал работ (эпик #167 «README как
витрина»):

1. **README line-budget.** ``README.md`` не должен превышать ``README_LINE_BUDGET``
   строк (см. константу ниже). README — короткая витрина, подробности живут в
   ``docs/`` (см. CONTRIBUTING.md §«Документация: README как витрина»).
2. **Markdown relative link-check.** Локальные ссылки ``[текст](путь)`` и
   ``[текст](путь#якорь)`` между README ↔ docs ↔ корневыми ``*.md`` должны вести
   на существующий файл, а якоря — на существующий заголовок в целевом
   Markdown-файле. Внешние ссылки (http/https/mailto и т.п.) осознанно НЕ
   проверяются — сетевые проверки делают CI флаки.
3. **Link captions (issue #827).** Если подпись ссылки выглядит как путь
   (``[docs/use/configuration.md § …](…)``), она обязана быть «хвостом»
   фактической цели. Раскладка ``docs/`` по направлениям пережила девять таких
   подписей: цель рабочая, а путь в тексте ведёт в никуда — и читатель копирует
   именно его.
4. **Showcase metrics (issue #829).** В `README.md`/`README.en.md` нет метрик
   числом: живой источник числа тестов, покрытия и размера глоссария — бейджи в
   шапке. Вписанное руками «2100+ tests» пережило рост набора на четверть и
   осталось в английской версии, когда из русской его уже убрали.
5. **Docs directions.** В корне ``docs/`` лежит только ``README.md``-развилка;
   документы живут в направлениях по читателю — ``use/`` (как пользоваться),
   ``dev/`` (как устроено, с ``dev/design/`` для спроектированного без кода),
   ``agent/`` (служебное для Claude Code), ``audit/`` (находки незакрытых
   аудитов), ``archive/`` (история). Новый ``.md`` в корне ``docs/`` — ошибка:
   он не попадает ни в одно направление.
6. **Docs index completeness (issue #300/#562).** Каждый файл ``<dir>/*.md``
   (кроме самого ``<dir>/README.md``) должен быть упомянут в ``<dir>/README.md``
   — иначе индекс расходится с фактическим составом каталога (как произошло с
   ``changelog-archive.md``). **Рекурсивно (issue #562):** проверка применяется
   к ``docs/`` и к КАЖДОМУ подкаталогу с собственным ``README.md``-индексом
   (``docs/dev/adr/`` → ``docs/dev/adr/README.md``, ``docs/archive/`` →
   ``docs/archive/README.md``). Подкаталог без своего ``README.md`` отдельно не
   индексируется — родитель ссылается на него одной строкой (``role-*.md``-
   приложения к сводному аудиту, ADR-набор до появления adr/README.md).
7. **Issue-tail policy.** Объясняющий документ отвечает «как это работает
   сейчас», поэтому ссылок на задачи в нём быть не должно: ``docs/use/``,
   ``docs/dev/*.md``, README, SECURITY и CONTRIBUTING держат ноль.
   Логи (``CHANGELOG.md``, ``docs/archive/``), находки (``docs/audit/``) и ADR
   не проверяются вовсе — там номер уместен. ``docs/dev/design/`` и агентские
   документы живут по бюджету: номер там работает как идентификатор
   согласованного требования, а не как датировка.
8. **PyPI readme (issue #832).** В файле, объявленном ``readme`` в
   ``pyproject.toml``, нет относительных ссылок и картинок: его содержимое
   уезжает в ``long_description`` дистрибутива как есть, а PyPI относительные
   пути резолвит к ``pypi.org``. Чтобы абсолютные адреса не вывели README
   из-под защит 2 и 3, ссылки на свой репозиторий разворачиваются обратно в
   путь (``_as_repo_path``).
9. **UI-strings issue policy (issue #820).** Та же политика — для строк, которые
   пользователь видит в интерфейсе, а не в документации: ``help=`` в
   ``cli/options.py`` (вывод ``--help``) и значения ``core/locales/*.json``.
   Гейт на доках держал ноль, а самая читаемая поверхность — справка — годами
   печатала «Issue #51 D-01» и «Эпик #80 Tier 1»: выписка из трекера вместо
   объяснения флага.
10. **Merge conflict markers (#1164).** В Markdown нет маркеров незаконченного
   слияния. Единственная поломка, которую проходили все девять защит выше
   разом: ссылки резолвятся, бюджет соблюдён, индексы полны — а раздел про
   лаунчер читатель видит дважды, вперемешку с ``HEAD`` и ``origin/main``. В
   ``.py`` то же самое ловит ruff (там это ``SyntaxError``), у документации
   такого рубежа не было.

Никаких внешних зависимостей: чистый ``ast``/``json``/``re`` + ``pathlib``,
детерминированно и кроссплатформенно (Windows/Linux/macOS).

Запуск::

    python scripts/check_docs_guardrails.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

__all__ = [
    "CHANGELOG_MAX_VERSIONS",
    "README_LINE_BUDGET",
    "check_changelog_fragments",
    "check_changelog_version_budget",
    "check_docs_directions",
    "check_docs_index_completeness",
    "check_issue_tail_policy",
    "check_link_captions",
    "check_markdown_links",
    "check_no_conflict_markers",
    "check_pypi_readme_is_absolute",
    "check_readme_budget",
    "check_showcase_metrics",
    "check_ui_issue_tail_policy",
    "collect_markdown_files",
    "collect_ui_strings",
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
#: Начало первой строки файла, собранного скриптом: номера задач в нём — данные
#: (след правила), а не рабочий журнал (issue #1342).
#:
#: Именно ПРЕФИКС, а не маркер, и имя об этом говорит: за словом идёт путь к
#: генератору, который у каждого файла свой. Маркер сверяют целиком (правило
#: 141), префикс — началом, и подменять одно другим нельзя ни в коде, ни в
#: названии. Полноту шапки (генератор назван и существует) проверяет
#: `check_generated_sources.py`.
_GENERATED_PREFIX = "<!-- СГЕНЕРИРОВАНО"

_AGENT_TAIL_BUDGET = 6

# [текст](target) — не изображение (нет ведущего "!"), target без пробелов/скобок.
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
# То же, но с захватом ПОДПИСИ — для проверки «подпись-путь совпадает с целью».
_LINK_WITH_TEXT_RE = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\s]+)\)")
# Подпись-ПУТЬ: `docs/use/configuration.md` или `docs/api.md § Раздел` (с
# бэктиками или без). Требуется каталог в подписи: голое имя файла
# (`glossary.md`) — это метка, а не путь, который читатель скопирует; подписи с
# `../` тоже пропускаем — они относительны положению документа, а не корня.
_PATH_CAPTION_RE = re.compile(r"^`?((?!\.\.)[\w.-]+(?:/[\w.-]+)+\.md)`?(?:\s+[§#].*)?$")
# Заголовки ATX: "# ...", "## ..." и т.д.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# Внешние схемы, которые не проверяем (сеть/почта/якоря протоколов).
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#?mailto)", re.IGNORECASE)
# Абсолютная ссылка на СВОЙ же репозиторий (issue #832): README.md обязан
# ссылаться абсолютно — на PyPI относительные пути резолвятся к pypi.org и дают
# 404, — но проверять такие ссылки надо как локальные, иначе перевод README на
# абсолютные адреса тихо выводит его из-под гейтов ссылок и подписей.
_SELF_REPO_RE = re.compile(
    r"^https://(?:github\.com/ArtVsMark/Stepik-Python-Grader/(?:blob|raw)/main/"
    r"|raw\.githubusercontent\.com/ArtVsMark/Stepik-Python-Grader/main/)(?P<path>.+)$"
)


def _as_repo_path(target: str) -> str | None:
    """Ссылка на свой репозиторий → путь от корня; иначе ``None`` (issue #832)."""
    match = _SELF_REPO_RE.match(target)
    return match.group("path") if match else None


# Версионный заголовок релиза в CHANGELOG.md: "## [1.8.0] - ДАТА" (issue #373).
# "[Unreleased]" и до-версионные "## [unreleased] / <дата>" не матчатся.
_CHANGELOG_VERSION_RE = re.compile(r"^## \[\d+\.\d+\.\d+\]")


def _index_link_re(name: str) -> re.Pattern[str]:
    """Регекс inline-ссылки на ``name`` из индекса того же каталога (issue #788).

    Прежде индекс проверялся подстрокой ``name in index_text`` — а имя, которое
    является хвостом другого упомянутого имени, проходило проверку без всякой
    ссылки. Такая пара в репозитории есть: ``audit-2026-07-15.md`` целиком
    входит в ``issue-audit-2026-07-15.md``, поэтому удаление строки про первый
    guard бы не заметил — ровно тот регресс, ради которого он написан. Голое
    упоминание в бэктиках (``` `file.md` ```) тоже больше не считается
    индексацией: из навигации по нему не перейти.

    Учитываются формы, встречающиеся в индексах: ``](name.md)``,
    ``](./name.md)``, ``](name.md#anchor)`` и CommonMark-вариант в угловых
    скобках.
    """
    return re.compile(r"\]\(<?\.?/?" + re.escape(name) + r">?[)#\s]")


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
            self_repo = _as_repo_path(target)
            if self_repo is not None:
                target = self_repo
            elif _EXTERNAL_RE.match(target):
                continue

            path_part, _, anchor = target.partition("#")
            base = _ROOT if self_repo is not None else md.parent
            rel = base if not path_part else (base / path_part)
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


def check_link_captions(errors: list[str]) -> None:
    """Подпись-путь у ссылки совпадает с её целью (issue #827).

    Ссылки проверялись только по цели, поэтому подписи пережили раскладку
    ``docs/`` по направлениям: ``[docs/configuration.md § …](docs/use/configuration.md#…)``
    — цель рабочая, а путь в тексте ведёт в никуда. Читатель копирует именно
    подпись, и чаще всего в самых чувствительных местах (threat model в
    SECURITY.md).
    """
    checked = 0
    for md in collect_markdown_files():
        rel_doc = md.relative_to(_ROOT).as_posix()
        for match in _LINK_WITH_TEXT_RE.finditer(md.read_text(encoding="utf-8")):
            caption, target = match.group(1), match.group(2)
            caption_match = _PATH_CAPTION_RE.match(caption.strip())
            if caption_match is None:
                continue
            # Абсолютная ссылка на свой репозиторий — тот же локальный путь
            # (issue #832): без разворота README.md выпал бы из этой проверки.
            self_repo = _as_repo_path(target)
            if self_repo is not None:
                target = self_repo
            elif _EXTERNAL_RE.match(target):
                continue
            target_path = target.partition("#")[0]
            if not target_path:
                continue
            checked += 1
            base = _ROOT if self_repo is not None else md.parent
            # Подпись — «хвост» реального пути: `use/web-interface.md` из
            # docs/dev/ сокращает `docs/use/web-interface.md` и читателя не
            # обманывает. Ловим другое — путь, которого в дереве нет вовсе
            # (`docs/configuration.md` после раскладки docs/ по направлениям).
            claimed = tuple(Path(caption_match.group(1)).parts)
            actual = tuple((base / target_path).resolve().relative_to(_ROOT).parts)
            if actual[-len(claimed) :] != claimed:
                errors.append(
                    f"{rel_doc}: подпись ссылки '{caption_match.group(1)}' не совпадает "
                    f"с целью '{target_path}' — читатель копирует путь из подписи и "
                    "попадает в никуда."
                )
    print(f"link captions: checked {checked} path-like caption(s) against their targets.")


# Витрины, где метрики живут только в бейджах (issue #829). Число, вписанное
# руками, устаревает к следующему PR: «2100+ tests» пережило рост набора на
# четверть и осталось в английской версии, когда из русской его уже убрали.
_SHOWCASE_FILES = ("README.md", "README.en.md")
# «2100+ tests», «1349 карточек», «2349 тестов» — число рядом со словом-метрикой.
_HARDCODED_METRIC_RE = re.compile(
    r"\b\d{3,}\+?\s*(?:automated\s+)?(?:tests?|тест\w*|карточ\w+|cards?)\b",
    re.IGNORECASE,
)


def check_showcase_metrics(errors: list[str]) -> None:
    """В README и README.en.md нет метрик числом — только бейджи (issue #829).

    Живой источник числа тестов, покрытия и размера глоссария — бейджи в шапке:
    они обновляются каждым прогоном CI, а вписанная руками цифра начинает врать
    в первый же день и противоречить соседнему файлу.
    """
    checked = 0
    for name in _SHOWCASE_FILES:
        path = _ROOT / name
        if not path.is_file():
            continue
        checked += 1
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("[!["):  # сами бейджи — источник истины
                continue
            found = _HARDCODED_METRIC_RE.findall(line)
            if found:
                errors.append(
                    f"{name}: метрика числом в прозе ('{line.strip()[:60]}…') — "
                    "живой источник у этих чисел бейджи в шапке; вписанное руками "
                    "устареет к следующему PR."
                )
    print(f"showcase metrics: checked {checked} README file(s) for hardcoded numbers.")


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
            if _index_link_re(md.name).search(index_text):
                continue
            # issue #788: различаем «файла нет вовсе» и «упомянут, но ссылки
            # нет» — второе типично после рефакторинга индекса, и подсказка
            # экономит поиск глазами.
            hint = (
                " (name appears in the text, but not as a link — indexing means a clickable entry)"
                if md.name in index_text
                else ""
            )
            errors.append(
                f"{idx_rel}: '{md.name}' exists in {d.relative_to(_ROOT)}/ but is not "
                f"linked from the navigation index{hint} (issue #300/#562/#788)."
            )
    print(f"docs/ index: checked {checked} file(s) against per-directory README indexes.")


def check_changelog_fragments(errors: list[str]) -> None:
    """Фрагменты ``changelog.d/`` читаются сборкой релиза.

    Негодный фрагмент опасен именно тем, что молчит: запись просто не попадёт в
    релиз, и обнаружится это через месяц. Правила разбора живут в
    ``scripts/collect_changelog.py`` — здесь они не дублируются, а вызываются,
    иначе два набора правил разъедутся при первой же правке.
    """
    script = _ROOT / "scripts" / "collect_changelog.py"
    spec = importlib.util.spec_from_file_location("_collect_changelog_guard", script)
    if spec is None or spec.loader is None:  # pragma: no cover — файл на месте в репозитории
        errors.append("scripts/collect_changelog.py: не удалось загрузить модуль сборки")
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    problems: list[str] = module.validate(_ROOT / "changelog.d")
    for problem in problems:
        errors.append(f"changelog.d/{problem}")
    if not problems:
        count = len(module.fragment_files(_ROOT / "changelog.d"))
        print(f"changelog.d: {count} fragment(s), all readable by the release collector.")


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
            "docs/archive/changelog-archive.md (keep [Unreleased] + the newest "
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

    * ``CHANGELOG.md``, ``HISTORY.md`` и ``docs/archive/`` — это и есть логи.
      ``HISTORY.md`` живёт в корне, а не в ``docs/archive/`` (issue #1181): это
      витрина уровня README, но жанр у неё исторический, и номера релизных
      issue в записях — часть содержания, а не журнал работ;
    * ``docs/audit/`` — находки аудита привязаны к задачам по определению;
    * ``docs/dev/adr/`` — ADR отвечает «почему решили так», задача часть ответа;
    * ``docs/dev/design/`` — там номер работает как идентификатор согласованного
      требования (``#156`` — контракт ``/api/v1/*``, ``#157.1``–``#157.6`` —
      требования к sandbox, на которые ADR-0008 ссылается поштучно). Лимит
      ``_DESIGN_TAIL_BUDGET`` не даёт этой зоне снова обрасти журналом;
    * ``CLAUDE.md`` и ``docs/agent/`` — агентский контракт; лимит
      ``_AGENT_TAIL_BUDGET`` оставляет место указателям вроде roadmap-issue;
    * **сгенерированные файлы** (issue #1342) — там номера не текст, а данные:
      указатель правил собирается из следов каталога, и след — это и есть
      ссылка на задачу. Править такой файл руками нельзя, значит и «вынести
      журнал в CHANGELOG» в нём невозможно: лимит ловил бы генератор, а не
      автора. Признак — маркер ``СГЕНЕРИРОВАНО`` в первой строке.

    Всё остальное (``docs/use/``, ``docs/dev/*.md``, ``README``, ``SECURITY``,
    ``CONTRIBUTING``) должно держать ноль.
    """
    free_zones = ("CHANGELOG.md", "HISTORY.md", "docs/archive/", "docs/audit/", "docs/dev/adr/")
    budgeted = {
        "docs/dev/design/": _DESIGN_TAIL_BUDGET,
        "CLAUDE.md": _AGENT_TAIL_BUDGET,
        "docs/agent/": _AGENT_TAIL_BUDGET,
    }

    zone_totals: dict[str, int] = {}
    checked = 0
    generated = 0
    for md in collect_markdown_files():
        rel = md.relative_to(_ROOT).as_posix()
        if any(rel == z or rel.startswith(z) for z in free_zones):
            continue
        text = md.read_text(encoding="utf-8")
        # issue #1342: файл собран скриптом — номера в нём данные, а не журнал.
        if text.lstrip().startswith(_GENERATED_PREFIX):
            generated += 1
            continue
        tails = _ISSUE_TAIL_RE.findall(text)
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
        + (f"; {generated} generated file(s) skipped" if generated else "")
    )


def _help_strings(path: Path) -> list[str]:
    """Строки справки argparse из модуля-парсера: ``help``/``description``/``epilog``.

    Разбор через ``ast``, а не regex: неявная конкатенация литералов в скобках
    (``help=("…" "…")``) сворачивается парсером в один ``ast.Constant``, поэтому
    многострочные справки читаются целиком, а не по кусочкам. Динамические
    значения (f-строки, вызовы) пропускаются — проверять в них нечего.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in {"help", "description", "epilog"}:
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found.append(kw.value.value)
    return found


def _json_strings(value: object) -> list[str]:
    """Все строковые значения JSON-структуры (ключи не в счёт — они идентификаторы)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _json_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _json_strings(v)]
    return []


def collect_ui_strings(errors: list[str] | None = None) -> dict[str, list[str]]:
    """Пользовательские строки интерфейса: ``{относительный путь: [строки]}``.

    Две поверхности, которые пользователь читает наравне с документацией:
    вывод ``--help`` (``cli/options.py``) и каталоги сообщений
    (``core/locales/*.json``).

    issue #988 (REV-2-02): пропавший вход — это ошибка, а не повод пропустить
    проверку. Прежняя редакция брала оба источника «если найдётся» (``is_file``
    плюс ``glob``), поэтому переезд ``options.py`` или каталога локалей обнулял
    гейт, оставляя его зелёным: проверка сообщала «0 строк проверено» ровно тем
    же тоном, что и «всё чисто». Guard, зеленеющий на пустом входе, не отличим
    от отсутствующего.
    """
    strings: dict[str, list[str]] = {}

    options_py = _ROOT / "src" / "stepik_grader" / "cli" / "options.py"
    if options_py.is_file():
        strings[options_py.relative_to(_ROOT).as_posix()] = _help_strings(options_py)
    elif errors is not None:
        errors.append(
            "UI-strings guard: не найден src/stepik_grader/cli/options.py — вход проверки "
            "пропал (переезд файла?), политика по строкам --help не проверена."
        )

    locales = _ROOT / "src" / "stepik_grader" / "core" / "locales"
    locale_files = sorted(locales.glob("*.json"))
    if not locale_files and errors is not None:
        errors.append(
            "UI-strings guard: в src/stepik_grader/core/locales нет ни одного *.json — "
            "вход проверки пропал, политика по сообщениям локалей не проверена."
        )
    for loc in locale_files:
        data = json.loads(loc.read_text(encoding="utf-8"))
        strings[loc.relative_to(_ROOT).as_posix()] = _json_strings(data)

    return strings


def check_ui_issue_tail_policy(errors: list[str]) -> None:
    """В пользовательских строках интерфейса нет ссылок вида ``#NNN`` (issue #820).

    Политика та же, что у объясняющей документации, и по той же причине: номер
    задачи ничего не сообщает тому, кто читает ``--help`` или сообщение об
    ошибке. Разница лишь в поверхности — здесь проверяются строки в коде, а не
    Markdown, поэтому гейт на доках эту зону не покрывал.
    """
    checked = 0
    for source, values in collect_ui_strings(errors).items():
        tails = sorted({tail for value in values for tail in _ISSUE_TAIL_RE.findall(value)})
        checked += len(values)
        if tails:
            errors.append(
                f"{source}: {len(tails)} issue reference(s) ({', '.join(tails[:5])}) "
                "in user-facing strings. Help output and locale messages explain a "
                "flag to the user - the work log belongs in CHANGELOG.md, the "
                "rationale in code comments."
            )
    print(f"UI-strings issue policy: checked {checked} user-facing string(s) at zero.")


def check_pypi_readme_is_absolute(errors: list[str]) -> None:
    """В readme-файле пакета нет относительных ссылок и картинок (issue #832).

    ``pyproject.toml`` объявляет ``readme = "README.md"``, и его содержимое
    уезжает в ``long_description`` дистрибутива как есть. PyPI относительные
    пути не переписывает — он резолвит их к ``pypi.org``: hero-гиф и скриншоты
    не отображаются, два десятка ссылок на ``docs/`` дают 404. Витрина
    ``pipx install`` — это то, что видит человек, УЖЕ готовый поставить пакет.

    На GitHub абсолютные ссылки на свой же репозиторий работают ровно так же,
    поэтому цена нулевая; чтобы они при этом не выпали из гейтов, ссылки и
    подписи резолвятся обратно в путь (``_as_repo_path``).
    """
    declared = re.search(
        r'^readme\s*=\s*"([^"]+)"', (_ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
    )
    if declared is None:  # pragma: no cover — поле обязательное, но пусть не падаем
        errors.append("pyproject.toml: не найдено поле readme — нечего проверять на PyPI.")
        return
    name = declared.group(1)
    text = (_ROOT / name).read_text(encoding="utf-8")
    relative = [
        target
        # Ищем по "](цель)", а НЕ по "[подпись](цель)": подпись бейджа сама
        # содержит картинку — `[![Glossary](img)](docs/…)`, — и шаблон с
        # `[^\]]*` такую вложенную ссылку пропускал. Две относительные цели
        # так и уцелели в README при переводе на абсолютные (issue #832).
        for target in re.findall(r"\]\(([^)\s]+)\)", text)
        if not _EXTERNAL_RE.match(target) and not target.startswith("#")
    ]
    if relative:
        errors.append(
            f"{name}: относительные ссылки/картинки в readme пакета — на PyPI они "
            f"резолвятся к pypi.org и дают 404 ({', '.join(sorted(set(relative))[:5])}). "
            "Используйте абсолютные адреса github.com/.../blob/main/ и "
            "raw.githubusercontent.com/.../main/."
        )
        return
    print(f"PyPI readme: {name} has no relative links or images.")


def check_no_conflict_markers(errors: list[str]) -> None:
    """В документации нет незаконченного слияния (#1164).

    Маркеры конфликта — единственный вид поломки, который в Markdown проходит
    все остальные гейты: ссылки резолвятся, бюджет строк соблюдён, индексы
    полны, а раздел про лаунчер при этом показан читателю дважды, вперемешку с
    ``HEAD`` и ``origin/main``. В ``.py`` то же самое ловит ruff — там это
    синтаксическая ошибка; у документации такого рубежа не было.

    Прецедент: описание окна лаунчера уехало в ``main`` с маркерами и прожило
    там несколько PR — ни один прогон CI не возразил.
    """
    opening, closing = "<" * 7, ">" * 7
    files = collect_markdown_files()
    found = 0
    for md in files:
        hits = [
            number
            for number, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1)
            if line.startswith((f"{opening} ", f"{closing} "))
        ]
        if hits:
            found += 1
            where = ", ".join(f"строка {number}" for number in hits[:5])
            errors.append(
                f"{md.relative_to(_ROOT)}: маркеры конфликта слияния ({where}) — "
                "слияние не доведено до конца, читателю показаны обе версии текста."
            )
    if not found:
        print(f"Merge conflicts: none across {len(files)} Markdown file(s).")


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли.

    Тексты нарушений русские, а консоль Windows по умолчанию cp1252/cp1251: без
    этого ``print`` падал ``UnicodeEncodeError`` и гейт возвращал 1 «на ровном
    месте», подменяя настоящую причину отказа своей собственной (тот же приём,
    что в ``scripts/skip_inventory.py`` и ``cli/options._force_utf8_stdio``).
    No-op на потоках без ``reconfigure`` — например, перехваченных pytest.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    _force_utf8_stdout()
    errors: list[str] = []
    check_no_conflict_markers(errors)
    check_readme_budget(errors)
    check_pypi_readme_is_absolute(errors)
    check_markdown_links(errors)
    check_link_captions(errors)
    check_showcase_metrics(errors)
    check_docs_directions(errors)
    check_docs_index_completeness(errors)
    check_changelog_version_budget(errors)
    check_changelog_fragments(errors)
    check_issue_tail_policy(errors)
    check_ui_issue_tail_policy(errors)

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
