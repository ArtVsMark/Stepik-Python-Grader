"""scripts/glossary_proposals.py — предложения к содержанию Glossary-Python.

Глоссарий ведётся в ArtVsMark/Glossary-Python, здесь лежит его копия
(инвариант 6). Но часть пробелов видна только потребителю. Проверка
совместимости со Stepik сверяет код решения с полями ``added``/``removed``
карточек и упирается в то, чего в данных нет. Этот скрипт отдаёт такие пробелы
обратно файлом ``.glossary/proposals.json``. Glossary-Python читает его обычным
HTTPS, как каталог правил читает ``.rules/proposals.json``: ни токена, ни прав
на запись к соседу.

ЧТО СЧИТАЕТСЯ ПРЕДЛОЖЕНИЕМ — четыре вида, каждый выводится из данных:

1. ``syntax-card`` — признак синтаксиса из ``core/stepik_compat.py`` без
   карточки, либо карточка есть, а её ``added`` расходится с версией признака.
2. ``module-card`` — живой модуль stdlib, у членов которого карточки есть, а у
   него самого нет. Тогда версию модуля приходится выводить из членов.
3. ``builtin-card`` — встроенное имя без карточки.
4. ``id-convention`` — одно предложение на все карточки встроенных имён, чей id
   отличается от самого имени (исключения в нижнем регистре при заголовке
   ``ExceptionGroup``).

У каждого предложения стабильный ``slug`` ``<вид>:<предмет>``. По нему
Glossary-Python может вынести вердикт, не сверяя текст.

ЧЕГО ЗДЕСЬ НЕТ. Вердиктов: где и как их публикует глоссарий, решает он. Правок
``glossary/data/``: эти файлы по-прежнему приезжают импортом.

Запуск::

    python scripts/glossary_proposals.py           # пересобрать файл
    python scripts/glossary_proposals.py --check   # файл == сборка

Коды выхода: 0 — записано или совпадает; 1 — ``--check`` нашёл расхождение.
"""

import argparse
import builtins
import contextlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stepik_grader.core import stepik_compat
from stepik_grader.glossary import BUNDLED_GLOSSARY_DIR
from stepik_grader.glossary.json_provider import JsonGlossaryProvider

__all__ = ["OUTPUT", "build", "main"]

# Печатает по-русски: в консоли cp1251/cp866 без этого упадёт на первом же print.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

OUTPUT = Path(__file__).resolve().parent.parent / ".glossary" / "proposals.json"

SCHEMA = "1.0"
PRODUCER = "ArtVsMark/Stepik-Python-Grader"
SOURCE = "ArtVsMark/Glossary-Python"

_REF = "https://docs.python.org/3/reference"
_WHATSNEW = "https://docs.python.org/3/whatsnew"


@dataclass(frozen=True)
class SyntaxCard:
    """Чем описать признак синтаксиса, если карточки нет, и где она уже есть."""

    title: str
    title_en: str
    pep: str
    docs_url: str
    card_id: str | None = None


