"""taxonomy.py — классификация разделов глоссария, подписи и порядок выдачи.

Домен глоссария, а не web: семейства разделов, EN-подписи, приоритет
типа-владельца, сортировки и правило «карточка приватна» — свойства самой базы.
Раньше всё это жило в ``web/glossary_adapter.py``, который по замыслу тонкий
пас-через: логика была недоступна CLI, а «тонкий адаптер» разросся до 600+
строк (issue #831, ARCH-06).

Зависит только от ``glossary/models.py``, своего каталога данных и stdlib —
ребра ``glossary → core`` здесь нет и быть не должно (ADR-0011).
"""

from __future__ import annotations

import json
import pathlib

from stepik_grader.glossary.models import GlossaryCard

__all__ = [
    "GROUPS",
    "MODULE_SECTION_PREFIX",
    "OTHER_GROUP",
    "SECTION_GROUPS",
    "SECTION_LABELS_EN",
    "SORTS",
    "card_group",
    "is_private_name",
    "section_label",
    "sort_cards",
    "type_priority",
]

# Допустимые сортировки раздела «Глоссарий» (issue #329, relevance — #685).
# Всё прочее → порядок источника (без сортировки).
SORTS = frozenset({"relevance", "az", "section", "version"})

# Семейства разделов (issue #685) — грань ?group=, вычисляемая из ``section``,
# без нового поля в карточках. Семейства покрывают ВСЕ разделы базы: в UI они
# заменили собой селект «Раздел» и чипы, поэтому раздел без семейства стал бы
# недостижимым в навигации.
#
# Классификацию ведёт Glossary-Python: выгрузка формы 6.1 несёт ``navigation`` —
# порядок семейств и для каждого раздела его семейство и подписи. Импорт кладёт
# её в ``data/_navigation.json``, а здесь она только читается. Прежде таблица
# жила здесь копией, и новый раздел в выгрузке требовал правки грейдера — а
# расхождение ловил лишь тест (issue #1573). Неизвестный раздел (сторонняя
# база без навигации) классифицируется правилом «Модуль X» либо падает в
# ``other``: кнопка «Прочее» появляется, только если семейство непусто.
MODULE_SECTION_PREFIX = "Модуль "
OTHER_GROUP = "other"
_NAVIGATION_FILE = pathlib.Path(__file__).parent / "data" / "_navigation.json"


