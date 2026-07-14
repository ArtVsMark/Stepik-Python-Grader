"""insights_adapter.py — web-адаптер над ``core/insights`` (issue #348, эпик #342).

Слой между HTTP-эндпоинтом `/api/insights` (и бейджем sidebar) и доменным
агрегатором карточек «Подучить» (`insights.learning_cards`). Пороги затухания
N/T/K берутся из `CONFIG`; путь БД — `.grader_history.db` в рабочей папке
сервера (тот же opt-in, что у CLI, #344). Тот же core-слой, что у CLI-витрины
(#349) — логика не дублируется.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core import history, insights

__all__ = ["insights_cards", "active_count"]


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else Path.cwd() / history.HISTORY_DB_NAME


def insights_cards(
    *, include_archived: bool = False, db_path: Path | None = None
) -> list[dict[str, Any]]:
    """Карточки «Подучить» из истории → список dict'ов для `/api/insights`.

    Пустая/отсутствующая история → ``[]`` (не ошибка). ``archived`` по
    умолчанию скрыты (хотелка №5); ``include_archived=True`` вернёт и их.
    """
    cards = insights.learning_cards(
        _db_path(db_path),
        n=CONFIG.insights_window_n,
        t=CONFIG.insights_active_threshold_t,
        k=CONFIG.insights_clean_streak_k,
        include_archived=include_archived,
    )
    return [asdict(card) for card in cards]


def active_count(*, db_path: Path | None = None) -> int:
    """Число активных карточек «Подучить» — для бейджа sidebar (issue #348)."""
    return sum(1 for card in insights_cards(db_path=db_path) if card["status"] == "active")
