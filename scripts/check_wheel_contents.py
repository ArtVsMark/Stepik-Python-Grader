#!/usr/bin/env python3
"""scripts/check_wheel_contents.py — что реально уехало в колесо (issue #953).

Гейт публикуемого артефакта. Тесты пакета смотрят на **дерево исходников**
(``stepik_grader.__file__``), поэтому сломанный glob в
``[tool.setuptools.package-data]`` они не видят вовсе: pytest, ruff, mypy и
verify остаются зелёными, а на PyPI уезжает wheel без вендоренного CodeMirror
и шрифтов — у пользователя ``--serve`` открывается без редактора. Узнаётся это
из жалобы, потому что версия в PyPI неперезаписываема.

Проверяются не все файлы подряд, а те, чьё отсутствие ломает продукт молча:
статика веб-оболочки, вендор-бандл, шрифты, локали (CLI и веб), данные
глоссария и правил, маркер типов ``py.typed``.

Запуск::

    python scripts/check_wheel_contents.py dist/*.whl
    python scripts/check_wheel_contents.py dist/stepik_python_grader-1.10.0-py3-none-any.whl
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import pathlib
import sys
import zipfile

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "REQUIRED_PATTERNS",
    "check_wheel",
    "main",
    "missing_patterns",
]

# Шаблоны путей ВНУТРИ колеса (относительно корня архива). Каждый обязан
# совпасть хотя бы с одним файлом: пустое совпадение означает, что glob в
# `[tool.setuptools.package-data]` перестал что-либо собирать.
REQUIRED_PATTERNS: tuple[str, ...] = (
    "stepik_grader/py.typed",
    "stepik_grader/_build_info.json",
    "stepik_grader/core/locales/*.json",
    "stepik_grader/glossary/data/*.json",
    "stepik_grader/rules/data/*.json",
    "stepik_grader/web/static/index.html",
    "stepik_grader/web/static/app.js",
    "stepik_grader/web/static/app.css",
    "stepik_grader/web/static/fonts/*.woff2",
    "stepik_grader/web/static/vendor/*.mjs",
    "stepik_grader/web/static/locales/*.json",
)


def missing_patterns(names: list[str], patterns: tuple[str, ...] = REQUIRED_PATTERNS) -> list[str]:
    """Шаблоны, под которые в колесе не нашлось ни одного файла."""
    return [p for p in patterns if not any(fnmatch.fnmatch(name, p) for name in names)]


def check_wheel(path: pathlib.Path) -> list[str]:
    """Проверить одно колесо; вернуть список проблем (пустой — всё на месте)."""
    if not path.is_file():
        return [f"{path}: файла нет"]
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        return [f"{path}: не читается как wheel — {exc}"]

    if not names:
        return [f"{path}: архив пуст"]
    return [f"{path}: нет файлов по шаблону {pattern}" for pattern in missing_patterns(names)]


def main(argv: list[str] | None = None) -> int:
    """Проверить перечисленные колёса; 0 — все обязательные файлы на месте."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("wheels", nargs="+", help="пути к .whl (можно glob-раскрытие оболочкой)")
    args = parser.parse_args(argv)

    wheels = [pathlib.Path(name) for name in args.wheels if name.endswith(".whl")]
    if not wheels:
        print(
            "Не передано ни одного .whl — проверять нечего, а публиковать нечего тем более.",
            file=sys.stderr,
        )
        return 1

    problems: list[str] = []
    for wheel in wheels:
        problems.extend(check_wheel(wheel))

    if problems:
        print("FAIL: в колесе нет файлов, без которых продукт работает неправильно:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nПроверьте [tool.setuptools.package-data] в pyproject.toml: сломанный glob "
            "не виден ни тестам, ни линтерам — они читают дерево исходников, а не артефакт."
        )
        return 1

    print(f"OK: проверено колёс — {len(wheels)}; все обязательные файлы на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