def _load_navigation(path: pathlib.Path) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    """Семейство и EN-подпись каждого раздела и порядок семейств из навигации.

    Файла нет или он не читается — пустая навигация: глоссарий остаётся рабочим
    на правилах по умолчанию, а комплектный файл держит тест
    ``test_the_bundled_navigation_classifies_every_section``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sections = data["sections"]
        groups = {name: str(entry["group"]) for name, entry in sections.items()}
        labels_en = {name: str(entry["en"]) for name, entry in sections.items()}
        order = tuple(str(group) for group in data["groups"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, {}, ()
    return groups, labels_en, order


SECTION_GROUPS: dict[str, str]
SECTION_LABELS_EN: dict[str, str]
SECTION_GROUPS, SECTION_LABELS_EN, _GROUP_ORDER = _load_navigation(_NAVIGATION_FILE)
GROUPS = frozenset({"modules", *SECTION_GROUPS.values(), *_GROUP_ORDER, OTHER_GROUP})

# EN-подпись раздела модуля строится правилом «Модуль X» → «Module X», а не
# берётся из навигации: там у раздела модуля подпись короткая (``abc``) — она
# для списка внутри семейства «Модули». Здесь подпись идёт и в мету карточки,
# где голое имя модуля читается хуже, поэтому интерфейс сохраняет прежний вид.
_MODULE_SECTION_PREFIX_EN = "Module "

# При коллизии «хвоста» (``split`` есть у str/bytes/bytearray) предпочитаем
# метод основного типа, который новичок и имеет в виду: str → list → dict → …
# Работает и на матче концепций из кода (``lookup.card_index``), и на тай-брейке
# релевантной выдачи (``sort_cards``, issue #685).
_TYPE_PRIORITY: tuple[str, ...] = ("str.", "list.", "dict.", "set.", "tuple.")


def section_label(section: str, lang: str) -> str:
    """Подпись раздела для UI: RU — как есть, EN — перевод (fallback — исходник).

    Разделы модулей переводятся правилом «Модуль X» → «Module X»: имя модуля
    (``math``, ``os``) — не текст для перевода, а идентификатор.
    """
    if lang != "en" or not section:
        return section
    if section.startswith(MODULE_SECTION_PREFIX):
        return _MODULE_SECTION_PREFIX_EN + section[len(MODULE_SECTION_PREFIX) :]
    return SECTION_LABELS_EN.get(section, section)


def type_priority(card: GlossaryCard) -> int:
    """Позиция типа-владельца карточки в ``_TYPE_PRIORITY`` (не из списка — в конец)."""
    cid = card.id.lower()
    for i, prefix in enumerate(_TYPE_PRIORITY):
        if cid.startswith(prefix):
            return i
    return len(_TYPE_PRIORITY)


def card_group(card: GlossaryCard) -> str:
    """Семейство карточки по её разделу (``modules``/``types``/… либо ``other``).

    Отдаётся в API вместе с карточкой (issue #685): UI строит из этого поля и
    ряд кнопок-семейств, и список разделов внутри раскрытого семейства — правило
    классификации живёт только здесь и в JS не повторяется.
    """
    group = SECTION_GROUPS.get(card.section)
    if group is not None:
        return group
    if card.section.startswith(MODULE_SECTION_PREFIX):
        return "modules"
    return OTHER_GROUP


def sort_cards(cards: list[GlossaryCard], sort: str | None, query: str = "") -> list[GlossaryCard]:
    """Отсортировать карточки: relevance, az (A–Z), section (раздел→A–Z), version.

    ``relevance`` (issue #685) — по качеству совпадения с ``query``
    (``GlossaryCard.match_rank``), тай-брейк — приоритет типа-владельца
    (``str.split`` выше ``bytearray.split``, тот же ``_TYPE_PRIORITY``, что у
    матча из кода) и затем A–Z. Без запроса ранжировать нечего, поэтому режим
    вырождается ровно в ``az``: приоритет типа там не применяется (иначе
    выдача «просто открыл раздел» перестала бы быть алфавитной — методы ``str.``
    всплыли бы наверх).
    """
    if sort == "relevance":
        if not query.strip():
            return sorted(cards, key=lambda c: c.title.lower())
        return sorted(cards, key=lambda c: (c.match_rank(query), type_priority(c), c.title.lower()))
    if sort == "az":
        return sorted(cards, key=lambda c: c.title.lower())
    if sort == "section":
        return sorted(cards, key=lambda c: (c.section.lower(), c.title.lower()))
    if sort == "version":
        # Карточки без версии — в конец; версии по возрастанию ЧИСЛОМ: строкой
        # «3.10» встаёт раньше «3.9» (тот же класс дефекта, что #1576).
        return sorted(cards, key=lambda c: (c.added == "", _version_key(c.added), c.title.lower()))
    return cards


def _version_key(version: str) -> tuple[int, ...]:
    """Числовой ключ версии Python для сортировки: ``"3.10"`` → ``(3, 10)``.

    ``"<3.0"`` (имя старше Python 3, форма 5.0 Glossary-Python) → ``(0,)``:
    встаёт раньше любой явной версии. Нечисловое — ``()``, в самое начало.
    """
    if version.startswith("<"):
        return (0,)
    parts = version.split(".")
    return tuple(int(part) for part in parts) if all(p.isdigit() for p in parts) else ()


def is_private_name(card_id: str) -> bool:
    """True для приватно-именованных карточек (issue #436).

    Приватным считается id, у которого ХОТЯ БЫ один сегмент (по точкам) начинается
    с одиночного ``_``, но НЕ является дандером ``__x__``. Примеры приватных:
    ``os._exit``, ``_pickle.pickleerror``, ``warnings._optionerror``. Дандеры
    (``__init__``, ``str.__len__``) — легитимные публичные карточки, НЕ приватны.
    """
    for segment in card_id.split("."):
        if segment.startswith("_") and not (segment.startswith("__") and segment.endswith("__")):
            return True
    return False
