"""glossary_adapter.py — тонкий web-адаптер над локальной базой глоссария.

Архитектурный слой: Application/UI (web-адаптер), как и ``viewmodels.py``.
Никакой новой бизнес-логики поиска/хранения — только пас-through над уже
готовым ``JsonGlossaryProvider``/``load_missing_queue`` (issue #126) и
fallback на компактный ``core/glossary.py`` (~28 записей), когда локальная
JSON-база не настроена (``CONFIG.glossary_store is None``), — так раздел
«Глоссарий» не пустует на свежей установке (issue #125).
"""

from __future__ import annotations

import keyword
import pathlib
import sys
from typing import Any, NamedTuple

from stepik_grader.config import CONFIG
from stepik_grader.core.glossary import all_entries
from stepik_grader.core.mtime_cache import MtimeCache
from stepik_grader.glossary.detector import MissingConceptDetector, scan_code_concepts
from stepik_grader.glossary.json_provider import (
    BUNDLED_GLOSSARY_DIR,
    GlossaryError,
    JsonGlossaryProvider,
    append_missing_entries,
    load_missing_queue,
)
from stepik_grader.glossary.models import GlossaryCard
from stepik_grader.glossary.stdlib_inventory import build_stdlib_inventory

__all__ = [
    "code_terms",
    "glossary_get",
    "glossary_missing",
    "glossary_search",
    "queue_code_gaps",
]

# Допустимые сортировки раздела «Глоссарий» (issue #329, relevance — #685).
# Всё прочее → порядок источника (без сортировки).
_SORTS = frozenset({"relevance", "az", "section", "version"})

