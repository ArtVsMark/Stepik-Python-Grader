#!/usr/bin/env python3
"""scripts/check_python_setup.py — работа зовёт python только после setup-python.

Версию прогона объявляют не только числом. Работа без шага ``setup-python``
числа не объявляет вовсе, а ``python`` в ней есть — тот, что пришёл с образом
раннера. Так жил ``release.yml``: джоб ``github-release`` извлекал заметки к
выпуску системным python раннера, и ни одна проверка этого не спрашивала —
код выпуска исполнялся версией, на которой его никто не проверял (issue #1570,
правило каталога 217).

**Что считается вызовом.** Строка внутри ``run:``, где первым словом команды
стоит ``python``, ``python3``, ``pip``, ``pip3``, ``pytest``, ``mypy`` или
``ruff`` (после ``&&``, ``;``, ``|`` или ``(`` — тоже). Работа в контейнере с
образом ``python:`` получает интерпретатор от образа — она законна.

**Что гейт не делает.** Не сверяет, какая версия поставлена: это видно в самом
шаге и держится планкой. Предмет один — вызов не раньше установки.

Исходы (правило 039): 0 — чисто; 1 — есть находки; 2 — проверка не отработала
(каталог workflow не прочитан или в нём нет ни одного файла).
"""

import argparse
import contextlib
import re
import sys
from pathlib import Path

__all__ = [
    "EXIT_BROKEN",
    "EXIT_FINDINGS",
    "EXIT_OK",
    "findings",
    "jobs",
    "main",
]

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_BROKEN = 2

#: Заголовок работы: ровно два пробела отступа под ``jobs:``.
_JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$", re.MULTILINE)
_SETUP_RE = re.compile(r"uses:\s*actions/setup-python@")
_CONTAINER_RE = re.compile(r"^\s{4}container:.*?^\s{6}image:\s*python:", re.MULTILINE | re.DOTALL)
#: Команда, исполняемая интерпретатором: первым словом строки или после
#: разделителя команд оболочки.
_CALL_RE = re.compile(r"(?:^|&&|;|\||\()\s*(?:python3?|pip3?|pytest|mypy|ruff)(?:\s|$)")


def jobs(text: str) -> dict[str, str]:
    """Тела работ файла workflow по именам — чистая функция."""
    start = text.find("\njobs:")
    if start < 0:
        return {}
    body = text[start + 1 :]
    heads = list(_JOB_RE.finditer(body))
    return {
        head.group(1): body[head.end() : heads[i + 1].start() if i + 1 < len(heads) else len(body)]
        for i, head in enumerate(heads)
    }


def _first_call(job: str) -> int:
    """Смещение первой строки ``run:``-блока, зовущей интерпретатор; ``-1`` — нет."""
    offset = 0
    inside = False
    run_indent = 0
    for line in job.splitlines(keepends=True):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if inside and stripped and indent <= run_indent:
            inside = False
        if re.match(r"^-?\s*run:", stripped):
            inside = True
            run_indent = indent
            inline = stripped.split("run:", 1)[1].strip()
            if inline and inline not in {"|", ">", "|-", ">-"} and _CALL_RE.search(inline):
                return offset
        elif inside and not stripped.startswith("#") and _CALL_RE.search(stripped):
            return offset
        offset += len(line)
    return -1


def findings(workflows: dict[str, str]) -> list[str]:
    """Работы, где интерпретатор зовётся раньше ``setup-python``: ``файл: работа``."""
    found: list[str] = []
    for name, text in sorted(workflows.items()):
        for job, body in jobs(text).items():
            call = _first_call(body)
            if call < 0 or _CONTAINER_RE.search(body):
                continue
            setup = _SETUP_RE.search(body)
            if setup is None or setup.start() > call:
                found.append(
                    f"{name}: работа {job} зовёт python раньше setup-python — код "
                    "исполняется системным python раннера, а не версией планки"
                )
    return found


def main(argv: list[str] | None = None) -> int:
    """Точка входа: 0 — чисто, 1 — находки, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    folder = args.root / ".github" / "workflows"
    try:
        workflows = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.yml"))}
    except OSError as exc:
        print(f"Проверка не отработала: {exc}", file=sys.stderr)
        return EXIT_BROKEN
    if not workflows:
        print(f"Проверка не отработала: в {folder} нет ни одного workflow", file=sys.stderr)
        return EXIT_BROKEN

    found = findings(workflows)
    print(f"Работы, зовущие python: файлов {len(workflows)}, находок {len(found)}.")
    for line in found:
        print(f"  {line}")
    return EXIT_FINDINGS if found else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
