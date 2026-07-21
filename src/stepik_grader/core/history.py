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

Рост БД ограничен retention (issue #642): ``record_run`` держит не более
``_MAX_RUNS_PER_TASK`` последних прогонов на ``task_key`` — backstop, которого
у истории (в отличие от ``cache.py``/``stats.py``) раньше не было.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stepik_grader import db

__all__ = [
    "HISTORY_DB_NAME",
    "SCHEMA_VERSION",
    "CaseRecord",
    "LintRecord",
    "read_recent_runs",
    "record_run",
]

HISTORY_DB_NAME = ".grader_history.db"
SCHEMA_VERSION = 1

# Процесс-локальная сериализация записи (как ``_WRITE_LOCK`` в ``stats.py``,
# issue #605/#393). ``WAL`` снимает МЕЖпроцессную гонку CLI+web, но на Windows
# при барьерной ПЕРВОЙ инициализации свежей БД WAL не включается (нельзя, пока
# открыты другие соединения) — БД падает в rollback-journal с грубыми
# блокировками, и ВНУТРИпроцессные конкурентные писатели (40 потоков в тесте;
# реально — CLI-грейд и web-воркеры в одном процессе) изредка теряли запись
# через best-effort ``except``. Лок сериализует их детерминированно; как бонус,
# при сериализации соединения открываются по одному и WAL успевает включиться.
_WRITE_LOCK = threading.Lock()

# Короткий retry записи при транзиентной блокировке БД перед best-effort сдачей
# (issue #605): МЕЖпроцессную гонку (отдельные процессы лока не разделяют)
# ``busy_timeout`` покрывает не всегда — повтор ловит остаток вместо тихой потери.
_WRITE_ATTEMPTS = 3
_WRITE_RETRY_DELAY_S = 0.1

# issue #642: backstop роста БД истории. У ``cache.py`` есть cap
# ``_CACHE_MAX_ENTRIES=512``, у ``stats.py`` — ротация по ``_MAX_BYTES``; у
# истории backstop'а не было — ``record_run`` только INSERT'ил, без ``DELETE``/
# лимита, и файл рос линейно (особенно на сервере, где web пишет ``source='web'``
# по всем пользователям: раздувание файла, деградация ``idx_runs_task`` и
# GROUP BY-выборок «Подучить», рост ``-wal``). Держим не более
# ``_MAX_RUNS_PER_TASK`` последних прогонов на ``task_key``. Раздел «Подучить»
# смотрит окно последних N (``CONFIG.insights_window_n`` ≪ этого cap), поэтому
# удержание не режет инсайты. Значение переопределяется параметром
# ``record_run(max_runs_per_task=...)`` — на сервере его задают явно.
_MAX_RUNS_PER_TASK = 200


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
# ``IF NOT EXISTS`` на всех CREATE (issue #393): при одновременной инициализации
# свежей БД двумя процессами оба видят user_version=0, но идемпотентный DDL не
# роняет второго ``OperationalError: table runs already exists`` (иначе запись
# тихо терялась через best-effort except в record_run).
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    ts_utc        TEXT    NOT NULL,
    mode          INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    task_key      TEXT    NOT NULL,
    solution_name TEXT,
    solution_hash TEXT,
    duration_s    REAL
);
CREATE TABLE IF NOT EXISTS case_results (
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_no      INTEGER NOT NULL,
    verdict      TEXT    NOT NULL,
    time_ms      REAL,
    error_class  TEXT,
    failure_kind TEXT,
    PRIMARY KEY (run_id, case_no)
);
CREATE TABLE IF NOT EXISTS lint_violations (
    run_id    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    rule_code TEXT    NOT NULL,
    line_no   INTEGER NOT NULL,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_task  ON runs(task_key, id);
CREATE INDEX IF NOT EXISTS idx_cases_kind ON case_results(failure_kind);
CREATE INDEX IF NOT EXISTS idx_lint_rule  ON lint_violations(rule_code);
"""


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601 (``2026-07-14T09:12:00Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция ``user_version`` 0→``SCHEMA_VERSION`` (#135).

    Делегирует общему ``db.apply_schema`` (issue #552): ``user_version=0``
    (свежая/пустая БД) → создаём схему v1 и ставим версию; актуальная/новее —
    no-op. DDL идемпотентен (``CREATE ... IF NOT EXISTS``), поэтому параллельная
    инициализация двумя процессами безопасна (#393). Будущие инкрементальные
    версии (1→2) добавляют свою миграцию поверх этого вызова.
    """
    db.apply_schema(conn, version=SCHEMA_VERSION, ddl=_SCHEMA_V1)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Открыть соединение (общий ``db.connect``: WAL + FK + busy_timeout) и
    домигрировать до ``SCHEMA_VERSION`` (issue #552).

    ``sqlite3.connect`` создаёт файл БД — вызывать только на пути записи или
    после проверки ``db_path.is_file()`` на пути чтения (чтобы не плодить БД
    при выключенной истории, #134).
    """
    return db.connect(db_path, migrate=_migrate)


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
    max_runs_per_task: int | None = _MAX_RUNS_PER_TASK,
) -> int | None:
    """Записать один прогон (+ его cases и lint) в историю; вернуть ``run_id``.

    ``mode`` — 1..4; ``source`` — ``'cli'``/``'web'``; ``task_key`` —
    относительный путь папки задачи (``''`` если её нет). ``cases`` — per-case
    результаты (для режимов 3/4 — по решению, verdict бенчмарка). ``lint``
    пуст в #344 (наполнит #346).

    ``max_runs_per_task`` (issue #642) — retention: после вставки для этого
    ``task_key`` остаётся не более ``max_runs_per_task`` последних прогонов,
    старые удаляются (FK ``ON DELETE CASCADE`` уносит их ``case_results``/
    ``lint_violations``). ``None`` отключает retention (например, для тестов или
    осознанно безлимитного хранения). По умолчанию — ``_MAX_RUNS_PER_TASK``;
    на сервере лимит задают явно.

    Best-effort: любая ``sqlite3.Error``/``OSError`` тихо проглатывается
    (возврат ``None``), как у ``stats.record_run``/``GraderCache`` — история
    не должна ронять грейдинг. Путь передаётся явно (opt-in, #134): вызывающая
    сторона включает историю флагом ``--history``/конфигом.
    """
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            with _WRITE_LOCK, contextlib.closing(_connect(db_path)) as conn:
                cur = conn.execute(
                    "INSERT INTO runs (ts_utc, mode, source, task_key, solution_name, "
                    "solution_hash, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        mode,
                        source,
                        task_key,
                        solution_name,
                        solution_hash,
                        duration_s,
                    ),
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
                if max_runs_per_task is not None:
                    # issue #642: retention — оставить не более max_runs_per_task
                    # последних прогонов для ЭТОГО task_key (рост БД идёт через
                    # него, поэтому полный скан не нужен). OFFSET-подзапрос по
                    # монотонному id находит границу «(cap+1)-й с конца»: всё с
                    # id ≤ неё — на удаление; FK ON DELETE CASCADE уносит их
                    # case_results/lint_violations. Прогонов ≤ cap → подзапрос
                    # даёт NULL, `id <= NULL` ложно — не удаляется ничего.
                    conn.execute(
                        "DELETE FROM runs WHERE task_key = ? AND id <= "
                        "(SELECT id FROM runs WHERE task_key = ? "
                        "ORDER BY id DESC LIMIT 1 OFFSET ?)",
                        (task_key, task_key, max_runs_per_task),
                    )
                conn.commit()
                return run_id
        except sqlite3.OperationalError:
            # Транзиентная блокировка БД (SQLITE_BUSY/"database is locked") —
            # межпроцессная гонка мимо _WRITE_LOCK; короткий backoff и повтор,
            # best-effort сдача только после исчерпания попыток (issue #605).
            if attempt == _WRITE_ATTEMPTS - 1:
                return None
            time.sleep(_WRITE_RETRY_DELAY_S * (attempt + 1))
        except (sqlite3.Error, OSError):
            return None
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
            run_ids = [row["id"] for row in rows]
            # Один запрос на cases и один на lint для ВСЕХ выбранных прогонов
            # вместо N+1 (по запросу на каждый run, issue #553). Контракт не
            # меняется: cases по case_no, lint по rule_code, прогоны по id DESC.
            cases_by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
            lint_by_run: dict[int, list[str]] = defaultdict(list)
            if run_ids:
                placeholders = ",".join("?" * len(run_ids))
                for case in conn.execute(
                    "SELECT run_id, case_no, verdict, time_ms, error_class, failure_kind "
                    f"FROM case_results WHERE run_id IN ({placeholders}) ORDER BY run_id, case_no",
                    run_ids,
                ):
                    cases_by_run[case["run_id"]].append(
                        {
                            "case_no": case["case_no"],
                            "verdict": case["verdict"],
                            "time_ms": case["time_ms"],
                            "error_class": case["error_class"],
                            "failure_kind": case["failure_kind"],
                        }
                    )
                for violation in conn.execute(
                    "SELECT run_id, rule_code FROM lint_violations "
                    f"WHERE run_id IN ({placeholders}) ORDER BY run_id, rule_code",
                    run_ids,
                ):
                    lint_by_run[violation["run_id"]].append(violation["rule_code"])
            result: list[dict[str, Any]] = []
            for row in rows:
                run = dict(row)
                run["cases"] = cases_by_run.get(row["id"], [])
                run["lint"] = lint_by_run.get(row["id"], [])
                result.append(run)
            return result
    except (sqlite3.Error, OSError):
        return []
