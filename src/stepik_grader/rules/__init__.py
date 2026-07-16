"""rules — локальная база карточек правил PEP 8 (issue #345, эпик #342).

Раздел «Правила/PEP»: контентная база карточек, на которые ссылаются найденные
в коде ученика нарушения (`core/lint.py`, #346). Построена по образцу пакета
``glossary`` — модель + JSON-провайдер + bundled-данные, без обобщения в общий
«RefCard»-слой (решение § 11 аудита). Канон формата — `docs/rules-insights.md`.
"""

from __future__ import annotations

from .json_provider import (
    BUNDLED_RULES_DIR,
    JsonRulesProvider,
    RulesError,
    RulesProvider,
    bundled_rules,
)
from .models import RuleCard, RuleSeverity, RuleStatus

__all__ = [
    "BUNDLED_RULES_DIR",
    "JsonRulesProvider",
    "RuleCard",
    "RuleSeverity",
    "RuleStatus",
    "RulesError",
    "RulesProvider",
    "bundled_rules",
]
