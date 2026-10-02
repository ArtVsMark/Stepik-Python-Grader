"""taxonomy.py — классификация разделов глоссария, подписи и порядок выдачи.

Домен глоссария, а не web: семейства разделов, EN-подписи, приоритет
типа-владельца, сортировки и правило «карточка приватна» — свойства самой базы.
Раньше всё это жило в ``web/glossary_adapter.py``, который по замыслу тонкий
пас-через: логика была недоступна CLI, а «тонкий адаптер» разросся до 600+
строк (issue #831, ARCH-06).

Зависит только от ``glossary/models.py`` и stdlib — ребра ``glossary → core``
здесь нет и быть не должно (ADR-0011).
"""

from __future__ import annotations

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
# недостижимым в навигации. Страховка от такого дрейфа двойная: неизвестный
# раздел падает в ``other`` («Прочее» — кнопка появляется, только если семейство
# непусто), а тест ``test_every_bundled_section_has_explicit_group`` требует,
# чтобы в комплектной базе ``other`` оставалось пустым — новый раздел из аудита
# карточек (#684) обязан быть классифицирован здесь явно.
MODULE_SECTION_PREFIX = "Модуль "
SECTION_GROUPS: dict[str, str] = {
    # Типы данных — встроенные типы и их методы.
    "Строки (str)": "types",
    "Списки (list)": "types",
    "Кортежи (tuple)": "types",
    "Словари (dict)": "types",
    "Множества (set)": "types",
    "Байтовые последовательности": "types",
    "Числа и математика": "types",
    "Типы данных": "types",
    "Встроенные типы": "types",
    # Синтаксис языка — конструкции, а не библиотечные функции.
    "Функции": "syntax",
    "ООП": "syntax",
    "Циклы": "syntax",
    "Условный оператор": "syntax",
    "Итераторы и генераторы": "syntax",
    "Асинхронное программирование": "syntax",
    "Арифметика и операторы": "syntax",
    "Аннотации и typing": "syntax",
    # Встроенное и ошибки.
    "Встроенные функции": "builtins",
    "Исключения": "builtins",
    # Ввод-вывод.
    "Ввод и вывод": "io",
    "Файлы и I_O": "io",
    # Алгоритмы и структуры данных (учебная тема, не тип и не модуль).
    "Алгоритмы и структуры данных": "algorithms",
}
OTHER_GROUP = "other"
GROUPS = frozenset({"modules", *SECTION_GROUPS.values(), OTHER_GROUP})

# EN-подписи разделов (issue #685). Имя раздела — серверное ЗНАЧЕНИЕ фильтра
# (`?section=`) и остаётся русским; наружу вместе с ним едет `section_label` —
# то, что показывает UI. Переводы живут здесь, рядом с классификацией, а не в
# ui.json: там ключ пришлось бы синтезировать из русской строки, и любой
# переименованный при аудите (#684) раздел давал бы маркер ⟦…⟧ вместо текста.
# Здесь незнакомый раздел просто показывается как есть.
_MODULE_SECTION_PREFIX_EN = "Module "
SECTION_LABELS_EN: dict[str, str] = {
    "Строки (str)": "Strings (str)",
    "Списки (list)": "Lists (list)",
    "Кортежи (tuple)": "Tuples (tuple)",
    "Словари (dict)": "Dictionaries (dict)",
    "Множества (set)": "Sets (set)",
    "Байтовые последовательности": "Byte sequences",
    "Числа и математика": "Numbers & math",
    "Типы данных": "Data types",
    "Встроенные типы": "Built-in types",
    "Функции": "Functions",
    "ООП": "OOP",
    "Циклы": "Loops",
    "Условный оператор": "Conditionals",
    "Итераторы и генераторы": "Iterators & generators",
    "Асинхронное программирование": "Async programming",
    "Арифметика и операторы": "Arithmetic & operators",
    "Аннотации и typing": "Annotations & typing",
    "Встроенные функции": "Built-in functions",
    "Исключения": "Exceptions",
    "Ввод и вывод": "Input & output",
    "Файлы и I_O": "Files & I/O",
    "Алгоритмы и структуры данных": "Algorithms & data structures",
}

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
    if card.section.startswith(MODULE_SECTION_PREFIX):
        return "modules"
    return SECTION_GROUPS.get(card.section, OTHER_GROUP)


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
        # Карточки без версии — в конец; версии по возрастанию строкового ключа.
        return sorted(cards, key=lambda c: (c.version == "", c.version, c.title.lower()))
    return cards


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
