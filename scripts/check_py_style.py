#!/usr/bin/env python3
"""scripts/check_py_style.py — код написан на версии планки, а не только объявлен на ней.

Планка — два обещания. ``requires-python`` обещает потребителю, что пакет
заработает; то же число говорит читателю «код пишется на этой версии». Второе
не держал никто: после подъёма планки до 3.14 ``from __future__ import
annotations`` оставался в 484 файлах из 493 — переезд был бы формальным
(issue #1570, правило каталога 217).

**Требования выводятся из планки, а не вписываются (правило 005).** Каждое
знает версию, с которой оно действует; поднимется планка — гейт ужесточится
сам. Сейчас одно: с 3.14 (PEP 649/749) ``from __future__ import annotations``
лишний. ``except A, B:`` без скобок (PEP 758) и прочий синтаксис держит ruff:
его цель тоже выводится из ``requires-python``.

**Исключения — та же таблица, что у ruff.** Файлы из
``[tool.ruff.per-file-target-version]`` в ``pyproject.toml`` исполняет НЕ
интерпретатор грейдера: движок кода студента (3.12, эпик #1575), хуки окна
(системный python образа), корневой ``__init__`` и точки входа гейтов (они
отказывают словами под старым Python и обязаны им разбираться). Для них
действует их собственная цель — второго списка здесь нет.

Исходы (правило 039): 0 — чисто; 1 — есть находки; 2 — проверка не
отработала (нет планки, не прочитан ``pyproject.toml``, нет git).
"""

import argparse
import contextlib
import fnmatch
import re
import subprocess
import sys
import tomllib
from pathlib import Path

__all__ = [
    "EXIT_BROKEN",
    "EXIT_FINDINGS",
    "EXIT_OK",
    "LAZY_ANNOTATIONS",
    "findings",
    "floor",
    "main",
    "target_for",
]

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_BROKEN = 2

#: С какой версии ``from __future__ import annotations`` лишний (PEP 649/749).
LAZY_ANNOTATIONS = (3, 14)

_FUTURE = re.compile(r"^from __future__ import annotations\s*$", re.MULTILINE)
_REQUIRES = re.compile(r">=\s*(\d+)\.(\d+)")
_TARGET = re.compile(r"^py(\d)(\d+)$")


def floor(pyproject: dict) -> tuple[int, int] | None:
    """Планка из ``requires-python``; нет её — ``None`` (исход «не отработала»)."""
    spec = pyproject.get("project", {}).get("requires-python", "")
    match = _REQUIRES.search(spec)
    return (int(match.group(1)), int(match.group(2))) if match else None


def target_for(path: str, pyproject: dict, default: tuple[int, int]) -> tuple[int, int]:
    """Цель файла: своя из ``per-file-target-version``, иначе планка."""
    table = pyproject.get("tool", {}).get("ruff", {}).get("per-file-target-version", {})
    for pattern, target in table.items():
        if fnmatch.fnmatch(path, pattern):
            match = _TARGET.match(str(target))
            if match:
                return (int(match.group(1)), int(match.group(2)))
    return default


def findings(files: dict[str, str], pyproject: dict, default: tuple[int, int]) -> list[str]:
    """Нарушения стиля планки: ``путь: что`` — чистая функция."""
    found: list[str] = []
    for path, text in sorted(files.items()):
        target = target_for(path, pyproject, default)
        if target >= LAZY_ANNOTATIONS and _FUTURE.search(text):
            found.append(f"{path}: `from __future__ import annotations` лишний с Python 3.14")
    return found


def _tracked(root: Path) -> dict[str, str]:
    """Отслеживаемые ``.py`` — то, что поедет, а не то, что лежит рядом."""
    done = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return {
        name: (root / name).read_text(encoding="utf-8")
        for name in done.stdout.split("\0")
        if name and (root / name).is_file()
    }


def main(argv: list[str] | None = None) -> int:
    """Точка входа: 0 — чисто, 1 — находки, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        pyproject = tomllib.loads((args.root / "pyproject.toml").read_text(encoding="utf-8"))
        files = _tracked(args.root)
    except (OSError, tomllib.TOMLDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Проверка не отработала: {exc}", file=sys.stderr)
        return EXIT_BROKEN
    default = floor(pyproject)
    if default is None:
        print("Проверка не отработала: нет requires-python в pyproject.toml", file=sys.stderr)
        return EXIT_BROKEN

    found = findings(files, pyproject, default)
    print(f"Стиль планки {'.'.join(map(str, default))}: файлов {len(files)}, находок {len(found)}.")
    for line in found:
        print(f"  {line}")
    return EXIT_FINDINGS if found else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
