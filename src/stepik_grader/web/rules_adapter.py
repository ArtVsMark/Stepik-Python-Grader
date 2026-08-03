"""rules_adapter.py — web-адаптеры над пакетом ``rules/`` (issue #348, эпик #342).

Тонкий слой между HTTP-эндпоинтами (`/api/rules`, `/api/rules/{code}`) и
доменным провайдером карточек правил (`rules.bundled_rules`). По образцу
`web/glossary_adapter.py`: отдаёт готовые dict'ы, кеш bundled-базы по mtime —
внутри провайдера (общий `core/mtime_cache`), адаптер его не дублирует.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stepik_grader.core import insights
from stepik_grader.core.history_recording import default_history_db_path
from stepik_grader.rules import bundled_rules

__all__ = ["rules_get", "rules_search"]


def _db_path(db_path: Path | None) -> Path:
    # issue #818: тот же резолвер, что у CLI — иначе web читал бы базу
    # рабочей папки, пока CLI пишет в пользовательскую.
    return db_path if db_path is not None else default_history_db_path()


def rules_search(
    query: str = "", *, tag: str | None = None, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """Карточки правил по ``query`` (подстрока id/title/tags) и опциональному ``tag``.

    Пустой ``query`` — без текстового фильтра (вся база). ``tag`` — точное
    совпадение метки (без регистра). Каждая карточка получает ``violated`` —
    нарушал ли пользователь это правило лично (issue #403, из истории lint).
    Результат — список dict'ов для JSON.
    """
    provider = bundled_rules()
    cards = provider.search(query) if query.strip() else provider.all()
    if tag:
        needle = tag.lower()
        cards = [c for c in cards if needle in {t.lower() for t in c.tags}]
    violated = insights.violated_rule_codes(_db_path(db_path))
    return [{**c.to_dict(), "violated": c.id in violated} for c in cards]


def rules_get(code: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """Карточка правила по коду (``E501``), либо ``None`` (адаптер отдаёт 404).

    Добавляет ``violated`` — нарушал ли пользователь это правило лично (#403).
    """
    card = bundled_rules().get(code)
    if card is None:
        return None
    violated = insights.violated_rule_codes(_db_path(db_path))
    return {**card.to_dict(), "violated": card.id in violated}
