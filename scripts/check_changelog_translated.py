#!/usr/bin/env python3
"""scripts/check_changelog_translated.py — записи CHANGELOG не остаются английскими.

Английский вклад принимается как есть, а русские артефакты создаёт мержащий
(``CONTRIBUTING.md`` § Английский вклад). Отсюда новый способ ошибиться:
английская запись ``changelog.d`` уезжает в ``CHANGELOG.md`` непереведённой,
потому что при мерже про перевод забыли. Цена ошибки отложенная и
невозвратная — ``CHANGELOG.md`` и release notes публикуются в GitHub Release и
на PyPI, где версия неперезаписываема; замеченное после публикации правится
только следующим релизом.

Проверка зеркальна «Чистоте ``en.json``» из ``check_locale_guardrails.py``: там
кириллица в английском файле означала, что перевод НЕ сделан, здесь то же самое
означает её отсутствие в русском.

Что проверяется, а что нет. В ``CHANGELOG.md`` смотрим **только то, что ещё не
выпущено** — секцию ``[Unreleased]`` и буфер над первым версионным заголовком, —
плюс фрагменты ``changelog.d`` и файлы, переданные явно (на релизе это
извлечённые release notes). Выпущенные секции не трогаем намеренно: до ``1.10.0``
записи писались по-английски, это состоявшаяся история, а не дефект. Гейт
защищает то, что ПУБЛИКУЕТСЯ сейчас, и переписывание прошлого в его задачи не
входит.

Что считается записью и что с ней делается:

* **проза без кириллицы — подозрение.** Из записи вырезаются код (``` `...` ``
  и блоки), ссылки-адреса и хвост вида ``(#1234)``; если в остатке есть буквы,
  но ни одной кириллической — запись помечается непереведённой;
* **запись из одних идентификаторов проходит молча.** Имена флагов, файлов и
  ключей локали кириллицы не содержат по природе, и после вырезания кода в
  остатке букв не остаётся вовсе. Механизм исключения именно такой, явный:
  строка целиком в обратных кавычках — не текст, а перечень имён, переводить в
  ней нечего.

Строгость разная по месту, и это главное в проверке:

* **на PR — предупреждение** (``::warning::``, код возврата 0): запись в ветке
  может быть ещё черновиком, и падать на состоянии работы значит останавливать
  чужие изменения;
* **на релизе — отказ** (``--strict``, код возврата 1): публикация
  непереведённого необратима, поэтому здесь гейт роняет прогон.

Запуск::

    python scripts/check_changelog_translated.py            # предупреждения, exit 0
    python scripts/check_changelog_translated.py --strict    # отказ, exit 1
    python scripts/check_changelog_translated.py --strict notes.md   # + release notes
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

__all__ = [
    "DEFAULT_CHANGELOG",
    "DEFAULT_FRAGMENTS",
    "check_files",
    "entries",
    "fragment_entry",
    "main",
    "problem_with",
    "prose",
    "unreleased_part",
]

_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CHANGELOG = _ROOT / "CHANGELOG.md"
DEFAULT_FRAGMENTS = _ROOT / "changelog.d"

# Блоки и inline-код: имена файлов, флаги и ключи локали не переводятся, и
# кириллицы в них не бывает по природе. Вырезаем ДО проверки, иначе запись из
# одних идентификаторов навсегда останется «непереведённой».
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Адрес ссылки: текст ссылки — проза и проверяется, адрес — нет.
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
# Хвост записи «(#1234)» и HTML-комментарии: не текст записи.
_ISSUE_TAIL_RE = re.compile(r"\(#\d+(?:\.\d+)?\)")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

# Строка записи в CHANGELOG.md и в собранных release notes: «- текст».
_ENTRY_RE = re.compile(r"^\s*[-*]\s+(?P<text>\S.*)$")
# Версионный заголовок «## [1.11.0] - 2026-08-20»: граница выпущенного.
_VERSION_HEADING_RE = re.compile(r"^##\s+\[\d+\.\d+\.\d+\]")


def prose(entry: str) -> str:
    """Текст записи без кода, адресов ссылок и хвоста ``(#1234)``.

    Именно этот остаток и решает, переведена запись или нет: всё вырезанное —
    идентификаторы, у которых языка нет.
    """
    text = _COMMENT_RE.sub(" ", entry)
    text = _FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _LINK_TARGET_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    return _ISSUE_TAIL_RE.sub(" ", text)


def problem_with(entry: str) -> str | None:
    """Чем плоха запись; ``None`` — переводить нечего или перевод есть."""
    remainder = prose(entry)
    if not _LETTER_RE.search(remainder):
        return None
    if _CYRILLIC_RE.search(remainder):
        return None
    return "ни одной кириллической буквы вне кода — запись похожа на непереведённую"


def entries(text: str) -> list[tuple[int, str]]:
    """Строки-записи ``- текст`` с номерами строк, без блоков кода и комментариев.

    Комментарий в шапке ``CHANGELOG.md`` содержит образцы записей; проверять их
    значило бы ловить документацию вместо изменений.
    """
    without_comments = _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    found: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(without_comments.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ENTRY_RE.match(line)
        if match is not None:
            found.append((number, match.group("text")))
    return found


def unreleased_part(text: str) -> str:
    """Часть ``CHANGELOG.md`` до первого версионного заголовка ``## [X.Y.0]``.

    Это и есть «ещё не выпущено»: ``[Unreleased]`` и буфер под ним. Ниже —
    история, которую гейт не судит: до ``1.10.0`` записи писались по-английски.
    """
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if _VERSION_HEADING_RE.match(line):
            return "\n".join(lines[:number])
    return text


def fragment_entry(path: Path) -> str:
    """Текст фрагмента ``changelog.d/<slug>.<секция>.md`` — он же одна запись."""
    return path.read_text(encoding="utf-8").strip()


def check_files(
    changelog: Path | None = None,
    fragments: Path | None = None,
    extra: list[Path] | None = None,
) -> list[str]:
    """Найденные проблемы строками ``путь:строка — чем плоха``."""
    problems: list[str] = []

    if fragments is not None and fragments.is_dir():
        for path in sorted(fragments.glob("*.md")):
            if path.name == "README.md":
                continue
            if (problem := problem_with(fragment_entry(path))) is not None:
                problems.append(f"{_display(path)}: {problem}")

    checked: list[Path] = [path for path in (changelog, *(extra or [])) if path is not None]
    for path in checked:
        if not path.is_file():
            problems.append(f"{path}: файла нет — проверять нечего, а должно быть")
            continue
        text = path.read_text(encoding="utf-8")
        # У самого CHANGELOG.md судим только невыпущенную часть; переданные
        # файлы (release notes) — целиком: они и есть то, что публикуется.
        if changelog is not None and path == changelog:
            text = unreleased_part(text)
        for number, entry in entries(text):
            if (problem := problem_with(entry)) is not None:
                shown = entry if len(entry) <= 90 else entry[:87] + "…"
                problems.append(f"{_display(path)}:{number}: {problem} — {shown}")
    return problems


def _display(path: Path) -> str:
    """Путь относительно корня репозитория, если он внутри него."""
    try:
        return path.resolve().relative_to(_ROOT).as_posix()
    except ValueError:
        return str(path)


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли.

    Сообщения русские, а консоль Windows по умолчанию однобайтовая: без этого
    падает даже ``--help`` — ``UnicodeEncodeError`` вместо текста, и гейт
    возвращает 1, подменяя своей причиной ту, о которой спрашивали. Тот же
    приём, что в ``check_docs_guardrails.py``.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — чисто (или только предупреждения); 1 — ``--strict`` и есть находки."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/check_changelog_translated.py",
        description="Проверка, что записи CHANGELOG и release notes — по-русски.",
    )
    parser.add_argument(
        "notes",
        nargs="*",
        type=Path,
        help="дополнительные файлы с записями (например, извлечённые release notes)",
    )
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument("--fragments", type=Path, default=DEFAULT_FRAGMENTS)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="отказ вместо предупреждения: место публикации, откатить нечем",
    )
    args = parser.parse_args(argv)

    problems = check_files(args.changelog, args.fragments, list(args.notes))
    level = "error" if args.strict else "warning"
    for problem in problems:
        print(f"::{level}::{problem}")
    if not problems:
        print("CHANGELOG и фрагменты: непереведённых записей нет.")
        return 0
    print(f"Записей без перевода: {len(problems)}.")
    if args.strict:
        print(
            "Публикация остановлена: CHANGELOG и release notes уезжают в GitHub "
            "Release и на PyPI, где версия неперезаписываема.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
