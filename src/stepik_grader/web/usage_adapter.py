"""usage_adapter.py — журнал прогонов для веб-слоя (issue #1365).

Адаптер, а не прямой импорт ядра: маршрутизатор веб-слоя ходит в ядро только
через адаптеры (ARCH-07, issue #830). Без этой прослойки `api_routes.py` знал
бы про форму `ExportResult`, и любое изменение экспорта тянуло бы за собой
правку маршрута — при том, что маршруту нужен один словарь для ответа.

Своей логики здесь нет и не будет: сбор, схема и закрытый список полей живут в
``core/usage_export.py``, а тут — перевод результата в форму ответа API.
"""

from __future__ import annotations

from typing import Any

from stepik_grader.core.usage_export import USAGE_SCHEMA, collect_events

__all__ = ["usage_snapshot"]


def usage_snapshot(*, since: float | None = None) -> dict[str, Any]:
    """Журнал прогонов в форме ответа API.

    Args:
        since: отдавать только записи не старше этой отметки времени (epoch).

    Returns:
        ``{"schema", "events", "skipped"}``. ``skipped`` отдаётся всегда:
        «журнал пуст» и «журнал побился» обязаны различаться на стороне
        читателя, а не только в логах.
    """
    result = collect_events(since=since)
    return {"schema": USAGE_SCHEMA, "events": result.events, "skipped": result.skipped}
