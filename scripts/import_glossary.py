"""scripts/import_glossary.py — карточки глоссария приезжают выгрузкой Glossary-Python.

Содержание глоссария ведётся в ArtVsMark/Glossary-Python (решение владельца,
Glossary-Python#76); здесь — копия, которую не правят руками (issue #1573).
Издатель считает, потребитель читает: к каждому выпуску ``vX.Y.0`` глоссарий
прикладывает неизменяемые ``delivery.json`` и ``delivery.schema.json``, а этот
скрипт раскладывает ``groups`` по файлам ``glossary/data/<группа>.json`` без
преобразования схемы.

ЗАКРЕПЛЕНИЕ, А НЕ «ПОСЛЕДНЕЕ». Читается закреплённый выпуск, записанный в
``glossary/data/_source.json``, а не подвижный ``badges/delivery.json``: смена
формы карточки на стороне глоссария становится здесь явным изменением, а не
поломкой, которую замечают по пустому разделу.

ЧЕТЫРЕ ПРОВЕРКИ ДО ЗАПИСИ — каждая с собственным отказом:

1. **Форма.** Мажор ``form`` выгрузки обязан совпадать с ``ACCEPTED_FORM``:
   иначе «контракт глоссария сменился: vA → vB, обновите импорт». Минор выше —
   законен (новое необязательное поле, о котором импорт вправе не знать).
2. **Схема.** Выгрузка сверяется с приложенной к выпуску ``delivery.schema.json``
   проверщиком подмножества JSON Schema 2020-12 (``jsonschema`` не зависимость
   проекта). Незнакомое ключевое слово — отказ, а не пропуск: новая схема,
   проверенная наполовину, хуже непроверенной — она выглядит проверенной.
3. **Перенаправления.** У каждого «старый id → новый» из ``moved`` новый id
   есть в выгрузке, старого — нет.
4. **Чтение без потерь.** Каждая карточка проходит ``GlossaryCard.from_dict`` и
   ``to_dict`` и возвращается побайтно той же. Это главный сторож: на форме 6.0
   прежний загрузчик не падал, а молча превращал ``{"ru": …}`` в строку — такая
   поломка видна только сравнением.

Запуск::

    python scripts/import_glossary.py                       # закреплённый выпуск из сети
    python scripts/import_glossary.py --release v1.3.0      # перезакрепить на другой выпуск
    python scripts/import_glossary.py --file d.json --schema s.json --release v1.2.0
    python scripts/import_glossary.py --check               # дерево == импорт закреплённого

Коды выхода: 0 — записано/совпадает; 1 — ``--check`` нашёл расхождение;
2 — выгрузку получить не удалось (сеть, файл); 3 — выгрузка отвергнута
(форма, схема, перенаправления, чтение с потерями).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "ACCEPTED_FORM",
    "EXIT_CHECK_FAILED",
    "EXIT_FETCH_FAILED",
    "EXIT_REJECTED",
    "SOURCE_FILE_NAME",
    "DeliveryRejected",
    "main",
    "render_files",
    "schema_errors",
    "verify_delivery",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = _ROOT / "src" / "stepik_grader" / "glossary" / "data"

REPO = "ArtVsMark/Glossary-Python"
_RELEASE_URL = "https://github.com/{repo}/releases/download/{tag}/{name}"
_TIMEOUT_S = 30

#: Мажор формы карточки, который понимает ``GlossaryCard`` (форма 6.0:
#: ``examples`` блоками, ``added``/``deprecated``/``removed``, ``platforms``,
#: двуязычные ``title``/``subcat``). Поднимается вместе с правкой модели.
ACCEPTED_FORM = 6

#: Служебные файлы каталога данных: имя с ``_`` провайдер карточками не читает.
SOURCE_FILE_NAME = "_source.json"
MOVED_FILE_NAME = "_moved.json"

EXIT_CHECK_FAILED = 1
EXIT_FETCH_FAILED = 2
EXIT_REJECTED = 3

#: Предел перечисления находок в отказе: сводка, а не простыня.
_ERRORS_SHOWN = 20


class DeliveryRejected(ValueError):
    """Выгрузка получена, но импортировать её нельзя — с перечнем причин."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# ---------------------------------------------------------------------------
# Подмножество JSON Schema 2020-12 — ровно то, чем пользуется схема выгрузки
# ---------------------------------------------------------------------------

#: Ключевые слова-аннотации: на результат проверки не влияют. ``format`` по
#: стандарту 2020-12 — аннотация, если валидатор не объявил иного.
_ANNOTATIONS = frozenset({"$schema", "$id", "$comment", "title", "description", "format", "$defs"})

