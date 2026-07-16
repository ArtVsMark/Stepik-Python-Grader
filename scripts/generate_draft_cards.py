#!/usr/bin/env python
"""generate_draft_cards.py — черновики карточек из официальной документации.

Для каждой сущности stdlib-инвентаря (``build_stdlib_inventory``), у которой
ещё нет карточки в базе, создаёт ``GlossaryCard(status="draft")`` из
**официальной документации самого Python** — офлайн-интроспекцией
(``inspect.signature``/``inspect.getdoc``) + шаблонными ссылками на
``docs.python.org`` (issue #328, эпик #316). Сети нет: докстринги и сигнатуры
исполняемой stdlib — это и есть официальная документация.

Черновики пишутся отдельным файлом ``data/drafts.json`` (тот же каталог, что и
импортированная база — попадает в дефолтный store), статус ``draft`` их метит.
Жизненный цикл: ``draft`` → ручная редактура (RU-summary, примеры) → ``ready``.

Запуск (идемпотентно; существующие карточки не перезаписываются):

    python scripts/generate_draft_cards.py \
        --base src/stepik_grader/glossary/data \
        --out src/stepik_grader/glossary/data/drafts.json
"""

from __future__ import annotations

import argparse
import builtins
import importlib
import inspect
import json
import sys
from pathlib import Path

# Скрипт в scripts/ (не на sys.path пакета) — добавим src/ для импортов.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stepik_grader.glossary.coverage import missing_entries_from_inventory  # noqa: E402
from stepik_grader.glossary.json_provider import JsonGlossaryProvider  # noqa: E402
from stepik_grader.glossary.models import GlossaryCard  # noqa: E402
from stepik_grader.glossary.stdlib_inventory import (  # noqa: E402
    StdlibItem,
    build_stdlib_inventory,
)

__all__ = [
    "docs_url_for",
    "draft_card",
    "main",
    "resolve_object",
    "run_generate",
    "section_for",
]

_DOCS = "https://docs.python.org/3/library"

# Раздел карточки по типу/модулю — зеркалит имена разделов импортированной базы,
# чтобы черновики попадали под те же чипы-фильтры раздела «Глоссарий» (#329).
_TYPE_SECTIONS: dict[str, str] = {
    "str": "Строки (str)",
    "list": "Списки (list)",
    "tuple": "Кортежи (tuple)",
    "dict": "Словари (dict)",
    "set": "Множества (set)",
    "frozenset": "Множества (set)",
    "bytes": "Байты (bytes)",
    "bytearray": "Байты (bytes)",
    "int": "Числа и математика",
    "float": "Числа и математика",
    "complex": "Числа и математика",
}

# InventoryKind → GlossaryCard.kind (у карточки нет "method"/"class").
_CARD_KIND: dict[str, str] = {
    "exception": "exception",
    "function": "function",
    "method": "function",
    "class": "term",
}


def resolve_object(item: StdlibItem) -> object | None:
    """Разрешить ``StdlibItem`` в живой объект Python (или None, если не вышло)."""
    try:
        if item.kind == "method":
            type_name, method_name = item.qualname.split(".", 1)
            return getattr(getattr(builtins, type_name), method_name)
        if item.module == "builtins":
            return getattr(builtins, item.qualname)
        module = importlib.import_module(item.module)
        name = item.qualname[len(item.module) + 1 :]
        return getattr(module, name)
    except (ImportError, AttributeError, ValueError):
        return None


def docs_url_for(item: StdlibItem) -> str:
    """Шаблонная ссылка на официальную документацию по типу сущности."""
    qualname = item.qualname
    if item.kind == "method":
        return f"{_DOCS}/stdtypes.html#{qualname}"
    if item.kind == "exception":
        if item.module == "builtins":
            return f"{_DOCS}/exceptions.html#{qualname}"
        return f"{_DOCS}/{item.module}.html#{qualname}"
    if item.module == "builtins":
        return f"{_DOCS}/functions.html#{qualname}"
    return f"{_DOCS}/{item.module}.html#{qualname}"


