#!/usr/bin/env python3
"""scripts/check_adr_records.py — запись о решении полна, и её не правят задним числом.

Два правила каталога об одном предмете — записи ADR, — поэтому и гейт один.

**042: решение записывается вместе с отвергнутыми вариантами.** Запись без
раздела «Альтернативы» — не решение, а объявление: читатель через год видит
выбранный вариант и не видит, что рядом рассматривалось и чем оно хуже. Ровно
этого не хватает, когда решение предлагают пересмотреть: спор начинается с
нуля, потому что прошлые доводы не записаны. Отсюда и «не меньше двух»:
единственная альтернатива — это тот же выбор, только переписанный.

**043: решение не правится задним числом — его отменяет новое.** Правка старой
записи стирает причину перехода: остаётся текущее мнение и ни следа того, что
его сменило. Меняется решение — заводится новый ADR, а старый помечается
``Superseded by ADR-XXXX``. Это уже объявлено в ``docs/dev/adr/README.md``, но
до сих пор держалось только вниманием.

Что здесь проверяется:

- у каждой записи есть шапка со статусом из объявленного набора;
- есть раздел «Альтернативы» и в нём не меньше двух перечисленных вариантов;
- ``Superseded by ADR-XXXX`` называет **существующую** запись;
- ветка не переписывает «Контекст», «Решение» или «Альтернативы» уже принятой
  записи: правка задним числом видна как изменение этих разделов у файла,
  который в ``main`` уже есть.

Последняя проверка отличает правку решения от смены статуса и от опечатки:
меняются только шапка или прочие разделы — гейт молчит, потому что пометить
запись заменённой и починить ссылку разрешено и нужно.

Запуск::

    python scripts/check_adr_records.py
    python scripts/check_adr_records.py --base origin/main   # с какой базой сверять
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable

__all__ = [
    "FROZEN_SECTIONS",
    "STATUSES",
    "alternatives_count",
    "incomplete_records",
    "main",
    "rewritten_decisions",
    "status_of",
]

# issue #1095: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_ROOT = pathlib.Path(__file__).parent.parent
_ADR = _ROOT / "docs" / "dev" / "adr"

#: Объявленный набор статусов (``docs/dev/adr/README.md``). Свободный текст
#: после статуса разрешён — им поясняют, что именно реализовано.
STATUSES: tuple[str, ...] = ("Proposed", "Accepted", "Rejected", "Superseded")

#: Разделы, правка которых и есть «задним числом». Шапка сюда не входит
#: намеренно: пометить запись заменённой — законное изменение старого файла.
FROZEN_SECTIONS: tuple[str, ...] = ("Контекст", "Решение", "Альтернативы")

_STATUS_RE = re.compile(r"^-\s+\*\*Статус:\*\*\s*(?P<value>.+)$", re.MULTILINE)
_SUPERSEDED_RE = re.compile(r"Superseded\s+by\s+ADR-(?P<number>\d{4})", re.IGNORECASE)
_RECORD_NAME = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")


def _records(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Файлы записей ADR — README и черновики каталогом не считаются."""
    base = (root or _ROOT) / "docs" / "dev" / "adr"
    return sorted(path for path in base.glob("*.md") if _RECORD_NAME.match(path.name))


def status_of(text: str) -> str | None:
    """Строка статуса из шапки записи, если она там есть."""
    found = _STATUS_RE.search(text)
    return found.group("value").strip() if found else None


def _section(text: str, title: str) -> str | None:
    """Тело раздела ``## <title>`` до следующего заголовка того же уровня."""
    lines = text.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().startswith(title)
            continue
        if inside:
            collected.append(line)
    return "\n".join(collected) if inside or collected else None


def alternatives_count(text: str) -> int:
    """Сколько вариантов перечислено в разделе «Альтернативы».

    Считаются пункты верхнего уровня: вложенные (с отступом) — это доводы
    внутри варианта, а не отдельный вариант.
    """
    body = _section(text, "Альтернатив")
    if body is None:
        return 0
    return sum(1 for line in body.splitlines() if re.match(r"^[-*+]\s+\S", line))


def incomplete_records(root: pathlib.Path | None = None) -> list[str]:
    """Записи без статуса, без альтернатив или с висячей ссылкой замены."""
    base = root or _ROOT
    numbers = {path.name[:4] for path in _records(base)}
    found: list[str] = []
    for path in _records(base):
        name = path.name
        text = path.read_text(encoding="utf-8")

        status = status_of(text)
        if status is None:
            found.append(f"{name}: нет строки «- **Статус:** ...» в шапке")
        elif not status.startswith(STATUSES):
            found.append(f"{name}: статус «{status}» вне набора {', '.join(STATUSES)}")

        count = alternatives_count(text)
        if count < 2:
            found.append(
                f"{name}: альтернатив перечислено {count}, нужно не меньше двух "
                "(один вариант — это тот же выбор, только переписанный)"
            )

        superseded = _SUPERSEDED_RE.search(status or "")
        if superseded and superseded.group("number") not in numbers:
            found.append(f"{name}: заменена записью ADR-{superseded.group('number')}, которой нет")
    return found


