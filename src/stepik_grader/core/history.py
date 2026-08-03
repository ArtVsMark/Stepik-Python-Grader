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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from stepik_grader import db

__all__ = [
    "CORRECTNESS_MODES",
    "HISTORY_DB_NAME",
    "SCHEMA_VERSION",
    "CaseRecord",
    "HistoryRepository",
    "LintRecord",
    "RunRecord",
    "SqliteHistoryRepository",
    "purge_history",
    "read_recent_runs",
    "read_task_progress",
    "record_run",
]

HISTORY_DB_NAME = ".grader_history.db"
SCHEMA_VERSION = 2

# Режимы проверки корректности. Режимы 3/4 — бенчмарк: их вердикты
# (``ERR``/``SIMILAR``/``SLOWER``) отвечают на вопрос «быстрее ли», а не «верно
# ли», поэтому в метриках обучения (серия, попытки до первого зачёта) они не
# участвуют — ни как успех, ни как провал (issue #819).
CORRECTNESS_MODES = frozenset({1, 2})

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


@dataclass(frozen=True)
class RunRecord:
    """Один прогон как доменная модель (ADR-0009).

    Явный тип вместо россыпи параметров ``record_run`` — единственная доменная
    модель прогона для обоих бэкендов (SQLite локально, PostgreSQL на сервере:
    серверная схема добавит tenancy-колонки как надмножество, а не параллельный
    тип). ``cases``/``lint`` — вложенные ``CaseRecord``/``LintRecord``. ``mode``
    — 1..4; ``source`` — ``'cli'``/``'web'``; ``task_key`` — относительный путь
    папки задачи (``''`` если её нет).
    """

    mode: int
    cases: list[CaseRecord]
    source: str = "cli"
    task_key: str = ""
    solution_name: str | None = None
    solution_hash: str | None = None
    duration_s: float | None = None
    lint: list[LintRecord] = field(default_factory=list)


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

_SCHEMA_V1_VERSION = 1

