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

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core import glossary, history, insights
from stepik_grader.core.result import BenchResult, CaseResult
from stepik_grader.core.runprofile import current_profile

# issue #818: единая пользовательская база — одна на человека, а не на папку.
# Каталог с точкой, как у остальных пользовательских данных инструментов CLI;
# имя без точки — внутри скрытого каталога она уже не нужна.
_USER_HISTORY_DIR = ".stepik-grader"
_USER_HISTORY_FILE = "history.db"

# issue #818: аварийный переключатель пути к базе. Нужен там, где менять
# pyproject.toml нельзя или опасно — CI, контейнеры и, главное, СОБСТВЕННЫЙ
# набор тестов: с единой пользовательской базой прогон начал писать (и, через
# `--purge-history`, удалять) РЕАЛЬНЫЕ данные в домашней папке. Тот же приём,
# что у `STEPIK_GRADER_CONFIG`.
_ENV_HISTORY_DB = "STEPIK_GRADER_HISTORY_DB"

__all__ = [
    "cases_from_bench_results",
    "cases_from_test_results",
    "current_isolation",
    "default_history_db_path",
    "find_existing_history_db",
    "lint_records_from_violations",
    "user_history_db_path",
]


def current_isolation() -> str:
    """Чем исполняется прогон прямо сейчас (issue #1220).

    ``history.ISOLATION_NONE`` — обычный ``LocalRunner`` без ОС-изоляции; иначе
    имя backend'а песочницы (``bwrap``/``sandbox-exec``/``job-objects``).
    Backend назван, а не свёрнут в булев «под песочницей»: гарантии у трёх
    реализаций разные (SECURITY.md), и «изолировано» без уточнения — обещание,
    которого ни один из них по отдельности не даёт.

    Читается в момент вызова: ``--sandbox`` подменяет runner уже после загрузки
    модулей, поэтому снимок на импорте всегда врал бы «без изоляции».
    """
    backend = current_profile().sandbox_backend
    return backend or history.ISOLATION_NONE


def cases_from_test_results(cases: list[CaseResult]) -> list[history.CaseRecord]:
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
    results: Mapping[Path, BenchResult],
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


def user_history_db_path() -> Path:
    """Единая база истории пользователя — ``~/.stepik-grader/history.db`` (#818)."""
    return Path.home() / _USER_HISTORY_DIR / _USER_HISTORY_FILE


def find_existing_history_db(start: Path | None = None) -> Path | None:
    """Ближайшая существующая ``.grader_history.db`` от ``start`` вверх (#818).

    Нужна ради обратной совместимости: у тех, кто уже накопил историю в рабочей
    папке, она обязана продолжать пополняться, а не осиротеть при обновлении.
    Заодно покрывает нормальный учебный случай — база в корне курса, а запуск
    из папки конкретной задачи.

    Обход ограничен строго ВНУТРЕННОСТЬЮ домашней папки — сама ``home`` в
    цепочку не входит. Без границы поиск доходил до корня диска и цеплял
    постороннюю базу: из временного каталога под ``~/AppData`` находилась
    ``~/.grader_history.db``, из-за чего десять тестов web-слоя начали читать и
    писать РЕАЛЬНУЮ базу пользователя. Именно ``home`` исключена по той же
    причине: файл в корне домашней папки перехватывал бы любой запуск, где бы
    он ни происходил.

    Если рабочая папка вне ``home`` вовсе, смотрим только её саму: угадывать
    общий корень для ``/opt`` или сетевого диска — значит снова хватать чужое.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    if home in current.parents:
        chain = [current, *(p for p in current.parents if home in p.parents)]
    else:
        chain = [current]
    for folder in chain:
        candidate = folder / history.HISTORY_DB_NAME
        if candidate.is_file():
            return candidate
    return None


def default_history_db_path() -> Path:
    """Куда писать историю обучения (issue #344/#395, переработано в #818).

    Порядок:

    0. Переменная окружения ``STEPIK_GRADER_HISTORY_DB`` — аварийный
       переключатель для CI/контейнеров и изоляции собственных тестов.
    1. ``CONFIG.history_db_path`` — явная настройка. Относительный путь
       резолвится от текущей папки, поэтому прежнее поведение возвращается
       строкой ``".grader_history.db"``.
    2. Существующая ``.grader_history.db`` рядом или выше по дереву — чтобы
       уже накопленная история продолжала пополняться после обновления.
    3. ``~/.stepik-grader/history.db`` — единая база пользователя.

    Зачем это изменено: база лежала строго в ``Path.cwd()``, а рекомендованный
    сценарий (docs/use/grader-workflow.md) — запуск из папки задачи. Значит у
    студента на каждую задачу заводилась своя база, и «Подучить», «Прогресс»,
    серия и бейджи не наполнялись никогда. Проверено прогоном: два прогона из
    соседних папок дали два файла по 40 КБ, а `--insights` из второй показал
    одну задачу вместо двух.
    """
    override = os.environ.get(_ENV_HISTORY_DB, "").strip()
    if override:
        return Path(override).expanduser()
    configured = str(getattr(CONFIG, "history_db_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    existing = find_existing_history_db()
    if existing is not None:
        return existing
    return user_history_db_path()
