"""rules_adapter.py — web-адаптеры над пакетом ``rules/`` (issue #348, эпик #342).

Тонкий слой между HTTP-эндпоинтами (`/api/rules`, `/api/rules/{code}`) и
доменным провайдером карточек правил (`rules.bundled_rules`). По образцу
`web/glossary_adapter.py`: отдаёт готовые dict'ы, кеш bundled-базы по mtime —
внутри провайдера (общий `core/mtime_cache`), адаптер его не дублирует.
"""

from __future__ import annotations

from typing import Any

from stepik_grader.rules import bundled_rules

__all__ = ["rules_search", "rules_get"]


def rules_search(query: str = "", *, tag: str | None = None) -> list[dict[str, Any]]:
    """Карточки правил по ``query`` (подстрока id/title/tags) и опциональному ``tag``.

    Пустой ``query`` — без текстового фильтра (вся база). ``tag`` — точное
    совпадение метки (без регистра). Результат — список dict'ов для JSON.
    """
    provider = bundled_rules()
    cards = provider.search(query) if query.strip() else provider.all()
    if tag:
        needle = tag.lower()
        cards = [c for c in cards if needle in {t.lower() for t in c.tags}]
    return [c.to_dict() for c in cards]


def rules_get(code: str) -> dict[str, Any] | None:
    """Карточка правила по коду (``E501``), либо ``None`` (адаптер отдаёт 404)."""
    card = bundled_rules().get(code)
    return card.to_dict() if card is not None else None