_HUNK_RE = re.compile(
    r"^@@ -(?P<old>\d+)(?:,(?P<old_len>\d+))? \+(?P<new>\d+)(?:,(?P<new_len>\d+))? @@"
)


def _sections_by_line(text: str) -> list[tuple[int, str]]:
    """Пары «номер строки заголовка → название раздела», сверху вниз."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            found.append((number, line[3:].strip()))
    return found


def _section_at(sections: list[tuple[int, str]], line: int) -> str:
    """Раздел, внутри которого лежит строка (шапка до первого ## — пусто)."""
    name = ""
    for start, title in sections:
        if start <= line:
            name = title
        else:
            break
    return name


def _changed_sections(diff: str, side: Callable[[str, str], str]) -> dict[str, set[str]]:
    """Какие разделы каких записей тронул дифф.

    Заголовок ханка (``@@ ... @@ <хвост>``) для Markdown приходит произвольной
    предыдущей строкой, а не заголовком раздела, — по нему определять раздел
    нельзя. Поэтому считаются **номера строк**: добавленная строка ищется в
    новой версии файла, удалённая — в базовой.

    Args:
        diff: вывод ``git diff --unified=0``.
        side: как получить текст файла — ``side(path, "new"|"old")``.
    """
    touched: dict[str, set[str]] = {}
    path = ""
    cache: dict[tuple[str, str], list[tuple[int, str]]] = {}
    old_line = new_line = 0

    def sections(which: str) -> list[tuple[int, str]]:
        key = (path, which)
        if key not in cache:
            cache[key] = _sections_by_line(side(path, which))
        return cache[key]

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            old_line = int(hunk.group("old"))
            new_line = int(hunk.group("new"))
            continue
        if not path.endswith(".md"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            touched.setdefault(path, set()).add(_section_at(sections("new"), new_line))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            touched.setdefault(path, set()).add(_section_at(sections("old"), old_line))
            old_line += 1
    return touched


def rewritten_decisions(base: str = "origin/main", root: pathlib.Path | None = None) -> list[str]:
    """Записи, у которых ветка переписала «замороженные» разделы.

    Args:
        base: с чем сравнивать — ветка правит **уже существующую** запись
            только относительно общей базы.
        root: корень репозитория (для тестов).

    Returns:
        Строки «файл: раздел» для каждого тронутого замороженного раздела.

    Raises:
        RuntimeError: базы сравнения нет — ответить нечем.
    """
    cwd = root or _ROOT

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, encoding="utf-8"
        )
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or f"git {args[0]} не отработал")
        return done.stdout

    diff = git("diff", "--unified=0", f"{base}...HEAD", "--", "docs/dev/adr")
    # Файл, добавленный этой же веткой, править задним числом нельзя по
    # определению: он ещё не решение, а черновик решения.
    # Правило 165: пути читаются по NUL. ``.split()`` разваливал бы ещё и имена
    # с пробелами, а экранированное не-ASCII имя молча выпадало бы из набора
    # «добавлено этой веткой» — то есть правка задним числом переставала бы
    # отличаться от нового файла (issue #1417).
    fresh = {
        path
        for path in git(
            "diff", "--name-only", "--diff-filter=A", f"{base}...HEAD", "-z", "--", "docs/dev/adr"
        ).split("\0")
        if path
    }

    def side(path: str, which: str) -> str:
        revision = "HEAD" if which == "new" else base
        try:
            return git("show", f"{revision}:{path}")
        except RuntimeError:
            return ""

    found: list[str] = []
    for path, names in sorted(_changed_sections(diff, side).items()):
        if path in fresh or not _RECORD_NAME.match(pathlib.Path(path).name):
            continue
        for name in sorted(names):
            if name.startswith(FROZEN_SECTIONS):
                found.append(f"{path}: раздел «{name}»")
    return found


def main(argv: list[str] | None = None) -> int:
    """0 — записи полны и не переписаны; 1 — находка; 2 — сверить не с чем."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "база сравнения; без неё проверяется только полнота записей — "
            "историю ветки видит preflight, а не мелкий клон CI"
        ),
    )
    args = parser.parse_args(argv)

    if not _ADR.is_dir():
        print("каталога docs/dev/adr нет — проверять нечего", file=sys.stderr)
        return 2

    problems = incomplete_records()

    if args.base:
        try:
            problems.extend(
                f"правка задним числом — {place}" for place in rewritten_decisions(args.base)
            )
        except RuntimeError as exc:
            # Третий исход: без общей базы (мелкий клон, отсутствующая ветка)
            # ответить нечем — и это не то же самое, что «правок нет».
            print(f"сверить правки не с чем: {exc}", file=sys.stderr)
            return 2

    if problems:
        print("записи о решениях неполны или переписаны:", file=sys.stderr)
        for problem in problems:
            print(f"  • {problem}", file=sys.stderr)
        print(
            "\nБез альтернатив запись — объявление, а не решение; меняется решение — "
            "заводится новый ADR, а старый помечается «Superseded by ADR-XXXX» "
            "(docs/dev/adr/README.md).",
            file=sys.stderr,
        )
        return 1

    history = "правок задним числом нет" if args.base else "история ветки не сверялась"
    print(f"записей ADR: {len(_records())}; у каждой статус и альтернативы, {history}")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
