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
from stepik_grader.core import history, insights, progress_export

__all__ = ["active_count", "insights_cards", "progress_report", "progress_rows"]


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else Path.cwd() / history.HISTORY_DB_NAME


def progress_report(*, db_path: Path | None = None) -> dict[str, Any]:
    """Агрегатный отчёт прогресса → dict для `/api/progress` (issue #538/#432).

    Тонкая обёртка над ``progress_export.build_progress_report`` (тот же движок,
    что CLI ``--export-progress`` — логика в core, не дублируется): KPI
    ``solved_tasks``/``total_tasks``, тали ``verdicts``/``failure_kinds`` и TTFG
    по задачам (``tasks``). Пустая/отсутствующая история → отчёт с нулевыми
    счётчиками (``build_progress_report`` читает историю best-effort, не 500).
    """
    return progress_export.build_progress_report(_db_path(db_path))


def progress_rows(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    """TTFG-прогресс по задачам → список dict'ов (issue #431).

    Попыток/времени до первого полного AC по каждой `task_key`. Пустая/
    отсутствующая история → ``[]``. С issue #538 ``/api/progress`` отдаёт полный
    ``progress_report`` (в нём те же строки в ``tasks``); эта функция оставлена
    как тонкий per-task адаптер для повторного использования/тестов.
    """
    return [asdict(p) for p in insights.time_to_first_green(_db_path(db_path))]


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
