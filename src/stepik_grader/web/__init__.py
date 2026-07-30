"""web — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1; issue #125).

Пакет — эволюция бывшего одиночного ``web.py`` (см. docs/dev/web-contracts.md §
«Архитектура будущего web UI»): ``server.py`` (HTTP-хендлер), ``viewmodels.py``
(грейдинг → JSON), ``static/`` (HTML/CSS/JS без build-шага). Публичный API
(``grade_benchmark``/``grade_path``/``run_server``) не меняется — только
внутреннее расположение модулей.
"""

from __future__ import annotations

from stepik_grader.web.server import (
    _INDEX_HTML,  # noqa: F401 — re-exported for test back-compat
    _STATIC_JS_SOURCES,  # noqa: F401 — re-exported for source-regression tests (#426: all static/*.js)
    _GraderServer,  # noqa: F401 — re-exported for tests (issue #261 — workspace/confine)
    _Handler,  # noqa: F401 — re-exported for test back-compat
    run_server,
)
from stepik_grader.web.viewmodels import (
    _case_view,  # noqa: F401 — re-exported for test back-compat (tests/test_glossary.py)
    _wa_suggestion,  # noqa: F401 — re-exported for test back-compat (issue #301)
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
