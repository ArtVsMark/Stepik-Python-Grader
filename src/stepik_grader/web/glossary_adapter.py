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
from typing import Any

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

__all__ = [
    "glossary_search",
    "glossary_get",
    "glossary_missing",
    "code_terms",
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


def _all_cards(store_path: pathlib.Path | None) -> list[GlossaryCard]:
    """Карточки источника по приоритету: явный store → ``CONFIG.glossary_store``
    → комплектная база (issue #326) → компактный fallback (``core/glossary.py``).

    Комплектная база (``glossary/data/*.json``, 581 карточка) делает раздел
    «Глоссарий» полноценным на свежей установке без конфигурации; fallback на
    ~28 исключений остаётся, если каталог отсутствует/пуст/битый. Результат
    кешируется по mtime источника (issue #339) — без репарсинга на каждый запрос.
    """
    path = store_path if store_path is not None else CONFIG.glossary_store
    if path:
        p = pathlib.Path(path)
        cards = _CARDS_CACHE.get_or_load(
            f"store:{p}", [p], lambda: JsonGlossaryProvider.load(p).all(), on_error=GlossaryError
        )
        if cards is not None:
            return cards
    if BUNDLED_GLOSSARY_DIR.is_dir() and any(BUNDLED_GLOSSARY_DIR.glob("*.json")):
        files = sorted(BUNDLED_GLOSSARY_DIR.glob("*.json"))
        cards = _CARDS_CACHE.get_or_load(
            "bundled",
            files,
            lambda: JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all(),
            on_error=GlossaryError,
        )
        if cards is not None:
            return cards
    return _fallback_cards()


def glossary_search(
    query: str,
    *,
    section: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    sort: str | None = None,
    store_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Карточки, отфильтрованные по ``query`` и опциональным граням, отсортированные.

    - ``query`` — подстрока по search-терминам (пустой = без текстового фильтра);
    - ``section``/``kind``/``status`` — точное совпадение соответствующего поля
      (issue #329); разделы НЕ объединяются — «Списки (list)» и «Кортежи (tuple)»
      фильтруются раздельно;
    - ``sort`` — ``az``/``section``/``version`` (иначе порядок источника).
    """
    cards = _all_cards(store_path)
    if query.strip():
        cards = [c for c in cards if c.matches(query)]
    if section:
        cards = [c for c in cards if c.section == section]
    if kind:
        cards = [c for c in cards if c.kind == kind]
    if status:
        cards = [c for c in cards if c.status == status]
    cards = _sort_cards(cards, sort)
    return [c.to_dict() for c in cards]


def glossary_get(card_id: str, *, store_path: pathlib.Path | None = None) -> dict[str, Any] | None:
    """Карточка по id, либо None (адаптер отдаёт 404 в этом случае)."""
    card = next((c for c in _all_cards(store_path) if c.id == card_id), None)
    return card.to_dict() if card is not None else None


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


def code_terms(code: str, *, store_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Термины глоссария для концепций, найденных в ``code`` (issue #321/#322).

    Сканирует код (``scan_code_concepts`` — builtin'ы, вызовы stdlib, методы,
    ``match/case``) и сопоставляет с карточками базы. Возвращает **все**
    распознанные концепции (а не только покрытые) в виде
    ``{id, title, summary, kind, has_card, url, confidence, snippet}``:
    покрытые несут данные карточки (``has_card=True``), непокрытые —
    сам концепт (``has_card=False``, панель рисует их приглушённо). Методы —
    ``confidence="low"`` (тип получателя статически неизвестен). Порядок:
    покрытые вперёд, затем по ``title``.
    """
    concepts = scan_code_concepts(code)
    if not concepts:
        return []
    index = _card_index(_all_cards(store_path))
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
                    "summary": card.summary,
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
    path = queue_path if queue_path is not None else CONFIG.glossary_missing_queue
    try:
        entries = load_missing_queue(path)
    except GlossaryError:
        return []
    return [e.to_dict() for e in entries]