# Признак → карточка. Ключи обязаны совпадать с SYNTAX_FEATURES: расхождение —
# отказ сборки, а не молчаливый пропуск признака.
SYNTAX_CARDS: dict[str, SyntaxCard] = {
    "fstring": SyntaxCard(
        "f-строки", "f-strings", "PEP 498", f"{_REF}/lexical_analysis.html#f-strings", "f-строки"
    ),
    "var_annotation": SyntaxCard(
        "Аннотация переменной (x: int = 1)",
        "Variable annotation (x: int = 1)",
        "PEP 526",
        f"{_REF}/simple_stmts.html#annotated-assignment-statements",
    ),
    "async_generator": SyntaxCard(
        "Асинхронный генератор",
        "Asynchronous generator",
        "PEP 525",
        f"{_REF}/expressions.html#asynchronous-generator-functions",
    ),
    "walrus": SyntaxCard(
        "Оператор :=",
        "Walrus operator :=",
        "PEP 572",
        f"{_REF}/expressions.html#assignment-expressions",
        "walrus-operator",
    ),
    "posonly": SyntaxCard(
        "Только позиционные параметры (/)",
        "Positional-only parameters (/)",
        "PEP 570",
        f"{_WHATSNEW}/3.8.html#positional-only-parameters",
    ),
    "match": SyntaxCard(
        "match / case",
        "match / case",
        "PEP 634",
        f"{_REF}/compound_stmts.html#the-match-statement",
        "match-case",
    ),
    "except_star": SyntaxCard(
        "except*",
        "except*",
        "PEP 654",
        f"{_REF}/compound_stmts.html#except-star",
    ),
    "star_index": SyntaxCard(
        "Распаковка * в индексе (a[*b])",
        "Star unpacking in an index (a[*b])",
        "PEP 646",
        f"{_WHATSNEW}/3.11.html#pep-646-variadic-generics",
    ),
    "type_statement": SyntaxCard(
        "Оператор type",
        "The type statement",
        "PEP 695",
        f"{_REF}/simple_stmts.html#the-type-statement",
    ),
    "type_params": SyntaxCard(
        "Параметры типа (class C[T])",
        "Type parameters (class C[T])",
        "PEP 695",
        f"{_REF}/compound_stmts.html#type-params",
    ),
    "tstring": SyntaxCard(
        "t-строки",
        "t-strings",
        "PEP 750",
        f"{_REF}/lexical_analysis.html#t-strings",
    ),
}


def _proposal(kind: str, subject: str, why: str, **fields: Any) -> dict[str, Any]:
    """Одно предложение: стабильный ключ, вид, предмет, обоснование и поля."""
    return {"slug": f"{kind}:{subject}", "kind": kind, "subject": subject, "why": why, **fields}