# DDL схемы v2 (issue #819): агрегат «попыток/времени до первого зачёта» на
# задачу. Считать его на лету по таблице ``runs`` было нельзя: retention
# (``_MAX_RUNS_PER_TASK``) удаляет ранние прогоны, и ``runs[0]`` переставал быть
# первой попыткой — метрика тихо занижалась у самых активных пользователей,
# ровно у тех, ради кого она заведена. Агрегат монотонен: удаление прогонов его
# не трогает, потому что он не пересчитывается по ним.
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS task_progress (
    task_key             TEXT    PRIMARY KEY,
    first_ts_utc         TEXT    NOT NULL,
    runs_total           INTEGER NOT NULL DEFAULT 0,
    first_ac_ts_utc      TEXT,
    attempts_to_first_ac INTEGER
);
"""


def _utc_now_iso() -> str:
    """Текущее время UTC в ISO-8601 (``2026-07-14T09:12:00Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _backfill_task_progress(conn: sqlite3.Connection) -> None:
    """Заполнить агрегат v2 по уже накопленным прогонам (миграция 1→2).

    Для существующих БД восстанавливаем метрику из того, что ещё лежит в
    ``runs``: точнее уже не будет (вытесненные retention'ом прогоны потеряны
    безвозвратно), но дальше агрегат ведётся инкрементально и больше не
    зависит от удаления.
    """
    rows = conn.execute(
        "SELECT r.id, r.ts_utc, r.task_key, "
        "       (SELECT COUNT(*) FROM case_results c WHERE c.run_id = r.id) AS cases_total, "
        "       (SELECT COUNT(*) FROM case_results c WHERE c.run_id = r.id "
        "        AND c.verdict = 'AC') AS cases_ac "
        f"FROM runs r WHERE r.mode IN ({','.join(str(m) for m in sorted(CORRECTNESS_MODES))}) "
        "ORDER BY r.id"
    ).fetchall()
    for row in rows:
        run_id, ts_utc, task_key, cases_total, cases_ac = row
        del run_id
        _bump_task_progress(
            conn,
            task_key=task_key,
            ts_utc=ts_utc,
            full_ac=bool(cases_total) and cases_total == cases_ac,
        )


def _bump_task_progress(
    conn: sqlite3.Connection, *, task_key: str, ts_utc: str, full_ac: bool
) -> None:
    """Учесть один прогон режима 1/2 в агрегате задачи (issue #819).

    ``first_ts_utc`` пишется один раз (первая попытка), ``runs_total`` растёт,
    а ``first_ac_ts_utc``/``attempts_to_first_ac`` фиксируются на первом полном
    AC и больше не меняются — «первый зачёт» по определению случается однажды.
    """
    conn.execute(
        "INSERT INTO task_progress "
        "    (task_key, first_ts_utc, runs_total, first_ac_ts_utc, attempts_to_first_ac) "
        "VALUES (?, ?, 1, ?, ?) "
        "ON CONFLICT(task_key) DO UPDATE SET "
        "    runs_total = runs_total + 1, "
        "    first_ac_ts_utc = COALESCE(first_ac_ts_utc, excluded.first_ac_ts_utc), "
        "    attempts_to_first_ac = COALESCE("
        "        attempts_to_first_ac, "
        "        CASE WHEN excluded.first_ac_ts_utc IS NOT NULL THEN runs_total + 1 END)",
        (task_key, ts_utc, ts_utc if full_ac else None, 1 if full_ac else None),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция ``user_version`` 0→``SCHEMA_VERSION`` (#135).

    Делегирует общему ``db.apply_schema`` (issue #552) схему v1, затем
    инкрементально доводит до v2 (агрегат ``task_progress``, issue #819) с
    разовым заполнением по имеющимся прогонам. DDL идемпотентен (``CREATE ...
    IF NOT EXISTS``), поэтому параллельная инициализация двумя процессами
    безопасна (#393).
    """
    db.apply_schema(conn, version=_SCHEMA_V1_VERSION, ddl=_SCHEMA_V1)
    if db.user_version(conn) >= SCHEMA_VERSION:
        return
    conn.executescript(_SCHEMA_V2)
    _backfill_task_progress(conn)
    db.set_user_version(conn, SCHEMA_VERSION)
    conn.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Открыть соединение (общий ``db.connect``: WAL + FK + busy_timeout) и
    домигрировать до ``SCHEMA_VERSION`` (issue #552).

    ``sqlite3.connect`` создаёт файл БД — вызывать только на пути записи или
    после проверки ``db_path.is_file()`` на пути чтения (чтобы не плодить БД
    при выключенной истории, #134).
    """
    return db.connect(db_path, migrate=_migrate)


@runtime_checkable
class HistoryRepository(Protocol):
    """Абстракция хранилища истории (ADR-0009): одна доменная модель, разные бэкенды.

    ``SqliteHistoryRepository`` — локальный бэкенд (фаза 0, файл на пользователя);
    серверный PostgreSQL-бэкенд (фаза 3) встанет рядом той же поверхностью, а не
    переписыванием доменной логики истории/insights — аналогично тому, как
    ``Runner`` абстрагирует исполнение (ADR-0001).
    """

    def add_run(
        self, record: RunRecord, *, max_runs_per_task: int | None = _MAX_RUNS_PER_TASK
    ) -> int | None:
        """Записать прогон, вернуть его id (или ``None`` при best-effort сбое)."""
        ...

    def recent_runs(self, *, task_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Последние прогоны (новые первыми) с вложенными ``cases``/``lint``."""
        ...

    def task_progress(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Агрегат «попыток/времени до первого зачёта» по задачам (issue #819)."""
        ...


@dataclass(frozen=True)
class SqliteHistoryRepository:
    """Локальный SQLite-бэкенд истории (ADR-0009, фаза 0).

    Оборачивает прежний module-функциональный путь БЕЗ смены поведения: тот же
    файл ``.grader_history.db``, та же схема/миграции, тот же retention (#642),
    тот же best-effort и та же межпоточная сериализация. Использует module-level
    ``_connect``/``_WRITE_LOCK``/``_MAX_RUNS_PER_TASK`` bare-именами — тесты патчат
    их через ``history.X`` (late-binding сохранён).
    """

    db_path: Path

    def add_run(
        self, record: RunRecord, *, max_runs_per_task: int | None = _MAX_RUNS_PER_TASK
    ) -> int | None:
        """Записать один прогон (+ cases и lint) в SQLite; вернуть ``run_id``.

        ``max_runs_per_task`` (issue #642) — retention: после вставки для
        ``record.task_key`` остаётся не более ``max_runs_per_task`` последних
        прогонов, старые удаляются (FK ``ON DELETE CASCADE`` уносит их
        ``case_results``/``lint_violations``). ``None`` отключает retention.

        Best-effort: любая ``sqlite3.Error``/``OSError`` тихо проглатывается
        (возврат ``None``), как у ``stats.record_run``/``GraderCache`` — история
        не должна ронять грейдинг.
        """
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                ts_utc = _utc_now_iso()
                with _WRITE_LOCK, contextlib.closing(_connect(self.db_path)) as conn:
                    cur = conn.execute(
                        "INSERT INTO runs (ts_utc, mode, source, task_key, solution_name, "
                        "solution_hash, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            ts_utc,
                            record.mode,
                            record.source,
                            record.task_key,
                            record.solution_name,
                            record.solution_hash,
                            record.duration_s,
                        ),
                    )
                    run_id = cur.lastrowid
                    if record.cases:
                        conn.executemany(
                            "INSERT INTO case_results (run_id, case_no, verdict, time_ms, "
                            "error_class, failure_kind) VALUES (?, ?, ?, ?, ?, ?)",
                            [
                                (
                                    run_id,
                                    c.case_no,
                                    c.verdict,
                                    c.time_ms,
                                    c.error_class,
                                    c.failure_kind,
                                )
                                for c in record.cases
                            ],
                        )
                    if record.lint:
                        conn.executemany(
                            "INSERT INTO lint_violations (run_id, rule_code, line_no, message) "
                            "VALUES (?, ?, ?, ?)",
                            [(run_id, v.rule_code, v.line_no, v.message) for v in record.lint],
                        )
                    if record.mode in CORRECTNESS_MODES:
                        # issue #819: агрегат «до первого зачёта» ведётся только по
                        # прогонам проверки. Бенчмарк (режимы 3/4) не участвует ни
                        # как попытка, ни как провал — у него другой вопрос.
                        _bump_task_progress(
                            conn,
                            task_key=record.task_key,
                            ts_utc=ts_utc,
                            full_ac=bool(record.cases)
                            and all(c.verdict == "AC" for c in record.cases),
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
                            (record.task_key, record.task_key, max_runs_per_task),
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

    def recent_runs(self, *, task_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Последние прогоны (новые первыми) с вложенными ``cases``.

        Отсутствующая/битая БД → пустой список (graceful, не ошибка); файл не
        создаётся, если его нет (#134). Фильтр по ``task_key`` опционален.
        """
        if not self.db_path.is_file():
            return []
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
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
                        f"FROM case_results WHERE run_id IN ({placeholders}) "
                        "ORDER BY run_id, case_no",
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

    def task_progress(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Агрегат «до первого зачёта» по задачам, по ``task_key`` (issue #819).

        Считается инкрементально при записи прогонов режимов 1/2, поэтому не
        зависит ни от retention (``_MAX_RUNS_PER_TASK``), ни от того, сколько
        последних прогонов прочитано: удаление старых записей больше не
        занижает «попыток до первого зачёта». Отсутствующая/битая БД → пустой
        список (graceful, как у ``recent_runs``).
        """
        if not self.db_path.is_file():
            return []
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT task_key, first_ts_utc, runs_total, first_ac_ts_utc, "
                    "attempts_to_first_ac FROM task_progress ORDER BY task_key LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []


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
    """Записать один прогон в локальную историю; вернуть ``run_id`` (или ``None``).

    Тонкая обёртка над ``SqliteHistoryRepository.add_run`` (ADR-0009): собирает
    параметры в доменный ``RunRecord`` и делегирует локальному бэкенду. Сигнатура
    сохранена для существующих вызывающих (``cli``/``web``) и тестов — поведение,
    retention (#642) и best-effort те же. Путь передаётся явно (opt-in, #134).
    """
    return SqliteHistoryRepository(db_path).add_run(
        RunRecord(
            mode=mode,
            cases=cases,
            source=source,
            task_key=task_key,
            solution_name=solution_name,
            solution_hash=solution_hash,
            duration_s=duration_s,
            lint=lint or [],
        ),
        max_runs_per_task=max_runs_per_task,
    )


def read_recent_runs(
    db_path: Path,
    *,
    task_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Последние прогоны (новые первыми) с вложенными ``cases``.

    Тонкая обёртка над ``SqliteHistoryRepository.recent_runs`` (ADR-0009).
    Сигнатура сохранена для тестов (roundtrip) и раздела «Подучить» (#347).
    """
    return SqliteHistoryRepository(db_path).recent_runs(task_key=task_key, limit=limit)


def task_key_for(task_dir: Path, base: Path) -> str:
    """Ключ задачи для истории: путь относительно ``base``, но никогда «.»/«..».

    issue #817: ключ считался как путь папки задачи относительно каталога
    запуска, а рекомендованный сценарий («Первый пример за 2 минуты») —
    запуск ИЗ папки задачи. Тогда ключом становилась точка, и в «Прогрессе»
    все задачи назывались «.», схлопываясь в одну строку: TTFG и «попытки»
    считались по смеси разных задач. Запуск из подпапки (``tests/``) давал
    ключ «..» с тем же эффектом.

    Правило: путь относительно ``base``, если он «внутрь» (``module1/04-slug``);
    иначе — собственное имя папки задачи, одинаковое при любом каталоге
    запуска. Имени нет только у корня ФС — тогда отдаём путь как есть.
    """
    try:
        rel = task_dir.relative_to(base, walk_up=True)
    except ValueError:
        # Разные anchor'ы (относительный путь против абсолютного, разные диски
        # на Windows) — отдаём как есть, а не роняем запись истории (issue #440).
        return str(task_dir)
    parts = rel.parts
    if parts and parts[0] != "..":
        return str(rel)
    return task_dir.resolve().name or str(rel)


def read_task_progress(db_path: Path, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Агрегат «попыток/времени до первого зачёта» по задачам (issue #819).

    Тонкая обёртка над ``SqliteHistoryRepository.task_progress`` (ADR-0009) —
    источник метрик ``core/insights.time_to_first_green``.
    """
    return SqliteHistoryRepository(db_path).task_progress(limit=limit)


def purge_history(db_path: Path, *, task_key: str | None = None) -> int:
    """Удалить историю прогонов; вернуть число удалённых прогонов (issue #813).

    ``task_key=None`` — удалить БД целиком вместе с ``-wal``/``-shm``
    спутниками: у пользователя должен быть способ убрать журнал обучения, не
    зная о существовании этих файлов. С ``task_key`` удаляются прогоны только
    этой задачи (``cases``/``lint`` уходят каскадом по FK), а база остаётся.

    Best-effort, как и вся работа с историей: отсутствующая БД — это 0
    удалённых, а не ошибка.
    """
    if not db_path.is_file():
        return 0
    if task_key is None:
        with contextlib.closing(db.connect(db_path)) as conn:
            removed = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
        return removed
    with contextlib.closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM runs WHERE task_key = ?", (task_key,))
        # issue #819 + #813: агрегат «до первого зачёта» живёт отдельной строкой
        # и каскадом FK не уносится — без этого «историю удалили» оставляло бы
        # число попыток и время первого зачёта по задаче лежать в базе.
        conn.execute("DELETE FROM task_progress WHERE task_key = ?", (task_key,))
        conn.commit()
        # VACUUM возвращает место ОС: иначе удалённые записи остаются
        # вычитываемыми из файла сырым чтением, а обещание «удалили» — неполным.
        conn.execute("VACUUM")
        return int(cursor.rowcount or 0)
