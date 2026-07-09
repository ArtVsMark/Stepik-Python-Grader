"""web — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1; issue #125).

Пакет — эволюция бывшего одиночного ``web.py`` (см. docs/web-mvp.md §
«Архитектура будущего web UI»): ``server.py`` (HTTP-хендлер), ``viewmodels.py``
(грейдинг → JSON), ``static/`` (HTML/CSS/JS без build-шага). Публичный API
(``grade_benchmark``/``grade_path``/``run_server``) не меняется — только
внутреннее расположение модулей.
"""

from __future__ import annotations

from stepik_grader.web.server import (
    _APP_JS,  # noqa: F401 — re-exported for test back-compat (source-regression tests)
    _INDEX_HTML,  # noqa: F401 — re-exported for test back-compat
    _Handler,  # noqa: F401 — re-exported for test back-compat
    run_server,
)
from stepik_grader.web.viewmodels import (
    _case_view,  # noqa: F401 — re-exported for test back-compat (tests/test_glossary.py)
    grade_benchmark,
    grade_path,
)

__all__ = ["grade_benchmark", "grade_path", "run_server"]
