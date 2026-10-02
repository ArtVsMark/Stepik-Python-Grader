#!/usr/bin/env python3
"""scripts/check_showcase_links.py — из оригинала в его копию не ссылаются.

Правило 089 каталога и инвариант №6 CLAUDE.md: внутренняя база глоссария —
источник истины, внешний [Glossary-Python](https://github.com/ArtVsMark/Glossary-Python)
— только витрина. Связь односторонняя, и это касается не только полноты, но и
**ссылок**: ссылка из оригинала в его копию уводит читателя на заведомо более
старое содержимое. Наружу из карточки ведёт единственный адрес — `docs_url` на
официальный `docs.python.org`.

Проверяется то, что видит пользователь: данные карточек, строки локалей и
разметка веб-слоя. Проза, объясняющая устройство (docstring, комментарий,
документация), под запрет не попадает — там витрину как раз и надо называть,
иначе следующий разбор начнётся с вопроса «а почему нельзя».

Запуск::

    python scripts/check_showcase_links.py
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import sys

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["SHOWCASE_URL", "main", "showcase_links", "strip_comments"]

_ROOT = pathlib.Path(__file__).parent.parent

#: Адрес витрины. Именно URL, а не имя: имя законно встречается в прозе.
SHOWCASE_URL = "github.com/ArtVsMark/Glossary-Python"

#: Что считается «тем, что видит пользователь».
_DATA = ("src/stepik_grader/glossary/data", "src/stepik_grader/locales")
_MARKUP = ("src/stepik_grader/web/static",)
_MARKUP_SUFFIXES = (".html", ".js", ".css")

_LINE_COMMENT = re.compile(r"^\s*(//|/\*|\*)")


def strip_comments(text: str) -> str:
    """Убрать построчные комментарии JS/CSS: объяснять устройство ими можно."""
    kept: list[str] = []
    inside_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if inside_block:
            if "*/" in stripped:
                inside_block = False
            continue
        if stripped.startswith("/*"):
            inside_block = "*/" not in stripped
            continue
        if _LINE_COMMENT.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def showcase_links(root: pathlib.Path | None = None) -> list[str]:
    """Места, где оригинал ссылается на свою витрину."""
    base = root or _ROOT
    found: list[str] = []

    for directory in _DATA:
        for path in sorted((base / directory).rglob("*.json")):
            text = path.read_text(encoding="utf-8")
            if SHOWCASE_URL not in text:
                continue
            # Ключи-комментарии в данных законны; ищем значение, которое уедет
            # пользователю. Разбор JSON, а не подстрока: комментарий в JSON
            # живёт ключом, и отличить его от значения можно только структурой.
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                found.append(f"{path.relative_to(base)}: ссылка на витрину (файл не разобран)")
                continue
            for key, value in _walk(data):
                if isinstance(value, str) and SHOWCASE_URL in value and not key.startswith("_"):
                    found.append(f"{path.relative_to(base)}: поле {key} ведёт на витрину")

    for directory in _MARKUP:
        for path in sorted((base / directory).rglob("*")):
            if path.suffix not in _MARKUP_SUFFIXES or not path.is_file():
                continue
            if SHOWCASE_URL in strip_comments(path.read_text(encoding="utf-8")):
                found.append(f"{path.relative_to(base)}: разметка ведёт на витрину")

    return found


def _walk(node: object, key: str = "") -> list[tuple[str, object]]:
    """Пары (ключ, значение) вглубь структуры — для поиска по значениям."""
    if isinstance(node, dict):
        pairs: list[tuple[str, object]] = []
        for name, value in node.items():
            pairs.extend(_walk(value, str(name)))
        return pairs
    if isinstance(node, list):
        pairs = []
        for item in node:
            pairs.extend(_walk(item, key))
        return pairs
    return [(key, node)]


def main() -> int:
    """0 — оригинал не ссылается на копию; 1 — ссылается."""
    found = showcase_links()
    if found:
        print("оригинал ссылается на свою витрину:", file=sys.stderr)
        for place in found:
            print(f"  • {place}", file=sys.stderr)
        print(
            "\nАдрес карточки — её id как якорь своего раздела (#/glossary/<id>); "
            "наружу ведёт только docs_url на docs.python.org.",
            file=sys.stderr,
        )
        return 1

    print("оригинал на витрину не ссылается: данные карточек, локали и разметка чисты")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
