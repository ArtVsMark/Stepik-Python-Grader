#!/usr/bin/env python3
"""scripts/check_catalogue_trails.py — след каталога в НАШЕ дерево разрешается.

Правило 185 каталога: след записи — это **адрес** в чужом репозитории, а не
проза. Он не переводится вместе с текстом и не пересказывается; когда названный
документ правят, след поправляет **та сторона, чей документ**, — она одна знает
о правке в момент правки.

Наша сторона этого правила стояла без механизма. Каталог ссылается на разделы
`CLAUDE.md`, `CONTRIBUTING.md` и `docs/`; переименование раздела проходит у нас
зелёным, а след обрывается молча — узнать об этом можно было только из чужого
прогона, то есть никогда.

Замер на выгрузке 1.5 (09.09.2026): следов-документов в наше дерево **105**,
не разрешаются **15**. Три вида поломки, и все три вида нужны разом:

* **путь пережил переезд** — `core/tracer.py`, `cli/__init__.py`,
  `core/history.py`, `core/submission_archive.py`: пакет уехал в src-layout, а
  адреса остались от прежнего дерева;
* **раздел переименован** — `§ Комплексный issue ведёт чек-лист` против живого
  `§ Комплексный issue ведёт чек-лист находок`;
* **имя раздела обрезано** — `§ Соглашения. Смежное:`, `§ Ещё два урока —`,
  `§ Что прощается / §`: след оборван на полуслове при переносе.

**Это предупреждение, а не отказ, и намеренно.** Предмет живёт в ЧУЖОМ
репозитории: чинится он там, а красный гейт у нас останавливал бы нашу работу
из-за состояния, которое мы правим не своей рукой. Правило 142 (у находки
обязан быть адресат) выполняется иначе — находка уезжает в задачу ночного
обхода, откуда её видно тому, кто отправляет предложения каталогу.

Три исхода (правило 039) разведены: «чисто», «следы оборваны» и «сверить не
удалось» — последнее означает недоступный клон каталога, а не порядок в следах.

Запуск::

    python scripts/check_catalogue_trails.py --catalogue <клон каталога>
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

_ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "PROJECT",
    "Broken",
    "broken_trails",
    "headings",
    "main",
    "our_trails",
]

#: Как этот проект назван в следах каталога.
PROJECT = "ArtVsMark/Stepik-Python-Grader"

EXIT_OK = 0
#: Сверить не удалось: клона нет, выгрузка не читается. Не «следы в порядке».
EXIT_UNKNOWN = 2


class Broken(NamedTuple):
    """Оборванный след: правило, адрес и что именно не нашлось."""

    rule: str
    doc: str
    section: str | None
    why: str

    def line(self) -> str:
        """Строка отчёта — адрес целиком, чтобы правку можно было сделать сразу."""
        where = f"{self.doc} § {self.section}" if self.section else self.doc
        return f"  правило {self.rule}: {where} — {self.why}"


def our_trails(export: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Следы-документы, указывающие в наше дерево.

    Форма ``{repo, issue}`` сюда не попадает: задача — состояние трекера, а не
    адрес в дереве, и переименованием раздела её не сломать.

    Args:
        export: Разобранная выгрузка ``export/rules.json``.

    Returns:
        Пары «идентификатор правила, след».
    """
    found: list[tuple[str, dict[str, Any]]] = []
    for rule in export.get("rules", []):
        if not isinstance(rule, dict):
            continue
        for trail in rule.get("trails") or []:
            if not isinstance(trail, dict) or "doc" not in trail:
                continue
            if str(trail.get("repo", "")) != PROJECT:
                continue
            found.append((str(rule.get("id", "?")), trail))
    return found


def headings(text: str) -> list[str]:
    """Заголовки markdown-документа без решёток и украшений."""
    return [
        re.sub(r"^#+\s*", "", line).strip() for line in text.splitlines() if line.startswith("#")
    ]


def broken_trails(export: dict[str, Any], *, root: Path | None = None) -> list[Broken]:
    """Следы каталога, которые в нашем дереве не разрешаются.

    Раздел ищется **вхождением**, а не равенством: каталог законно сокращает
    длинный заголовок, и требовать дословного совпадения значило бы объявлять
    поломкой корректный след.

    Args:
        export: Разобранная выгрузка каталога.
        root: Корень репозитория; ``None`` — свой собственный.

    Returns:
        Список оборванных следов; пустой — все разрешились.
    """
    base = root if root is not None else _ROOT
    broken: list[Broken] = []
    for rule_id, trail in our_trails(export):
        doc = str(trail.get("doc", ""))
        section = trail.get("section")
        section = str(section) if section else None
        path = base / doc
        if not path.is_file():
            broken.append(Broken(rule_id, doc, section, "документа нет по этому адресу"))
            continue
        if section is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover — файл есть, но не читается
            broken.append(Broken(rule_id, doc, section, f"документ не прочитан ({exc})"))
            continue
        if not any(section.lower() in head.lower() for head in headings(text)):
            broken.append(Broken(rule_id, doc, section, "такого раздела в документе нет"))
    return broken


def _load_export(catalogue: Path) -> dict[str, Any] | None:
    """Выгрузка правил каталога; ``None`` — прочитать не удалось."""
    try:
        data = json.loads((catalogue / "export" / "rules.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    """0 — сверено (находки печатаются предупреждением); 2 — сверить не удалось."""
    parser = argparse.ArgumentParser(
        prog="python scripts/check_catalogue_trails.py",
        description="След каталога правил в наше дерево обязан разрешаться.",
    )
    parser.add_argument("--catalogue", type=Path, required=True, help="клон каталога правил")
    args = parser.parse_args(argv)

    export = _load_export(args.catalogue)
    if export is None:
        print(
            f"Сверить не удалось: выгрузка правил не прочитана в {args.catalogue}. "
            "Это не «следы в порядке» — предмет проверки недоступен.",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    trails = our_trails(export)
    broken = broken_trails(export)
    print(f"Следы каталога в наше дерево: {len(trails)}, не разрешаются: {len(broken)}")
    if not broken:
        return EXIT_OK

    for item in broken:
        print(item.line())
    print(
        f"::warning::оборванных следов: {len(broken)}. След — адрес в нашем дереве, и "
        "правит его наша сторона: переименование раздела проходит здесь зелёным, а "
        "ссылка каталога обрывается молча. Чинится предложением в каталог."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