#: Ключевые слова, которые проверщик исполняет. Остальное — отказ.
_ASSERTIONS = frozenset(
    {
        "type",
        "required",
        "properties",
        "additionalProperties",
        "const",
        "enum",
        "items",
        "minItems",
        "uniqueItems",
        "minLength",
        "minimum",
        "pattern",
        "propertyNames",
        "$ref",
    }
)

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "object": (dict,),
    "array": (list,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_ok(value: Any, name: str) -> bool:
    """Соответствует ли значение типу JSON Schema; ``bool`` не число."""
    if name in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    """Локальная ссылка ``#/$defs/name`` → подсхема; иная — незнакомое."""
    if not ref.startswith("#/"):
        raise DeliveryRejected([f"схема: внешняя ссылка {ref!r} не поддерживается импортом"])
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise DeliveryRejected([f"схема: ссылка {ref!r} никуда не ведёт"])
        node = node[part]
    if not isinstance(node, dict):
        raise DeliveryRejected([f"схема: ссылка {ref!r} ведёт не на схему"])
    return node


def _check(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> Iterator[str]:
    """Находки проверки значения подсхемой — по одной на нарушение."""
    unknown = set(schema) - _ASSERTIONS - _ANNOTATIONS
    if unknown:
        raise DeliveryRejected(
            [
                f"схема: незнакомые ключевые слова {sorted(unknown)} в {path or '/'} — "
                "проверщик импорта их не исполняет, обновите его"
            ]
        )
    if "$ref" in schema:
        yield from _check(value, _resolve_ref(schema["$ref"], root), root, path)
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, name) for name in names):
            yield f"{path or '/'}: ожидался тип {'|'.join(names)}, получено {type(value).__name__}"
            return  # дальнейшие проверки на чужом типе только множат шум
    if "const" in schema and value != schema["const"]:
        yield f"{path or '/'}: ожидалось {schema['const']!r}, получено {value!r}"
    if "enum" in schema and value not in schema["enum"]:
        yield f"{path or '/'}: {value!r} не из {schema['enum']!r}"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            yield f"{path or '/'}: короче {schema['minLength']} символов"
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            yield f"{path or '/'}: {value!r} не подходит под {schema['pattern']!r}"
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        yield f"{path or '/'}: {value} меньше {schema['minimum']}"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            yield f"{path or '/'}: меньше {schema['minItems']} элементов"
        if schema.get("uniqueItems"):
            seen = [json.dumps(item, sort_keys=True) for item in value]
            if len(seen) != len(set(seen)):
                yield f"{path or '/'}: элементы повторяются"
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                yield from _check(item, schema["items"], root, f"{path}/{index}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                yield f"{path or '/'}: нет обязательного поля {key!r}"
        properties: dict[str, Any] = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        names_schema = schema.get("propertyNames")
        for key, item in value.items():
            if isinstance(names_schema, dict):
                yield from _check(key, names_schema, root, f"{path}/<{key}>")
            if key in properties:
                yield from _check(item, properties[key], root, f"{path}/{key}")
            elif extra is False:
                yield f"{path or '/'}: лишнее поле {key!r}"
            elif isinstance(extra, dict):
                yield from _check(item, extra, root, f"{path}/{key}")


def schema_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Все нарушения схемы выгрузки; пусто — выгрузка соответствует.

    Raises:
        DeliveryRejected: схема использует то, что проверщик не исполняет.
    """
    return list(_check(instance, schema, schema, ""))


# ---------------------------------------------------------------------------
# Проверки выгрузки и раскладка по файлам
# ---------------------------------------------------------------------------


def _form_major(form: object) -> int | None:
    """Мажор строки формы ``"6.0"`` → ``6``; не похоже на форму — ``None``."""
    head = str(form).split(".", 1)[0]
    return int(head) if head.isdigit() else None


def verify_delivery(delivery: dict[str, Any], schema: dict[str, Any] | None) -> None:
    """Четыре проверки модульного докстринга; молча — значит, импортировать можно.

    Raises:
        DeliveryRejected: со всеми найденными причинами.
    """
    major = _form_major(delivery.get("form"))
    if major != ACCEPTED_FORM:
        raise DeliveryRejected(
            [
                f"контракт глоссария сменился: v{ACCEPTED_FORM} → v{delivery.get('form')}, "
                "обновите импорт (GlossaryCard и ACCEPTED_FORM в scripts/import_glossary.py)"
            ]
        )
    if schema is not None:
        found = schema_errors(delivery, schema)
        if found:
            shown = found[:_ERRORS_SHOWN]
            more = len(found) - len(shown)
            raise DeliveryRejected(
                [f"схема выпуска: {item}" for item in shown]
                + ([f"…и ещё {more} нарушений"] if more else [])
            )

    groups: dict[str, list[dict[str, Any]]] = delivery["groups"]
    ids = {card["id"] for cards in groups.values() for card in cards}
    reasons: list[str] = []
    for old, new in (delivery.get("moved") or {}).items():
        if new not in ids:
            reasons.append(f"перенаправление {old!r} → {new!r}: карточки {new!r} в выгрузке нет")
        if old in ids:
            reasons.append(f"перенаправление {old!r}: карточка с этим id в выгрузке осталась")

    # Импорт ниже пакета: ``scripts/`` зовут из корня клона, где пакет поставлен
    # ``pip install -e`` — тем же способом его берут соседние скрипты.
    from stepik_grader.glossary.models import GlossaryCard

    for group, cards in groups.items():
        for card in cards:
            try:
                back = GlossaryCard.from_dict(card).to_dict()
            except ValueError as exc:
                reasons.append(f"{group}/{card.get('id')}: не читается — {exc}")
                continue
            if back != card:
                changed = sorted(
                    key for key in card.keys() | back.keys() if card.get(key) != back.get(key)
                )
                reasons.append(f"{group}/{card['id']}: читается с потерями в полях {changed}")
    if reasons:
        raise DeliveryRejected(
            reasons[:_ERRORS_SHOWN]
            + ([f"…и ещё {len(reasons) - _ERRORS_SHOWN}"] if len(reasons) > _ERRORS_SHOWN else [])
        )


def _dump(payload: object) -> str:
    """Каноническая запись JSON: побайтно одинаковая для одинаковых данных."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_files(delivery: dict[str, Any], release: str) -> dict[str, str]:
    """Имя файла каталога данных → содержимое. Чистая функция: та же выгрузка — те же байты."""
    files = {f"{group}.json": _dump(cards) for group, cards in delivery["groups"].items()}
    files[MOVED_FILE_NAME] = _dump(dict(sorted((delivery.get("moved") or {}).items())))
    snapshot = delivery.get("snapshot", {})
    files[SOURCE_FILE_NAME] = _dump(
        {
            "_": (
                "Откуда карточки этого каталога. Файл пишет scripts/import_glossary.py; "
                "руками не править — ни его, ни карточки (issue #1573)."
            ),
            "repo": REPO,
            "release": release,
            "form": delivery.get("form"),
            "cards": snapshot.get("cards"),
            "digest": snapshot.get("digest"),
            "generated_at": delivery.get("generated_at"),
        }
    )
    return files


# ---------------------------------------------------------------------------
# Получение выгрузки
# ---------------------------------------------------------------------------


def pinned_release(data_dir: pathlib.Path = DATA_DIR) -> str | None:
    """Закреплённый выпуск из ``_source.json``; файла нет — ``None``."""
    path = data_dir / SOURCE_FILE_NAME
    if not path.exists():
        return None
    release = json.loads(path.read_text(encoding="utf-8")).get("release")
    return str(release) if release else None


def _fetch_json(url: str) -> Any:
    """JSON по адресу; сеть и разбор — ``OSError``/``ValueError`` наружу."""
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _load(args: argparse.Namespace, release: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Выгрузка и её схема: из файлов, если даны, иначе из выпуска в сети."""
    if args.file:
        delivery = json.loads(args.file.read_text(encoding="utf-8"))
        schema = json.loads(args.schema.read_text(encoding="utf-8")) if args.schema else None
        return delivery, schema
    base = {"repo": REPO, "tag": release}
    delivery = _fetch_json(_RELEASE_URL.format(name="delivery.json", **base))
    schema = _fetch_json(_RELEASE_URL.format(name="delivery.schema.json", **base))
    return delivery, schema


def main(argv: list[str] | None = None) -> int:
    """Точка входа: получить, проверить, записать (или сверить) каталог данных."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--release", help="выпуск Glossary-Python; по умолчанию — закреплённый")
    parser.add_argument("--file", type=pathlib.Path, help="delivery.json с диска вместо сети")
    parser.add_argument("--schema", type=pathlib.Path, help="delivery.schema.json к --file")
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true", help="сверить дерево, ничего не писать")
    args = parser.parse_args(argv)

    release = args.release or pinned_release(args.data_dir)
    if not release:
        print("Не задан выпуск: нет --release и нет закрепления в _source.json.", file=sys.stderr)
        return EXIT_FETCH_FAILED
    if args.file and not args.schema:
        print("Без --schema выгрузка с диска проверяется только формой и чтением.", file=sys.stderr)

    try:
        delivery, schema = _load(args, release)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"Выгрузку {REPO} {release} получить не удалось: {exc}", file=sys.stderr)
        return EXIT_FETCH_FAILED

    try:
        verify_delivery(delivery, schema)
    except DeliveryRejected as exc:
        print(f"Выгрузка {REPO} {release} отвергнута:", file=sys.stderr)
        for reason in exc.reasons:
            print(f"  — {reason}", file=sys.stderr)
        return EXIT_REJECTED

    files = render_files(delivery, release)
    if args.check:
        stale = [
            name
            for name, text in files.items()
            if not (args.data_dir / name).exists()
            or (args.data_dir / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"Каталог данных расходится с выпуском {release}: {', '.join(stale)}")
            print("Обновить: python scripts/import_glossary.py")
            return EXIT_CHECK_FAILED
        print(f"Каталог данных совпадает с выпуском {release}.")
        return 0

    args.data_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.data_dir / name).write_text(text, encoding="utf-8", newline="\n")
    snapshot = delivery.get("snapshot", {})
    print(
        f"Импортирован {REPO} {release}: форма {delivery.get('form')}, "
        f"карточек {snapshot.get('cards')}, групп {len(delivery['groups'])}, "
        f"перенаправлений {len(delivery.get('moved') or {})}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
