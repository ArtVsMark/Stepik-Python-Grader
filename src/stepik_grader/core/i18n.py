"""i18n.py — загрузка JSON-локалей для CLI-сообщений (issue #141/#144).

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib, не
импортирует project-код).

Основной механизм локализации CLI сегодня — статический словарь ``_MESSAGES``
в ``cli.py`` (issue #51 D-01, ru/en захардкожены прямо в коде). Этот модуль —
параллельный, аддитивный путь: JSON-файлы в ``core/locales/<lang>.json``
позволяют добавлять НОВЫЕ сообщения, не трогая ``_MESSAGES`` и не переписывая
существующие (issue #144 — заложить механизм, не мигрировать всё разом).
``cli._t()`` сначала смотрит в JSON-локаль, при отсутствии ключа там —
откатывается на статический ``_MESSAGES``.
"""

from __future__ import annotations

import json
import pathlib

__all__ = ["LOCALES_DIR", "load_locale_messages"]

LOCALES_DIR = pathlib.Path(__file__).parent / "locales"


def load_locale_messages(lang: str) -> dict[str, str]:
    """Загрузить сообщения локали ``lang`` из ``core/locales/<lang>.json``.

    Graceful degradation (тот же принцип, что у ``GraderConfig.load_config()``
    и у кэша issue #56): отсутствующий файл, битый JSON или не-объект в
    корне — пустой ``dict``, не исключение. Вызывающая сторона (``cli._t()``)
    просто откатывается на статический ``_MESSAGES``.
    """
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}
