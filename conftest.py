"""conftest.py — корневой конфиг pytest.

src/-layout (Issue #35 / CLAUDE.md Sprint 8.2): исходники живут в
src/stepik_grader/, а не в корне репозитория. Явно добавляем src/ в
sys.path, чтобы `import stepik_grader` работал в тестах даже без
`pip install -e .` (хотя обычная разработка предполагает editable install —
см. CONTRIBUTING.md).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