def _syntax(cards: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Признаки синтаксиса без карточки или с расходящейся версией."""
    features = {key: since for since, key, _ in stepik_compat.SYNTAX_FEATURES}
    if features.keys() != SYNTAX_CARDS.keys():
        raise SystemExit(
            "SYNTAX_CARDS расходится с stepik_compat.SYNTAX_FEATURES: "
            f"{sorted(features.keys() ^ SYNTAX_CARDS.keys())}"
        )
    for key, since in features.items():
        spec = SYNTAX_CARDS[key]
        version = stepik_compat.format_version(since)
        card = cards.get(spec.card_id) if spec.card_id else None
        if card is None:
            yield _proposal(
                "syntax-card",
                key,
                "Признака синтаксиса нет в глоссарии. Проверка совместимости со Stepik "
                "его находит, а сослаться ей не на что.",
                suggested={
                    "title": spec.title,
                    "title_en": spec.title_en,
                    "added": version,
                    "pep": spec.pep,
                    "docs_url": spec.docs_url,
                },
            )
        elif card.added != version:
            yield _proposal(
                "syntax-card",
                key,
                f"У карточки «{card.id}» added={card.added}, а признак появился в {version}.",
                card_id=card.id,
                suggested={"added": version},
            )


def _modules(cards: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Живые модули stdlib, у членов которых карточки есть, а у них самих нет."""
    members: dict[str, list[Any]] = {}
    for card_id, card in cards.items():
        head, dot, _ = card_id.partition(".")
        if dot:
            members.setdefault(head, []).append(card)
    for module in sorted(members):
        if module in cards or module not in sys.stdlib_module_names:
            continue
        own = members[module]
        known = [v for v in (stepik_compat.parse_version(c.added) for c in own) if v is not None]
        yield _proposal(
            "module-card",
            module,
            "У модуля нет своей карточки. Версию модуля проверка совместимости выводит "
            "из самой ранней карточки его членов, а это нижняя граница, а не факт.",
            evidence={
                "member_cards": len(own),
                "earliest_member_added": stepik_compat.format_version(min(known))
                if known
                else None,
            },
            suggested={
                "title": module,
                "docs_url": f"https://docs.python.org/3/library/{module}.html",
            },
        )


def _public_builtins() -> list[str]:
    """Встроенные имена, которые пишут в коде решений: без ``_`` в начале."""
    return sorted(name for name in dir(builtins) if not name.startswith("_"))


def _builtins(cards: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Встроенные имена без карточки.

    Имя считается описанным, если совпадает (без учёта регистра) с id,
    заголовком или алиасом какой-нибудь карточки: ``IOError`` законно живёт
    алиасом ``OSError``, и предлагать ему отдельную карточку — шум.
    """
    lowered: set[str] = set()
    for card in cards.values():
        labels = [card.id, card.title, card.title_en, *card.aliases]
        # «Ellipsis ...»: имя — первое слово заголовка. Слова алиасов не берутся:
        # «case True в match» не описывает True.
        labels += [
            str(title).split(maxsplit=1)[0] for title in (card.title, card.title_en) if title
        ]
        lowered.update(str(label).strip().removesuffix("()").lower() for label in labels)
    for name in _public_builtins():
        if name.lower() not in lowered:
            yield _proposal(
                "builtin-card",
                name,
                "Встроенного имени нет в глоссарии.",
                suggested={
                    "title": name,
                    "docs_url": "https://docs.python.org/3/library/functions.html",
                },
            )


def _id_convention(cards: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Одно предложение: id карточек встроенных имён, отличающиеся от имени."""
    names = {name.lower(): name for name in _public_builtins()}
    mismatched = [
        {"id": card_id, "name": names[card_id.lower()]}
        for card_id in sorted(cards)
        if card_id.lower() in names and card_id != names[card_id.lower()]
    ]
    if mismatched:
        yield _proposal(
            "id-convention",
            "builtin-names",
            "id карточки встроенного имени отличается от имени, которое пишут в коде. "
            "Поиск по имени из кода находит такую карточку, только если сверяет без "
            "учёта регистра, а якорь #/glossary/<id> не совпадает с именем. Решение "
            "за глоссарием: id = имя (старый id — в _moved) либо явное правило.",
            evidence={"cards": mismatched},
        )


def build(directory: Path = BUNDLED_GLOSSARY_DIR) -> dict[str, Any]:
    """Собрать предложения по копии глоссария; ``generated_at`` ставит запись."""
    cards = {card.id: card for card in JsonGlossaryProvider.from_directory(directory).all()}
    source = json.loads((directory / "_source.json").read_text(encoding="utf-8"))
    proposals = [
        *_syntax(cards),
        *_modules(cards),
        *_builtins(cards),
        *_id_convention(cards),
    ]
    return {
        "_": "Предложения к содержанию глоссария от потребителя. Файл собирает "
        "scripts/glossary_proposals.py из копии глоссария этого репозитория; руками "
        "не правится. Glossary-Python читает его сам, обычным HTTPS.",
        "_пусто": "Пустой список — законное состояние: предлагать нечего. "
        "Отсутствие файла означает, что канал не подключён.",
        "schema": SCHEMA,
        "schema_of": "формат ЭТОГО файла — предложения потребителя к содержанию глоссария",
        "producer": PRODUCER,
        "source": SOURCE,
        "examined": {"release": source.get("release"), "digest": source.get("digest")},
        "generated_at": "",
        "proposals": proposals,
    }


def _comparable(payload: dict[str, Any]) -> dict[str, Any]:
    """Содержание без момента сборки: по нему решается, есть ли что записать."""
    return {key: value for key, value in payload.items() if key != "generated_at"}


def main(argv: list[str] | None = None) -> int:
    """Точка входа: пересобрать файл или сверить его со сборкой."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="сверить файл со сборкой")
    args = parser.parse_args(argv)

    fresh = build()
    current = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else None
    if current is not None and _comparable(current) == _comparable(fresh):
        print(f"{OUTPUT.name}: совпадает со сборкой, предложений {len(fresh['proposals'])}.")
        return 0
    if args.check:
        print(
            f"{OUTPUT.name} расходится со сборкой по копии глоссария — "
            "пересоберите: python scripts/glossary_proposals.py"
        )
        return 1
    fresh["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUTPUT.name}: записано, предложений {len(fresh['proposals'])}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
