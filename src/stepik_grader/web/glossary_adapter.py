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
from stepik_grader.glossary.json_provider import (
    BUNDLED_GLOSSARY_DIR,
    GlossaryError,
    JsonGlossaryProvider,
    load_missing_queue,
)
from stepik_grader.glossary.models import GlossaryCard

__all__ = ["glossary_search", "glossary_get", "glossary_missing"]

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


def _all_cards(store_path: pathlib.Path | None) -> list[GlossaryCard]:
    """Карточки источника по приоритету: явный store → ``CONFIG.glossary_store``
    → комплектная база (issue #326) → компактный fallback (``core/glossary.py``).

    Комплектная база (``glossary/data/*.json``, 581 карточка) делает раздел
    «Глоссарий» полноценным на свежей установке без конфигурации; fallback на
    ~28 исключений остаётся, если каталог отсутствует/пуст/битый.
    """
    path = store_path if store_path is not None else CONFIG.glossary_store
    if path:
        try:
            return JsonGlossaryProvider.load(path).all()
        except GlossaryError:
            pass  # битый/отсутствующий store — деградируем ниже, не падаем
    if BUNDLED_GLOSSARY_DIR.is_dir() and any(BUNDLED_GLOSSARY_DIR.glob("*.json")):
        try:
            return JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()
        except GlossaryError:
            pass  # битая комплектная база — последний рубеж fallback
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


def glossary_missing(*, queue_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Очередь пополнения (J7) — пусто при отсутствующем/битом файле очереди."""
    path = queue_path if queue_path is not None else CONFIG.glossary_missing_queue
    try:
        entries = load_missing_queue(path)
    except GlossaryError:
        return []
    return [e.to_dict() for e in entries]
