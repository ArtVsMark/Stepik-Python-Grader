"""web — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1; issue #125).

Пакет — эволюция бывшего одиночного ``web.py`` (см. docs/dev/web-contracts.md §
«Архитектура будущего web UI»): ``server.py`` (HTTP-хендлер), ``viewmodels.py``
(грейдинг → JSON), ``static/`` (HTML/CSS/JS без build-шага). Публичный API
(``grade_benchmark``/``grade_path``/``run_server``) не меняется — только
внутреннее расположение модулей.

Реэкспортируется ровно ``__all__`` и ничего сверх: приватные внутренности
(``_Handler``, ``_INDEX_HTML``, ``_case_view``, …) берутся из своих модулей —
``web.server``/``web.viewmodels``. Иначе тесты, импортировавшие их отсюда,
фиксировали приватное как де-факто публичный API пакета (issue #830, ARCH-08).
"""

from __future__ import annotations

from stepik_grader.web.server import run_server
from stepik_grader.web.viewmodels import (
    estimate_run_count,
    grade_benchmark,
    grade_microbench,
    grade_path,
    list_solutions,
    read_source,
    save_solution,
)

__all__ = [
    "estimate_run_count",
    "grade_benchmark",
    "grade_microbench",
    "grade_path",
    "list_solutions",
    "read_source",
    "run_server",
    "save_solution",
]
