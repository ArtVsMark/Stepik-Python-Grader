#!/usr/bin/env python3
"""scripts/extract_release_notes.py — секция CHANGELOG.md как тело релиза (issue #834).

Релиз собирался только автогенерацией `generate_release_notes` — списком
заголовков PR вида «chore(ci): update badges [skip ci] by @ArtVsMark». Подробный
русский CHANGELOG, который ведётся в каждом PR, в релиз не попадал вовсе: страница,
которую цитируют и репостят, читалась как внутренний git log.

Скрипт вырезает секцию одной версии (`## [X.Y.0] - ДАТА`) и печатает её тело в
stdout (или в файл через `--out`). Автосписок PR при этом никуда не девается —
`action-gh-release` дописывает его ниже `body_path` как «Full changelog».

Запуск::

    python scripts/extract_release_notes.py v1.10.0 --out release-notes.md
    python scripts/extract_release_notes.py 1.10.0          # префикс `v` необязателен
    python scripts/extract_release_notes.py --latest         # верхняя версионная секция

Без внешних зависимостей: `re` + `pathlib`, детерминированно и кроссплатформенно.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

__all__ = ["NotesNotFoundError", "extract_notes", "latest_version", "main"]

_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = _ROOT / "CHANGELOG.md"

# Версионный заголовок релиза: "## [1.10.0] - 2026-07-30". `[Unreleased]` не
# матчится намеренно — незавершённый раздел телом релиза быть не может.
_VERSION_HEADING_RE = re.compile(r"^## \[(?P<version>\d+\.\d+\.\d+)\]", re.MULTILINE)
# Любой заголовок второго уровня — граница секции (в т.ч. `## [Unreleased]`).
_ANY_H2_RE = re.compile(r"^## ", re.MULTILINE)


class NotesNotFoundError(LookupError):
    """В CHANGELOG.md нет секции запрошенной версии."""


def _normalize(version: str) -> str:
    """`v1.10.0` и `1.10.0` — одно и то же: тег приходит с префиксом, заголовок без."""
    return version.strip().removeprefix("v")


def latest_version(text: str) -> str:
    """Верхняя версионная секция CHANGELOG (issue #834).

    Raises:
        NotesNotFoundError: версионных секций нет вовсе.
    """
    match = _VERSION_HEADING_RE.search(text)
    if match is None:
        raise NotesNotFoundError("в CHANGELOG.md нет ни одной секции вида '## [X.Y.Z]'")
    return match.group("version")


def extract_notes(text: str, version: str) -> str:
    """Тело секции ``## [version]`` без самого заголовка (issue #834).

    Границей служит СЛЕДУЮЩИЙ заголовок второго уровня, а не следующая версия:
    иначе `[Unreleased]`, стоящий выше, утащил бы в релиз незавершённый раздел.

    Raises:
        NotesNotFoundError: секции такой версии в файле нет — на релизе это
            означает, что запись под тег забыли, и молчать об этом нельзя.
    """
    wanted = _normalize(version)
    for match in _VERSION_HEADING_RE.finditer(text):
        if match.group("version") != wanted:
            continue
        body_start = text.index("\n", match.end()) + 1
        next_h2 = _ANY_H2_RE.search(text, body_start)
        body = text[body_start : next_h2.start() if next_h2 else len(text)]
        return body.strip()
    raise NotesNotFoundError(
        f"в CHANGELOG.md нет секции '## [{wanted}]' — запись под этот тег не добавлена"
    )


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0 и напечатать заметки; 1 — если секции нет."""
    parser = argparse.ArgumentParser(
        prog="python scripts/extract_release_notes.py",
        description="Вырезать секцию версии из CHANGELOG.md для тела GitHub-релиза.",
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="версия или тег (1.10.0 / v1.10.0); без аргумента нужен --latest",
    )
    parser.add_argument("--latest", action="store_true", help="взять верхнюю версионную секцию")
    parser.add_argument("--out", type=Path, help="файл для записи (по умолчанию — stdout)")
    parser.add_argument(
        "--changelog", type=Path, default=CHANGELOG, help="путь к CHANGELOG.md (для тестов)"
    )
    args = parser.parse_args(argv)

    if not args.version and not args.latest:
        parser.error("укажите версию или --latest")

    text = args.changelog.read_text(encoding="utf-8")
    try:
        version = latest_version(text) if args.latest else args.version
        notes = extract_notes(text, version)
    except NotesNotFoundError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.out is not None:
        args.out.write_text(notes + "\n", encoding="utf-8")
        print(f"release notes for {version}: {len(notes.splitlines())} line(s) -> {args.out}")
    else:
        print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
