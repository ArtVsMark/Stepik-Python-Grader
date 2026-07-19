"""failure_context.py — сборка заземляющего контекста упавшего кейса (issue #542).

Единый core-хелпер, строящий :class:`~stepik_grader.core.ai_hints.FailureContext`
из case-dict проверки + якорей (:func:`insights.failure_kind`,
:func:`error_glossary.resolve_error_hint`). Раньше сборка жила приватной в
``cli/commands.py`` (режимы 1/2) — web-слой и режимы 3/4 не могли её
переиспользовать без дублирования. Вынос в core делает контекст общим: CLI-режимы
1–4 и web-слой (эпик E3) строят ОДИН И ТОТ ЖЕ контекст из общей формы результата
(``CaseResult`` из ``run_tests`` — режимы 1/2 и web; bench-dict — режимы 3/4).

Сам ``ai_hints`` намеренно развязан от якорей (принимает готовые строки) — связка
insights + error_glossary сведена сюда, в отдельный модуль, чтобы
``ai_hints.FailureContext``/``explain_failure`` оставались чистыми.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stepik_grader.core import error_glossary, insights
from stepik_grader.core.ai_hints import FailureContext

__all__ = ["build_failure_context"]


def build_failure_context(
    case: Mapping[str, Any], *, code: str = "", lang: str = "ru"
) -> FailureContext:
    """Собрать ``FailureContext`` из case-dict проверки + якорей.

    ``case`` — словарь одного кейса формы ``CaseResult`` (режимы 1/2, web) или
    bench-dict решения (режимы 3/4: только ``verdict``/``error``, без
    ``output``/``expected``/``stdin``/``diff``). Все поля читаются через ``.get``
    с дефолтами, поэтому обе формы валидны — отсутствующие дают пустые строки.
    Якоря: ``insights.failure_kind`` (таксономия падения) и
    ``error_glossary.resolve_error_hint`` (карточка ошибки для RE).

    ``code`` — исходник решения (заземление промпта), ``lang`` — язык ответа.
    Идентичен для CLI и web: обе стороны кормят один и тот же ``CaseResult`` из
    общего ``run_tests``.
    """
    verdict = str(case.get("verdict") or ("AC" if case.get("passed") else "WA"))
    error = str(case.get("error", ""))
    kind = insights.failure_kind(
        verdict, error=error, output=case.get("output"), expected=case.get("expected")
    )
    entry = error_glossary.resolve_error_hint(error) if error else None
    card = f"{entry.exception}: {entry.hint}" if entry else ""
    return FailureContext(
        verdict=verdict,
        lang=lang,
        case_input=str(case.get("stdin", "")),
        expected="\n".join(case.get("expected") or []),
        actual="\n".join(case.get("output") or []),
        diff=str(case.get("diff", "")),
        error=error,
        failure_kind=kind or "",
        card_text=card,
        code=code,
    )
