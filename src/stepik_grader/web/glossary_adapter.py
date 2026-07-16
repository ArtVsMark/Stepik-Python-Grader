"""glossary_adapter.py — тонкий web-адаптер над локальной базой глоссария.

Архитектурный слой: Application/UI (web-адаптер), как и ``viewmodels.py``.
Никакой новой бизнес-логики поиска/хранения — только пас-through над уже
готовым ``JsonGlossaryProvider``/``load_missing_queue`` (issue #126) и
fallback на компактный ``core/glossary.py`` (~28 записей), когда локальная
JSON-база не настроена (``CONFIG.glossary_store is None``), — так раздел
«Глоссарий» не пустует на свежей установке (issue #125).
"""

from __future__ import annotations

import pathlib
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

# Допустимые сортировки раздела «Глоссарий» (issue #329). Всё прочее → порядок
# источника (без сортировки).
_SORTS = frozenset({"az", "section", "version"})


def _sort_cards(cards: list[GlossaryCard], sort: str | None) -> list[GlossaryCard]:
    """Отсортировать карточки: az (A–Z), section (раздел→A–Z), version (версия→A–Z)."""
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
    ``/api/code-terms``).
    """

    by_id: dict[str, GlossaryCard]
    by_concept: dict[str, GlossaryCard]


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
    return _GlossaryIndex(by_id=by_id, by_concept=_card_index(cards))


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
    lang: str = "ru",
    store_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Карточки, отфильтрованные по ``query`` и опциональным граням, отсортированные.

    - ``query`` — подстрока по search-терминам (пустой = без текстового фильтра);
    - ``section``/``kind`` — точное совпадение соответствующего поля (issue #329);
      разделы НЕ объединяются — «Списки (list)» и «Кортежи (tuple)» раздельно;
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
    effective_status = status if status else "ready"
    if effective_status != "all":
        cards = [c for c in cards if c.status == effective_status]
    cards = _sort_cards(cards, sort)
    return [c.to_api_dict(lang) for c in cards]


def glossary_get(
    card_id: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """Карточка по id, либо None (адаптер отдаёт 404 в этом случае).

    ``lang`` — локаль ``?lang=`` для ``summary``/``body`` (issue #363, fallback RU).
    """
    card = _glossary_index(store_path).by_id.get(card_id)  # issue #404: O(1) вместо O(n)
    return card.to_api_dict(lang) if card is not None else None


# При коллизии «хвоста» (``split`` есть у str/bytes/bytearray) предпочитаем
# метод основного типа, который новичок и имеет в виду: str → list → dict → …
_TYPE_PRIORITY: tuple[str, ...] = ("str.", "list.", "dict.", "set.", "tuple.")


def _card_index(cards: list[GlossaryCard]) -> dict[str, GlossaryCard]:
    """Индекс ``id/alias/«хвост id» (lower) -> карточка`` для матча концепций из кода.

    Помимо ``id`` и ``aliases`` карточка индексируется по «хвосту» своего id
    после точки (``str.split`` → ключ ``split``): концепция-метод из сканера
    приходит голым именем (``split``), а карточка метода хранится как
    ``str.split`` (issue #322). При конфликте «хвоста» побеждает карточка
    основного типа (``str.split`` важнее ``bytearray.split``).
    """

    def priority(card: GlossaryCard) -> int:
        cid = card.id.lower()
        for i, prefix in enumerate(_TYPE_PRIORITY):
            if cid.startswith(prefix):
                return i
        return len(_TYPE_PRIORITY)

    index: dict[str, GlossaryCard] = {}
    for card in sorted(cards, key=priority):  # приоритетные типы кладутся первыми
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
    ``hash``, …, которых узкий ``CODE_TERM_BUILTINS`` не знал); ``methods`` —
    имена публичных методов встроенных типов (``removeprefix``, ``translate``,
    bytes-методы, …). Кешируется на весь процесс.
    """
    global _INVENTORY_SETS
    if _INVENTORY_SETS is None:
        items = build_stdlib_inventory()
        builtins_names = frozenset(
            it.qualname
            for it in items
            if it.module == "builtins" and it.kind in ("function", "class")
        )
        method_names = frozenset(
            it.qualname.rsplit(".", 1)[-1] for it in items if it.kind == "method"
        )
        _INVENTORY_SETS = (builtins_names, method_names)
    return _INVENTORY_SETS


def code_terms(
    code: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    """Термины глоссария для концепций, найденных в ``code`` (issue #321/#322/#367).

    Сканирует код (``scan_code_concepts`` — builtin'ы и методы из stdlib-
    инвентаря, вызовы stdlib с разворотом цепочки ``os.path.join``, синтаксические
    конструкции) и сопоставляет с карточками базы. Возвращает **все**
    распознанные концепции (а не только покрытые) в виде
    ``{id, title, summary, kind, has_card, url, confidence, snippet}``:
    покрытые несут данные карточки (``has_card=True``), непокрытые —
    сам концепт (``has_card=False``, панель рисует их приглушённо). Методы —
    ``confidence="low"`` (тип получателя статически неизвестен). Порядок:
    покрытые вперёд, затем по ``title``.
    """
    notable_builtins, methods = _inventory_sets()
    concepts = scan_code_concepts(code, notable_builtins=notable_builtins, methods=methods)
    if not concepts:
        return []
    index = _glossary_index(store_path).by_concept  # issue #404: индекс кеширован по mtime
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