# Семейства разделов (issue #685) — грань ?group=, вычисляемая из ``section``,
# без нового поля в карточках. Семейства покрывают ВСЕ разделы базы: в UI они
# заменили собой селект «Раздел» и чипы, поэтому раздел без семейства стал бы
# недостижимым в навигации. Страховка от такого дрейфа двойная: неизвестный
# раздел падает в ``other`` («Прочее» — кнопка появляется, только если семейство
# непусто), а тест ``test_every_bundled_section_has_explicit_group`` требует,
# чтобы в комплектной базе ``other`` оставалось пустым — новый раздел из аудита
# карточек (#684) обязан быть классифицирован здесь явно.
_MODULE_SECTION_PREFIX = "Модуль "
_SECTION_GROUPS: dict[str, str] = {
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
_OTHER_GROUP = "other"
_GROUPS = frozenset({"modules", *_SECTION_GROUPS.values(), _OTHER_GROUP})

# EN-подписи разделов (issue #685). Имя раздела — серверное ЗНАЧЕНИЕ фильтра
# (`?section=`) и остаётся русским; наружу вместе с ним едет `section_label` —
# то, что показывает UI. Переводы живут здесь, рядом с классификацией, а не в
# ui.json: там ключ пришлось бы синтезировать из русской строки, и любой
# переименованный при аудите (#684) раздел давал бы маркер ⟦…⟧ вместо текста.
# Здесь незнакомый раздел просто показывается как есть.
_MODULE_SECTION_PREFIX_EN = "Module "
_SECTION_LABELS_EN: dict[str, str] = {
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


def _section_label(section: str, lang: str) -> str:
    """Подпись раздела для UI: RU — как есть, EN — перевод (fallback — исходник).

    Разделы модулей переводятся правилом «Модуль X» → «Module X»: имя модуля
    (``math``, ``os``) — не текст для перевода, а идентификатор.
    """
    if lang != "en" or not section:
        return section
    if section.startswith(_MODULE_SECTION_PREFIX):
        return _MODULE_SECTION_PREFIX_EN + section[len(_MODULE_SECTION_PREFIX) :]
    return _SECTION_LABELS_EN.get(section, section)


# При коллизии «хвоста» (``split`` есть у str/bytes/bytearray) предпочитаем
# метод основного типа, который новичок и имеет в виду: str → list → dict → …
# Работает и на матче концепций из кода (``_card_index``), и на тай-брейке
# релевантной выдачи (``_sort_cards``, issue #685).
_TYPE_PRIORITY: tuple[str, ...] = ("str.", "list.", "dict.", "set.", "tuple.")


def _type_priority(card: GlossaryCard) -> int:
    """Позиция типа-владельца карточки в ``_TYPE_PRIORITY`` (не из списка — в конец)."""
    cid = card.id.lower()
    for i, prefix in enumerate(_TYPE_PRIORITY):
        if cid.startswith(prefix):
            return i
    return len(_TYPE_PRIORITY)


def _card_group(card: GlossaryCard) -> str:
    """Семейство карточки по её разделу (``modules``/``types``/… либо ``other``).

    Отдаётся в API вместе с карточкой (issue #685): UI строит из этого поля и
    ряд кнопок-семейств, и список разделов внутри раскрытого семейства — правило
    классификации живёт только здесь и в JS не повторяется.
    """
    if card.section.startswith(_MODULE_SECTION_PREFIX):
        return "modules"
    return _SECTION_GROUPS.get(card.section, _OTHER_GROUP)


def _sort_cards(cards: list[GlossaryCard], sort: str | None, query: str = "") -> list[GlossaryCard]:
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
        return sorted(
            cards, key=lambda c: (c.match_rank(query), _type_priority(c), c.title.lower())
        )
    if sort == "az":
        return sorted(cards, key=lambda c: c.title.lower())
    if sort == "section":
        return sorted(cards, key=lambda c: (c.section.lower(), c.title.lower()))
    if sort == "version":
        # Карточки без версии — в конец; версии по возрастанию строкового ключа.
        return sorted(cards, key=lambda c: (c.version == "", c.version, c.title.lower()))
    return cards


def _is_private_name(card_id: str) -> bool:
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


def _fallback_cards() -> list[GlossaryCard]:
    """Компактный глоссарий (core/glossary.py) как GlossaryCard — zero-config."""
    return [
        GlossaryCard(
            id=entry.anchor,
            title=entry.exception,
            kind="exception",
            summary=entry.hint,
            status="ready",
            url=entry.url,
        )
        for entry in all_entries()
    ]


# Кеш распарсенных карточек по источнику (issue #339): раньше каждый запрос к
# /api/glossary*, /api/code-terms заново читал и парсил всю бандл-базу (1.2 МБ
# JSON, ~1400 карточек). Теперь парсим один раз и держим в памяти, инвалидируя
# по mtime через общий core/mtime_cache.MtimeCache (issue #345 — тот же
# механизм переиспользует провайдер правил, не копипастя его; правка store
# подхватывается, read-only бандл-база после первой загрузки стабильна).
# Потребители карточки не мутируют (glossary_search/_card_index строят новые
# списки), поэтому общий список безопасно шарить между запросами/потоками
# ThreadingHTTPServer — гонка на пересчёт идемпотентна.
_CARDS_CACHE: MtimeCache[list[GlossaryCard]] = MtimeCache()


class _GlossaryIndex(NamedTuple):
    """Производные индексы источника (issue #404) — считаются один раз на источник.

    ``by_id`` — ``id -> карточка`` для O(1) ``glossary_get`` (был O(n)-скан
    ``next(...)`` на каждый ``/api/glossary/{id}``); ``by_concept`` — индекс
    ``_card_index`` (id/alias/«хвост» → карточка) для ``code_terms`` (был пересбор
    словаря со внутренним ``sorted()`` по ~1400 карточкам на каждый
    ``/api/code-terms``); ``method_names`` (issue #686) — имена методов, которые
    знает сама база (``_method_names_from_cards``): ими сканер дополняет
    инвентарь встроенных типов, иначе ``Path.exists()`` остаётся незамеченным;
    ``name_concepts`` (issue #686) — bare-имена всех карточек
    (``_name_concepts_from_cards``): по ним сканер ловит голые ссылки
    (``isinstance(x, int)``, ``x: Counter``), а не только вызовы.
    """

    by_id: dict[str, GlossaryCard]
    by_concept: dict[str, GlossaryCard]
    method_names: frozenset[str]
    name_concepts: frozenset[str]


# Производные индексы кешируются по ТОЙ ЖЕ сигнатуре источника (ключ + mtime
# файлов), что и _CARDS_CACHE, поэтому инвалидируются синхронно с карточками:
# правка store перечитывает и карточки, и индексы. Read-only, гонка на пересчёт
# идемпотентна — как и у _CARDS_CACHE (общий MtimeCache без лока).
_INDEX_CACHE: MtimeCache[_GlossaryIndex] = MtimeCache()


def _resolve_source(
    store_path: pathlib.Path | None,
) -> tuple[str, list[pathlib.Path], list[GlossaryCard]]:
    """``(cache_key, files, cards)`` для источника по приоритету: явный store →
    ``CONFIG.glossary_store`` → комплектная база (issue #326) → компактный
    fallback (``core/glossary.py``).

    ``cache_key``/``files`` — сигнатура источника (issue #404): по ней и
    ``_CARDS_CACHE``, и ``_INDEX_CACHE`` инвалидируются вместе. Для fallback —
    ``("fallback", [])``: ``mtime_signature([]) == 0.0`` стабильна, а статические
    карточки ``core/glossary.py`` не меняются, поэтому производный индекс тоже
    кешируется корректно.
    """
    path = store_path if store_path is not None else CONFIG.glossary_store
    if path:
        p = pathlib.Path(path)
        cards = _CARDS_CACHE.get_or_load(
            f"store:{p}", [p], lambda: JsonGlossaryProvider.load(p).all(), on_error=GlossaryError
        )
        if cards is not None:
            return f"store:{p}", [p], cards
    if BUNDLED_GLOSSARY_DIR.is_dir() and any(BUNDLED_GLOSSARY_DIR.glob("*.json")):
        files = sorted(BUNDLED_GLOSSARY_DIR.glob("*.json"))
        cards = _CARDS_CACHE.get_or_load(
            "bundled",
            files,
            lambda: JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all(),
            on_error=GlossaryError,
        )
        if cards is not None:
            return "bundled", files, cards
    return "fallback", [], _fallback_cards()


def _all_cards(store_path: pathlib.Path | None) -> list[GlossaryCard]:
    """Карточки источника (комплектная база ~1400 карточек делает раздел
    «Глоссарий» полноценным на свежей установке; fallback на ~28 исключений —
    если каталог отсутствует/пуст/битый). Кешируется по mtime источника (#339)."""
    return _resolve_source(store_path)[2]


def _build_index(cards: list[GlossaryCard]) -> _GlossaryIndex:
    """Собрать оба производных индекса из карточек (id-first-wins, как прежний
    ``next(...)``-скан в ``glossary_get``)."""
    by_id: dict[str, GlossaryCard] = {}
    for card in cards:
        by_id.setdefault(card.id, card)
    return _GlossaryIndex(
        by_id=by_id,
        by_concept=_card_index(cards),
        method_names=_method_names_from_cards(cards),
        name_concepts=_name_concepts_from_cards(cards),
    )


def _glossary_index(store_path: pathlib.Path | None) -> _GlossaryIndex:
    """Производные индексы источника с кешем по его mtime-сигнатуре (issue #404)."""
    key, files, cards = _resolve_source(store_path)
    index = _INDEX_CACHE.get_or_load(key, files, lambda: _build_index(cards))
    return index if index is not None else _build_index(cards)


def glossary_search(
    query: str,
    *,
    section: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    sort: str | None = None,
    group: str | None = None,
    lang: str = "ru",
    store_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Карточки, отфильтрованные по ``query`` и опциональным граням, отсортированные.

    - ``query`` — подстрока по search-терминам (пустой = без текстового фильтра);
    - ``section``/``kind`` — точное совпадение соответствующего поля (issue #329);
      разделы НЕ объединяются — «Списки (list)» и «Кортежи (tuple)» раздельно;
    - ``group`` (issue #685) — семейство разделов: ``modules`` (все разделы
      «Модуль X»), ``types`` (встроенные типы), ``syntax`` (конструкции языка),
      ``builtins`` (встроенные функции и исключения), ``io`` (ввод-вывод и
      файлы), ``algorithms``, ``other`` (не классифицированные разделы).
      Считается из ``section`` (``_card_group``), нового поля в карточках не
      требует; неизвестное значение игнорируется, как и неизвестный ``sort``;
    - ``status`` (issue #436) — по умолчанию (``None``) выдача ограничена
      ``ready`` (черновики автогенерации не шумят); ``"all"`` — показать все
      статусы; иное значение — точное совпадение (``draft``/``new``/…);
    - ``sort`` — ``az``/``section``/``version`` (иначе порядок источника);
    - ``lang`` — локаль ``?lang=`` (issue #363): ``summary``/``body`` отдаются
      строкой выбранного языка (fallback RU).

    Приватно-именованные АВТОДРАФТЫ (``_module``/``obj._attr``, см.
    ``_is_private_name``) скрыты из выдачи ученика ВСЕГДА, даже под явным
    ``?status=draft`` (issue #436 AC2). Фильтр применяется ТОЛЬКО к не-``ready``
    карточкам: рукописные/промотированные ``ready`` (включая легитимные
    dunder-slug OOP-карточки ``__add__``/``__len__`` и ``_missing_``) приватностью
    имени не прячутся — иначе они регрессионно исчезли бы из поиска. Фильтр живёт
    здесь, а не в ``_all_cards``, поэтому детектор/очередь
    (``code_terms``/``queue_code_gaps``) по-прежнему видят ПОЛНУЮ базу (AC3).
    """
    cards = _all_cards(store_path)
    cards = [c for c in cards if c.status == "ready" or not _is_private_name(c.id)]
    if query.strip():
        cards = [c for c in cards if c.matches(query)]
    if section:
        cards = [c for c in cards if c.section == section]
    if kind:
        cards = [c for c in cards if c.kind == kind]
    if group in _GROUPS:
        cards = [c for c in cards if _card_group(c) == group]
    effective_status = status if status else "ready"
    if effective_status != "all":
        cards = [c for c in cards if c.status == effective_status]
    cards = _sort_cards(cards, sort, query)
    # ``group``/``section_label`` — аддитивные поля ответа (issue #685): по
    # первому UI строит семейства и списки их разделов, второе — подпись раздела
    # на языке ``lang`` (само ``section`` остаётся серверным значением фильтра).
    return [
        {
            **c.to_api_dict(lang),
            "group": _card_group(c),
            "section_label": _section_label(c.section, lang),
        }
        for c in cards
    ]


def glossary_get(
    card_id: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """Карточка по id, либо None (адаптер отдаёт 404 в этом случае).

    ``lang`` — локаль ``?lang=`` для ``summary``/``body`` (issue #363, fallback RU)
    и для подписи раздела ``section_label`` (issue #685) — как в ``glossary_search``,
    чтобы deep-link на карточку показывал раздел на том же языке, что и список.
    """
    card = _glossary_index(store_path).by_id.get(card_id)  # issue #404: O(1) вместо O(n)
    if card is None:
        return None
    return {
        **card.to_api_dict(lang),
        "group": _card_group(card),
        "section_label": _section_label(card.section, lang),
    }


def _card_index(cards: list[GlossaryCard]) -> dict[str, GlossaryCard]:
    """Индекс ``id/alias/«хвост id» (lower) -> карточка`` для матча концепций из кода.

    Помимо ``id`` и ``aliases`` карточка индексируется по «хвосту» своего id
    после точки (``str.split`` → ключ ``split``): концепция-метод из сканера
    приходит голым именем (``split``), а карточка метода хранится как
    ``str.split`` (issue #322). При конфликте «хвоста» побеждает карточка
    основного типа (``str.split`` важнее ``bytearray.split``).
    """
    index: dict[str, GlossaryCard] = {}
    for card in sorted(cards, key=_type_priority):  # приоритетные типы кладутся первыми
        keys = {card.id, card.id.rsplit(".", 1)[-1], *card.aliases}
        for key in keys:
            k = key.strip().lower()
            if k:
                index.setdefault(k, card)
    return index


def _match_card(concept: str, index: dict[str, GlossaryCard]) -> GlossaryCard | None:
    """Найти карточку под концепцию: точное id/alias/«хвост», затем «хвост» концепции.

    ``functools.reduce`` матчится картой ``reduce``, а голый метод ``split`` —
    картой ``str.split`` (через индекс по «хвосту id», см. ``_card_index``).
    """
    concept_lc = concept.lower()
    if concept_lc in index:
        return index[concept_lc]
    tail = concept_lc.rsplit(".", 1)[-1]
    return index.get(tail)


# issue #367: наборы builtins/методов для сканера — из stdlib-инвентаря (issue
# #196), а не из узкого хардкода detector.py. Инвентарь детерминирован и
# стабилен в пределах процесса (интроспекция running-интерпретатора, без ФС),
# поэтому считается один раз лениво и кешируется в модульном глобале.
_INVENTORY_SETS: tuple[frozenset[str], frozenset[str]] | None = None


def _inventory_sets() -> tuple[frozenset[str], frozenset[str]]:
    """``(builtins, methods)`` из ``stdlib_inventory`` для ``scan_code_concepts``.

    ``builtins`` — имена встроенных функций/классов (``frozenset``, ``super``,
    ``hash``, …, которых узкий ``CODE_TERM_BUILTINS`` не знал) **и встроенных
    исключений** (issue #686: без них ``ValueError("...")`` в коде не
    распознавалось, хотя карточка есть); ``methods`` — имена публичных методов
    встроенных типов (``removeprefix``, ``translate``, bytes-методы, …).
    Кешируется на весь процесс.
    """
    global _INVENTORY_SETS
    if _INVENTORY_SETS is None:
        items = build_stdlib_inventory()
        builtins_names = frozenset(
            it.qualname
            for it in items
            if it.module == "builtins" and it.kind in ("function", "class", "exception")
        )
        method_names = frozenset(
            it.qualname.rsplit(".", 1)[-1] for it in items if it.kind == "method"
        )
        _INVENTORY_SETS = (builtins_names, method_names)
    return _INVENTORY_SETS


def _name_concepts_from_cards(cards: list[GlossaryCard]) -> frozenset[str]:
    """Bare-имена (без точки), на которые в базе есть карточка, — правильный регистр.

    issue #686: сканер распознаёт голую ссылку на имя (``isinstance(x, int)``,
    ``x: Counter``, ``class Foo(Enum)``) только если оно в этом наборе, поэтому
    набор — это буквально «всё, на что есть карточка»: любой класс/функция
    модуля или встроенное имя, а не курируемый список. Регистр берём из
    ``title`` (``KeyError``, ``Counter``, ``NamedTuple``): id карточек
    нормализован в нижний, а в коде имя пишется как в Python. Ключевые слова
    (``for``, ``def``) исключены — они не ``Name``, их ловят visit-конструкции.
    """
    names: set[str] = set()
    for card in cards:
        if "." in card.id:
            continue
        title = card.title.replace("()", "").strip()
        name = title if title.isidentifier() else (card.id if card.id.isidentifier() else "")
        if name and not keyword.iskeyword(name):
            names.add(name)
    return frozenset(names)


def _method_names_from_cards(cards: list[GlossaryCard]) -> frozenset[str]:
    """Имена методов, известные самой базе: «хвосты» id вида ``Класс.метод``.

    issue #686: stdlib-инвентарь знает методы только встроенных типов (204 имени
    из ``builtins``), поэтому ``Path.exists()``/``Path.read_text()`` панель не
    видела — хотя карточки ``path.exists``/``path.read_text`` в базе есть.
    Источником имён становится сама база: если первый сегмент id — НЕ имя
    stdlib-модуля, то это класс, а хвост — его метод. Проверка по
    ``sys.stdlib_module_names`` не даёт превратить ``math.sqrt`` в «метод
    ``sqrt``», иначе любой ``obj.sqrt()`` матчился бы на функцию модуля.
    """
    names: set[str] = set()
    for card in cards:
        head, _, tail = card.id.partition(".")
        if not tail or "." in tail:
            continue
        if head in sys.stdlib_module_names:
            continue
        names.add(tail)
    return frozenset(names)


def code_terms(
    code: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    """Термины глоссария для концепций, найденных в ``code`` (issue #321/#322/#367/#686).

    Сканирует код (``scan_code_concepts`` — builtin'ы и методы из stdlib-
    инвентаря, вызовы stdlib с разворотом цепочки ``os.path.join``, синтаксические
    конструкции, исключения из ``raise``/``except`` и атрибуты модулей) и
    сопоставляет с карточками базы. Возвращает **все**
    распознанные концепции (а не только покрытые) в виде
    ``{id, title, summary, kind, has_card, url, confidence, snippet}``:
    покрытые несут данные карточки (``has_card=True``), непокрытые —
    сам концепт (``has_card=False``, панель рисует их приглушённо). Методы —
    ``confidence="low"`` (тип получателя статически неизвестен). Порядок:
    покрытые вперёд, затем по ``title``.

    issue #686: набор имён методов — инвентарь встроенных типов ПЛЮС имена,
    известные самой базе (``index.method_names``), поэтому в панель попадают и
    методы stdlib-классов (``Path.exists()``), которых инвентарь не знает; набор
    имён для голых ссылок (``detect_names``) — ``index.name_concepts`` вместе с
    инвентарными builtins, поэтому распознаётся любое имя с карточкой, а не
    только вызванное.
    """
    index_data = _glossary_index(store_path)  # issue #404: индекс кеширован по mtime
    inventory_builtins, methods = _inventory_sets()
    concepts = scan_code_concepts(
        code,
        notable_builtins=inventory_builtins | index_data.name_concepts,
        methods=methods | index_data.method_names,
        detect_names=True,
    )
    if not concepts:
        return []
    index = index_data.by_concept
    seen_cards: set[str] = set()
    seen_concepts: set[str] = set()
    terms: list[dict[str, Any]] = []
    for concept, (kind, snippet) in concepts.items():
        confidence = "low" if kind == "method" else "high"
        card = _match_card(concept, index)
        if card is not None:
            if card.id in seen_cards:
                continue
            seen_cards.add(card.id)
            terms.append(
                {
                    "id": card.id,
                    "title": card.title,
                    "summary": card.localized("summary", lang),
                    "kind": card.kind,
                    "has_card": True,
                    "url": card.url,
                    "confidence": confidence,
                    "snippet": snippet,
                }
            )
        elif concept not in seen_concepts:
            seen_concepts.add(concept)
            terms.append(
                {
                    "id": concept,
                    "title": concept,
                    "summary": "",
                    "kind": kind,
                    "has_card": False,
                    "url": "",
                    "confidence": confidence,
                    "snippet": snippet,
                }
            )
    terms.sort(key=lambda t: (not t["has_card"], t["title"].lower()))
    return terms


def queue_code_gaps(
    code: str,
    *,
    source: str = "",
    queue_path: pathlib.Path | None = None,
    store_path: pathlib.Path | None = None,
) -> None:
    """Practice-driven AST-канал (issue #322): пробелы кода → очередь «Недостающее».

    ``MissingConceptDetector.detect_from_code`` (узкий notable-набор — без
    повседневных ``print``/``len``) находит функции/конструкции без карточки в
    базе; ``append_missing_entries`` дедуплицирует по ``concept``. Best-effort и
    defensive (как ``_queue_missing_concept``): плохой/незаписываемый путь очереди
    не должен ронять эндпоинт. Вызывается на разовых ``{path}``-запросах (после
    прогона), не на debounce-редактировании.
    """
    path = queue_path if queue_path is not None else pathlib.Path(CONFIG.glossary_missing_queue)
    try:
        known = {term for card in _all_cards(store_path) for term in card.search_terms}
        entries = MissingConceptDetector().detect_from_code(code, known=known, source=source)
        if entries:
            append_missing_entries(path, entries)
    except (GlossaryError, OSError):
        pass


def glossary_missing(*, queue_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Очередь пополнения (J7) — пусто при отсутствующем/битом файле очереди."""
    path = queue_path if queue_path is not None else pathlib.Path(CONFIG.glossary_missing_queue)
    try:
        entries = load_missing_queue(path)
    except GlossaryError:
        return []
    return [e.to_dict() for e in entries]
