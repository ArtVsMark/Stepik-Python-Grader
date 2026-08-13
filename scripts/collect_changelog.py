#!/usr/bin/env python3
"""scripts/collect_changelog.py — записи CHANGELOG файлами, а не строками.

Проблема, которую это убирает. Запись в `CHANGELOG.md` обязательна в каждом PR,
и все PR правят **один и тот же участок** файла. Пока PR один, это незаметно;
как только их два, каждый мерж делает остальные конфликтными. Драйвер
``merge=union`` из ``.gitattributes`` спасает только локально: ``git merge``
сливает буфер без вмешательства ровно тогда, когда GitHub уже показывает
``mergeable_state=dirty``. А конфликт означает не просто ручную правку — на
конфликтном PR **не создаются проверки вообще**, потому что прогон идёт по
merge-коммиту, которого нет.

Решение — то же, к которому пришли towncrier и подобные: **PR кладёт свой
файл**, а не строку в общий. Два файла с разными именами не конфликтуют
никогда, поэтому очередь мержа перестаёт зависеть от порядка.

Формат фрагмента::

    changelog.d/<slug>.<секция>.md

``slug`` — что угодно уникальное, обычно имя ветки без префикса; секция — одна
из ``added``/``changed``/``fixed``/``removed``/``internal``. Внутри — одна
строка текста записи, без ведущего ``-`` и без имени секции: их подставит
сборка. Номер PR указывается в тексте как раньше — ``(#1234)``.

Запуск::

    python scripts/collect_changelog.py --check     # валидация (CI и preflight)
    python scripts/collect_changelog.py --preview   # как соберётся, ничего не меняя
    python scripts/collect_changelog.py --collect   # собрать в ## [Unreleased] и удалить файлы

Релизная процедура не меняется: ``--collect`` кладёт записи в ``[Unreleased]``,
который при релизе переименовывается в ``[X.Y.0] - ДАТА``, как и прежде.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

__all__ = [
    "SECTIONS",
    "Fragment",
    "collect_into_changelog",
    "fragment_files",
    "main",
    "parse_fragment",
    "render_sections",
    "validate",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FRAGMENT_DIR = "changelog.d"
_CHANGELOG = "CHANGELOG.md"

# Порядок секций фиксирован: он же в CHANGELOG.md, и «Added» перед «Fixed»
# читается как «что нового, а потом что починили».
SECTIONS: tuple[str, ...] = ("added", "changed", "fixed", "removed", "internal")

_TITLES = {
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "removed": "Removed",
    "internal": "Internal",
}

_NAME_RE = re.compile(r"^(?P<slug>[\w.-]+)\.(?P<section>[a-z]+)\.md$")
_UNRELEASED_RE = re.compile(r"^## \[Unreleased\]", re.MULTILINE)


class Fragment:
    """Один фрагмент: секция, текст записи и файл, из которого он прочитан."""

    def __init__(self, section: str, text: str, path: pathlib.Path) -> None:
        self.section = section
        self.text = text
        self.path = path

    def __repr__(self) -> str:
        """Отладочное представление."""
        return f"Fragment({self.section!r}, {self.text[:40]!r})"


def fragment_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Файлы-фрагменты в каталоге (``README.md`` не считается)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.name != "README.md")


def parse_fragment(path: pathlib.Path) -> Fragment | None:
    """Разобрать фрагмент; ``None``, если имя или содержимое негодные."""
    match = _NAME_RE.match(path.name)
    if match is None or match.group("section") not in SECTIONS:
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return Fragment(match.group("section"), text.lstrip("- ").strip(), path)


def validate(directory: pathlib.Path) -> list[str]:
    """Ошибки во фрагментах; пустой список — всё в порядке."""
    problems: list[str] = []
    for path in fragment_files(directory):
        match = _NAME_RE.match(path.name)
        if match is None:
            problems.append(f"{path.name}: имя не по шаблону <slug>.<секция>.md")
            continue
        section = match.group("section")
        if section not in SECTIONS:
            problems.append(
                f"{path.name}: неизвестная секция {section!r}; допустимы {', '.join(SECTIONS)}"
            )
            continue
        if not path.read_text(encoding="utf-8").strip():
            problems.append(f"{path.name}: пустой файл — запись должна быть одной строкой текста")
    return problems


def render_sections(fragments: list[Fragment]) -> str:
    """Собрать фрагменты в markdown-блок с заголовками секций."""
    lines: list[str] = []
    for section in SECTIONS:
        chosen = [f for f in fragments if f.section == section]
        if not chosen:
            continue
        lines.append(f"### {_TITLES[section]}")
        lines.append("")
        lines.extend(f"- {f.text}" for f in sorted(chosen, key=lambda f: f.path.name))
        lines.append("")
    return "\n".join(lines)


def collect_into_changelog(root: pathlib.Path, *, remove: bool = True) -> tuple[int, str]:
    """Перенести фрагменты в ``## [Unreleased]``; вернуть (сколько, что вышло)."""
    directory = root / _FRAGMENT_DIR
    fragments = [f for f in (parse_fragment(p) for p in fragment_files(directory)) if f]
    if not fragments:
        return 0, ""

    block = render_sections(fragments)
    changelog = root / _CHANGELOG
    text = changelog.read_text(encoding="utf-8")
    match = _UNRELEASED_RE.search(text)
    if match is None:
        raise RuntimeError(f"{_CHANGELOG}: нет секции «## [Unreleased]», некуда собирать")

    insert_at = text.index("\n", match.end()) + 1
    updated = text[:insert_at] + "\n" + block + text[insert_at:]
    changelog.write_text(updated, encoding="utf-8")

    if remove:
        for fragment in fragments:
            fragment.path.unlink(missing_ok=True)
    return len(fragments), block


def main(argv: list[str] | None = None) -> int:
    """Проверить, показать или собрать фрагменты; 0 — успех."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="проверить фрагменты и выйти")
    parser.add_argument("--preview", action="store_true", help="показать сборку, ничего не меняя")
    parser.add_argument("--collect", action="store_true", help="собрать в [Unreleased] и удалить")
    args = parser.parse_args(argv)

    directory = _ROOT / _FRAGMENT_DIR
    problems = validate(directory)
    if problems:
        print("Негодные фрагменты CHANGELOG:", file=sys.stderr)
        for problem in problems:
            print(f"  — {problem}", file=sys.stderr)
        return 1

    if args.check:
        print(f"Фрагментов: {len(fragment_files(directory))} — все читаются.")
        return 0

    if args.preview:
        fragments = [f for f in (parse_fragment(p) for p in fragment_files(directory)) if f]
        print(render_sections(fragments) or "Фрагментов нет.")
        return 0

    if args.collect:
        moved, block = collect_into_changelog(_ROOT)
        print(f"Перенесено записей: {moved}" if moved else "Фрагментов нет — переносить нечего.")
        if block:
            print(block)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
