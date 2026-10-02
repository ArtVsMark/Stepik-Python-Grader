#!/usr/bin/env python3
"""scripts/check_glossary_docs_links.py — ссылка карточки ведёт к самой карточке.

Карточка глоссария — конечная станция: студент приходит в неё из отчёта об
ошибке и оттуда же уходит в официальную документацию по ``docs_url``. Если
адрес ведёт «куда-то в ту сторону», а не к описываемому имени, поход в
документацию заканчивается поиском по странице — в момент, когда человек уже не
понимает, что происходит.

Так и было: девять карточек ``iter.json``, включая четыре функции ``itertools``
с собственными якорями, ссылались на общий термин ``glossary.html#term-generator``,
а ``pathlib.path`` — на ``functions.html#open``, страницу совсем другой функции
(issue #919, находка ``ADD-3-06``).

**Что проверяется.** Только карточки с КВАЛИФИЦИРОВАННЫМ именем вида
``module.name``: у такого имени в документации CPython есть собственный якорь,
построенный по тому же имени (Sphinx строит ``id`` директивы ``py:function`` из
полного имени). Значит адрес обязан это имя содержать. Карточки без точки в
имени сюда не попадают: у «сложения» или «срезов списка» своего якоря нет, и
общая страница для них — верный ответ, а не компромисс.

**Чего проверка НЕ делает.** Она не ходит в сеть. Гейт, зависящий от
доступности docs.python.org, краснел бы от чужого сбоя и зеленел бы от кэша —
то есть отвечал бы про сеть, а не про базу. Существование якоря проверяется
глазами при правке; форма — здесь и на каждом прогоне.

Запуск::

    python scripts/check_glossary_docs_links.py [--json]
"""

from __future__ import annotations

import argparse
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

__all__ = ["ALLOWED_GENERAL_PAGE", "DATA_DIR", "QUALIFIED_ID", "main", "misdirected_cards"]

DATA_DIR = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "glossary" / "data"

#: Имя вида ``module.name`` — у такого в документации CPython есть свой якорь.
QUALIFIED_ID = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+")

#: Карточки, чей адрес намеренно ведёт на общую страницу, и почему.
#:
#: Это не «разрешённый долг», а перечень мест, где своего якоря НЕТ: имя
#: карточки сокращено относительно документированного (``re.group`` — это метод
#: ``re.Match.group``), объект описан в разделе, а не отдельной директивой
#: (сигналы ``decimal``, утилиты ``enum``), либо документация описывает
#: родителя (подклассы ``shutil.Error``). Каждая строка проверена по
#: документации вручную; добавлять сюда, чтобы обойти проверку, нельзя — тогда
#: проверка перестанет отвечать на свой вопрос.
ALLOWED_GENERAL_PAGE: dict[str, str] = {
    "shutil.execerror": "подкласс shutil.Error, отдельной директивы нет",
    "shutil.readerror": "подкласс shutil.Error, отдельной директивы нет",
    "shutil.registryerror": "подкласс shutil.Error, отдельной директивы нет",
    "decimal.divisionimpossible": "сигналы decimal описаны разделом #signals",
    "decimal.divisionundefined": "сигналы decimal описаны разделом #signals",
    "decimal.conversionsyntax": "сигналы decimal описаны разделом #signals",
    "decimal.invalidcontext": "сигналы decimal описаны разделом #signals",
    "re.group": "документирован как re.Match.group — имя карточки сокращено",
    "re.groups": "документирован как re.Match.groups — имя карточки сокращено",
    "re.groupdict": "документирован как re.Match.groupdict — имя карточки сокращено",
    "enum.global_enum_repr": "описан разделом #utilities-and-decorators",
    "enum.global_flag_repr": "описан разделом #utilities-and-decorators",
    "enum.global_str": "описан разделом #utilities-and-decorators",
    "enum.pickle_by_enum_name": "описан разделом #utilities-and-decorators",
    "enum.pickle_by_global_name": "описан разделом #utilities-and-decorators",
    "os.times_result": "тип результата описан у самой os.times",
    "os.waitid_result": "тип результата описан у самой os.waitid",
    "file.read": "методы файла документированы как io.TextIOBase/io.IOBase",
    "file.readline": "методы файла документированы как io.TextIOBase/io.IOBase",
    "file.readlines": "методы файла документированы как io.TextIOBase/io.IOBase",
    "file.write": "методы файла документированы как io.TextIOBase/io.IOBase",
    "file.writelines": "методы файла документированы как io.TextIOBase/io.IOBase",
    "int.conjugate": "числовые методы описаны разделом #numeric-types-int-float-complex",
    "float.conjugate": "числовые методы описаны разделом #numeric-types-int-float-complex",
    "complex.conjugate": "числовые методы описаны разделом #numeric-types-int-float-complex",
    "tuple.count": "методы кортежа описаны разделом #common-sequence-operations",
    "tuple.index": "методы кортежа описаны разделом #common-sequence-operations",
}


def misdirected_cards(directory: pathlib.Path | None = None) -> list[tuple[str, str]]:
    """Карточки с квалифицированным именем, чей ``docs_url`` его не содержит.

    Args:
        directory: каталог с JSON-файлами базы; по умолчанию — встроенная база.

    Returns:
        Пары ``(идентификатор, адрес)``, отсортированные по идентификатору.
    """
    root = DATA_DIR if directory is None else directory
    found: list[tuple[str, str]] = []
    for path in sorted(root.glob("*.json")):
        cards = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or "")
            url = str(card.get("docs_url") or "")
            if not url or not QUALIFIED_ID.fullmatch(card_id):
                continue
            if card_id in ALLOWED_GENERAL_PAGE:
                continue
            if card_id.lower() not in url.lower():
                found.append((card_id, url))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если каждая ссылка ведёт к описываемому имени."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    # Правило каталога 039: у гейта прогоняются ВСЕ объявленные исходы, а не
    # только зелёный. Без этого флага красный путь нечем вызвать — база в
    # репозитории проходит проверку, и «отказ» существовал бы лишь в коде.
    parser.add_argument(
        "--data-dir",
        type=pathlib.Path,
        default=None,
        help="каталог с JSON-файлами базы (по умолчанию — встроенная)",
    )
    args = parser.parse_args(argv)

    bad = misdirected_cards(args.data_dir)
    if args.json:
        print(
            json.dumps(
                {"misdirected": len(bad), "cards": [name for name, _ in bad]},
                ensure_ascii=False,
                indent=1,
            )
        )
    if bad:
        print(f"FAIL: ссылка ведёт мимо описываемого имени — {len(bad)} карточек.", file=sys.stderr)
        for name, url in bad[:20]:
            print(f"  - {name}: {url}", file=sys.stderr)
        print(
            "\nЛибо поправьте адрес на якорь этого имени, либо — если своего "
            "якоря в документации нет — внесите карточку в ALLOWED_GENERAL_PAGE "
            "с причиной.",
            file=sys.stderr,
        )
        return 1

    if not args.json:
        print(
            f"Ссылки карточек: проверено имён с точкой, исключений "
            f"{len(ALLOWED_GENERAL_PAGE)} — все с причиной."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