def section_for(item: StdlibItem) -> str:
    """RU-раздел карточки (зеркалит разделы импортированной базы)."""
    if item.kind == "method":
        type_name = item.qualname.split(".", 1)[0]
        return _TYPE_SECTIONS.get(type_name, "Методы типов")
    if item.kind == "exception":
        return "Исключения"
    if item.module == "builtins":
        return "Встроенные функции"
    return f"Модуль {item.module}"


def _syntax_for(obj: object, qualname: str) -> str:
    """Сигнатура: ``inspect.signature`` или первая строка docstring как fallback."""
    name = qualname.rsplit(".", 1)[-1]
    try:
        return f"{name}{inspect.signature(obj)}"  # type: ignore[arg-type]
    except (ValueError, TypeError):
        pass
    doc = inspect.getdoc(obj) or ""
    first = doc.split("\n", 1)[0].strip()
    # C-функции часто кладут сигнатуру первой строкой ("reduce(function, ...) -> ...").
    head = first.split("(", 1)[0]
    if "(" in first and head.isidentifier():
        return first
    return ""


def _body_for(obj: object) -> str:
    """Первый абзац docstring (EN, черновой источник для редактуры)."""
    doc = inspect.getdoc(obj) or ""
    return doc.split("\n\n", 1)[0].strip()


def draft_card(item: StdlibItem) -> GlossaryCard:
    """Собрать ``GlossaryCard(status="draft")`` из интроспекции сущности.

    ``id`` = qualname (исключения — в нижнем регистре, конвенция анкоров), что
    делает карточку зачётной для coverage полного qualname (issue #327).
    ``summary`` намеренно пуст — его (RU-однострочник) заполняет редактор при
    промоции ``draft`` → ``ready``.
    """
    obj = resolve_object(item)
    body = _body_for(obj) if obj is not None else ""
    syntax = _syntax_for(obj, item.qualname) if obj is not None else ""
    card_id = item.qualname.lower() if item.kind == "exception" else item.qualname
    return GlossaryCard.from_dict(
        {
            "id": card_id,
            "title": item.qualname,
            "kind": _CARD_KIND.get(item.kind, "term"),
            "summary": "",
            "body": body,
            "syntax": syntax,
            "status": "draft",
            "docs_url": docs_url_for(item),
            "section": section_for(item),
            "tags": ["autodraft"],
        }
    )


def run_generate(base_dir: Path, out_file: Path) -> int:
    """Сгенерировать/обновить черновики недостающих сущностей в ``out_file``.

    Идемпотентно: существующие карточки базы и уже написанные черновики
    (в т.ч. отредактированные вручную) не перезаписываются. Возвращает итоговое
    число черновиков в ``out_file``.
    """
    base = JsonGlossaryProvider.from_directory(base_dir)
    existing_ids = {card.id for card in base.all()}
    known = base.known_terms()

    inventory = build_stdlib_inventory()
    by_qualname = {item.qualname: item for item in inventory}
    missing = missing_entries_from_inventory(inventory, known=known)

    # Сохранить уже написанные черновики (ручные правки) как есть.
    preserved: dict[str, GlossaryCard] = {}
    if out_file.exists():
        for card in JsonGlossaryProvider.from_file(out_file).all():
            preserved[card.id] = card

    cards: dict[str, GlossaryCard] = dict(preserved)
    for entry in missing:
        item = by_qualname[entry.qualname]
        card = draft_card(item)
        # Защита от дубля id с не-черновой карточкой (иначе from_directory упадёт).
        if card.id in existing_ids and card.id not in preserved:
            continue
        cards.setdefault(card.id, card)

    ordered = sorted(cards.values(), key=lambda c: c.id)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps([c.to_dict() for c in ordered], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(ordered)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--base <dir> --out <file>`` → генерация черновиков, печать сводки."""
    default_base = _SRC / "stepik_grader" / "glossary" / "data"
    parser = argparse.ArgumentParser(
        description="Генерация draft-карточек из официальной документации (офлайн)."
    )
    parser.add_argument("--base", type=Path, default=default_base, help="Каталог базы карточек")
    parser.add_argument(
        "--out", type=Path, default=default_base / "drafts.json", help="Файл черновиков"
    )
    args = parser.parse_args(argv)
    if not args.base.is_dir():
        parser.error(f"База не найдена: {args.base}")

    total = run_generate(args.base, args.out)
    print(f"Черновиков в {args.out}: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
