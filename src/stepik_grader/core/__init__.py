"""core — внутренние модули слоя Infrastructure/Utility.

Публичной поверхностью API **не является**: точки входа (`grader.py`,
`downloader.py`, `diagnostic_stepik.py`) импортируют подмодули отсюда напрямую,
а не через этот пакет.

`__all__` здесь намеренно пуст: пакет ничего не реэкспортирует, и
`from stepik_grader.core import *` не должен приносить имена, которых он не
объявлял. Список подмодулей и их назначение — в
[`docs/dev/architecture.md`](../../../docs/dev/architecture.md).
"""

from __future__ import annotations

__all__: list[str] = []
