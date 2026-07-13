"""i18n.py — загрузка JSON-локалей для CLI-сообщений (issue #141/#144/#355).

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib, не
импортирует project-код).

Единый каталог сообщений CLI (issue #355): JSON-файлы
``core/locales/<lang>.json`` (ru/en). ``cli._t()`` — тонкая обёртка, читающая
шаблон отсюда; захардкоженного словаря ``_MESSAGES`` больше нет (слит в JSON).
Парность ru/en стережёт ``scripts/check_locale_guardrails.py``. Изначально
(issue #144) JSON был аддитивным путём поверх ``_MESSAGES``; issue #355 сделал
его единственным.
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
    корне — пустой ``dict``, не исключение. Комплектные ru/en грузятся при
    импорте ``cli`` в ``_LOCALE_MESSAGES``; их парность и наличие обязательных
    ключей стережёт ``scripts/check_locale_guardrails.py`` (issue #355).
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
