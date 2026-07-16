"""history_recording.py — сборка записей истории из результатов грейдинга.

Архитектурный слой: Application-service над ``core/history`` (issue #395).
Раньше эти хелперы жили в ``cli/commands.py`` и были недоступны web-слою (web
не должен импортировать cli — оба презентация над core). Вынесены сюда, чтобы
и CLI (режимы 1-4), и web (``web/viewmodels``) наполняли ``.grader_history.db``
через один и тот же код, без дублирования таксономии ``failure_kind`` и
конвертации lint-нарушений.

Чистые функции-преобразователи: не пишут в БД сами — возвращают
``list[CaseRecord]``/``list[LintRecord]``/``Path``, которые вызывающая сторона
передаёт в ``history.record_run(...)``. Best-effort и opt-in — как весь
``core/history`` (см. его докстринг).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stepik_grader.core import glossary, history, insights

__all__ = [
    "cases_from_bench_results",
    "cases_from_test_results",
    "default_history_db_path",
    "lint_records_from_violations",
]


def cases_from_test_results(cases: list[dict[str, Any]]) -> list[history.CaseRecord]:
    """``CaseRecord``'ы режимов 1/2 из ``result['cases']`` (issue #344/#395).

    ``error_class`` для RE достаётся тем же ``lookup_from_error``, что и
    подсказка проверки; ``failure_kind`` — таксономия § 9.3 (issue #347).
    """
    records: list[history.CaseRecord] = []
    for i, c in enumerate(cases, 1):
        verdict = c.get("verdict") or ("AC" if c.get("passed") else "WA")
        raw_time = c.get("time")
        time_ms = float(raw_time) * 1000 if isinstance(raw_time, int | float) else None
        error = c.get("error") or ""
        error_class = None
        if verdict == "RE" and error:
            entry = glossary.lookup_from_error(error)
            error_class = entry.exception if entry else None
        fkind = insights.failure_kind(
            verdict, error=error, output=c.get("output"), expected=c.get("expected")
        )
        records.append(
            history.CaseRecord(
                i, verdict, time_ms=time_ms, error_class=error_class, failure_kind=fkind
            )
        )
    return records


def cases_from_bench_results(
    results: dict[Path, dict[str, Any]],
) -> list[history.CaseRecord]:
    """``CaseRecord``'ы режимов 3/4 — вердикт по решению (issue #344/#395).

    Бенчмарк не даёт per-case вердиктов проверки — пишем один ``CaseRecord`` на
    решение (``ERR`` при ошибке, иначе relative-вердикт ранжирования).
    """
    records: list[history.CaseRecord] = []
    for i, data in enumerate(results.values(), 1):
        verdict = "ERR" if data.get("error") else (data.get("verdict") or "ERR")
        records.append(history.CaseRecord(i, verdict, failure_kind=insights.failure_kind(verdict)))
    return records


def lint_records_from_violations(violations: list[Any]) -> list[history.LintRecord]:
    """``LintRecord``'ы из ``lint.Violation``'ов (issue #403).

    Замыкает контур ``run_lint → LintRecord → record_run(lint=...)``: столбец
    ``column`` в историю не пишется (карточки «Правила» адресуются по
    ``rule_code``). Принимает ``Any`` вместо ``lint.Violation``, чтобы не тянуть
    сюда опциональный extra ``[lint]`` (модуль ``core/lint`` грузится только при
    наличии ruff).
    """
    return [
        history.LintRecord(rule_code=v.rule_code, line_no=v.line_no, message=v.message)
        for v in violations
    ]


def default_history_db_path() -> Path:
    """Путь БД истории в текущей рабочей папке (issue #344/#395)."""
    return Path.cwd() / history.HISTORY_DB_NAME
