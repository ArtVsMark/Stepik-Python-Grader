"""history.py — opt-in локальная SQLite-история прогонов (issue #344, эпик #342).

Архитектурный слой: Infrastructure / Utilities. Leaf-модуль на stdlib
``sqlite3`` (как ``core/cache.py``/``core/stats.py``) — фундамент Э0 из
``docs/audit-2026-07.md`` § 9.2: локальная база ``.grader_history.db`` с
историей прогонов (``runs``), per-case результатами (``case_results``) и
lint-нарушениями (``lint_violations``). На ней строятся разделы «Правила/PEP»
и «Подучить» (#345–#349).

Почему SQLite, а не JSON Lines как ``stats.py``: разделу «Подучить» нужны
агрегатные выборки по окну последних N прогонов (затухание карточек, § 9.3) —
это ``GROUP BY``/оконные запросы, а не линейное чтение журнала. ``WAL`` снимает
межпроцессную гонку CLI+web, которую ``_WRITE_LOCK`` в ``stats.py`` закрыть не
может (см. комментарий про #344 в ``stats.py``).

Best-effort по всему модулю (принцип ``GraderCache``/``stats``): битая БД,
нет прав, полный диск — тихо пропустить запись/вернуть пусто, никогда не
ронять грейдинг. Opt-in: путь передаётся явно (``db_path=``); по умолчанию
модуль ничего не создаёт (требование #134 — CLI без БД, пока не задан
``--history``). Таксономия ``failure_kind`` (§ 9.3) и наполнение
``lint_violations`` — за будущими #347/#346; здесь колонки лишь заводятся.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "HISTORY_DB_NAME",
    "SCHEMA_VERSION",
    "CaseRecord",
    "LintRecord",
    "record_run",
    "read_recent_runs",
    "hash_solution",
]

HISTORY_DB_NAME = ".grader_history.db"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CaseRecord:
    """Один per-case результат для записи в ``case_results``.

    ``failure_kind`` (таксономия § 9.3) заполняет #347 — в #344 по умолчанию
    ``None``.
    """

    case_no: int
    verdict: str
    time_ms: float | None = None
    error_class: str | None = None
    failure_kind: str | None = None


@dataclass(frozen=True)
class LintRecord:
    """Одно lint-нарушение для ``lint_violations`` (наполняет #346)."""

    rule_code: str
    line_no: int
    message: str | None = None


# DDL схемы v1 (канон — docs/audit-2026-07.md § 9.2). Применяется миграцией
# user_version 0→1; менять существующую версию нельзя — только добавлять v2+.
_SCHEMA_V1 = """
CREATE TABLE runs (
    id            INTEGER PRIMARY KEY,
    ts_utc        TEXT    NOT NULL,
    mode          INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    task_key      TEXT    NOT NULL,
    solution_name TEXT,
    solution_hash TEXT,
    duration_s    REAL
);
CREATE TABLE case_results (
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_no      INTEGER NOT NULL,
    verdict      TEXT    NOT NULL,
    time_ms      REAL,
    error_class  TEXT,
    failure_kind TEXT,
    PRIMARY KEY (run_id, case_no)
);
CREATE TABLE lint_violations (
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    rule_code TEXT    NOT NULL,
    line_no   INTEGER NOT NULL,
    message   TEXT
);
CREATE INDEX idx_runs_task  ON runs(task_key, id);
CREATE INDEX idx_cases_kind ON case_results(failure_kind);
CREATE INDEX idx_lint_rule  ON lint_violations(rule_code);
"""


def hash_solution(code: str) -> str:
    """sha256 текста решения (для колонки ``runs.solution_hash``)."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601 (``2026-07-14T09:12:00Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция ``user_version`` 0→``SCHEMA_VERSION`` (#135).

    ``user_version=0`` (свежая/пустая БД) → создаём схему v1 и ставим версию.
    Актуальная или новее — no-op (повторный вызов безопасен). Будущие версии
    добавляют свои ветки здесь, не трогая ``_SCHEMA_V1``.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version == 0:
        conn.executescript(_SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Открыть соединение, включить WAL/FK и домигрировать до ``SCHEMA_VERSION``.

    ``sqlite3.connect`` создаёт файл БД — вызывать только на пути записи или
    после проверки ``db_path.is_file()`` на пути чтения (чтобы не плодить БД
    при выключенной истории, #134).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def record_run(
    mode: int,
    cases: list[CaseRecord],
    *,
    db_path: Path,
    source: str = "cli",
    task_key: str = "",
    solution_name: str | None = None,
    solution_hash: str | None = None,
    duration_s: float | None = None,
    lint: list[LintRecord] | None = None,
) -> int | None:
    """Записать один прогон (+ его cases и lint) в историю; вернуть ``run_id``.

    ``mode`` — 1..4; ``source`` — ``'cli'``/``'web'``; ``task_key`` —
    относительный путь папки задачи (``''`` если её нет). ``cases`` — per-case
    результаты (для режимов 3/4 — по решению, verdict бенчмарка). ``lint``
    пуст в #344 (наполнит #346).

    Best-effort: любая ``sqlite3.Error``/``OSError`` тихо проглатывается
    (возврат ``None``), как у ``stats.record_run``/``GraderCache`` — история
    не должна ронять грейдинг. Путь передаётся явно (opt-in, #134): вызывающая
    сторона включает историю флагом ``--history``/конфигом.
    """
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            cur = conn.execute(
                "INSERT INTO runs (ts_utc, mode, source, task_key, solution_name, "
                "solution_hash, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_utc_now_iso(), mode, source, task_key, solution_name, solution_hash, duration_s),
            )
            run_id = cur.lastrowid
            if cases:
                conn.executemany(
                    "INSERT INTO case_results (run_id, case_no, verdict, time_ms, "
                    "error_class, failure_kind) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (run_id, c.case_no, c.verdict, c.time_ms, c.error_class, c.failure_kind)
                        for c in cases
                    ],
                )
            if lint:
                conn.executemany(
                    "INSERT INTO lint_violations (run_id, rule_code, line_no, message) "
                    "VALUES (?, ?, ?, ?)",
                    [(run_id, v.rule_code, v.line_no, v.message) for v in lint],
                )
            conn.commit()
            return run_id
    except (sqlite3.Error, OSError):
        return None


def read_recent_runs(
    db_path: Path,
    *,
    task_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Последние прогоны (новые первыми) с вложенными ``cases``.

    Отсутствующая/битая БД → пустой список (graceful, не ошибка); файл не
    создаётся, если его нет (#134). Фильтр по ``task_key`` опционален.
    Используется тестами (roundtrip) и будущим разделом «Подучить» (#347).
    """
    if not db_path.is_file():
        return []
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            clause = "WHERE task_key = ?" if task_key is not None else ""
            head = (task_key,) if task_key is not None else ()
            rows = conn.execute(
                f"SELECT * FROM runs {clause} ORDER BY id DESC LIMIT ?",
                (*head, limit),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                cases = conn.execute(
                    "SELECT case_no, verdict, time_ms, error_class, failure_kind "
                    "FROM case_results WHERE run_id = ? ORDER BY case_no",
                    (row["id"],),
                ).fetchall()
                run = dict(row)
                run["cases"] = [dict(c) for c in cases]
                lint = conn.execute(
                    "SELECT rule_code FROM lint_violations WHERE run_id = ? ORDER BY rule_code",
                    (row["id"],),
                ).fetchall()
                run["lint"] = [r["rule_code"] for r in lint]
                result.append(run)
            return result
    except (sqlite3.Error, OSError):
        return []
