"""stdio_encoding.py — общий переключатель stdout/stderr на UTF-8.

Leaf-модуль: только stdlib, ни одного проектного импорта (ADR-0011, тот же
приём, что у ``atomic_io.py`` и ``db.py``). Лежит на верхнем уровне пакета
намеренно — им пользуются и точки входа (`cli/`, `downloader.py`,
`diagnostic_stepik.py`, `launcher.py`), и скрипты стенда из ``scripts/``, а
тянуть ради этого ``core/`` никому из них не нужно.

Зачем модуль вообще: консоль Windows работает в cp1251/cp866, и печать
символа вне этой кодировки роняет процесс ``UnicodeEncodeError``. Приём против
этого жил в ``cli/options._force_utf8_stdio`` (issue #64) и звался ровно из
одной точки входа — остальные падали (issue #1108). Причём падали не только на
наших эмодзи: заголовки уроков приходят из Stepik, и «Достижения курсов
Поколения 🏆» роняло обход курса на реальном курсе владельца.
"""

from __future__ import annotations

import sys
from typing import Any

__all__ = ["force_utf8_stdio"]


def force_utf8_stdio() -> None:
    """Переключить ``stdout``/``stderr`` на UTF-8 с заменой непредставимого.

    ``errors="replace"`` выбран сознательно: неотображаемый глиф обязан
    испортить один символ, а не убить команду целиком — инструмент, который
    падает на названии чужого урока, бесполезен ровно там, где нужен.

    No-op на потоках без ``reconfigure`` (перехваченные pytest, подменённые
    обёртки) и на тех, что уже в UTF-8. Вызывать в начале ``main()`` каждой
    точки входа — до первой печати.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure: Any = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding not in {"utf-8", "utf8"}:
            reconfigure(encoding="utf-8", errors="replace")
