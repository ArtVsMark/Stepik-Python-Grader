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

Схема v4 добавила три вещи: вердикт платформы по отправке решения
(``stepik_submissions``, issue #1175), признак изоляции прогона
(``runs.isolation``, issue #1220) и переходы в глоссарий из ошибки
(``glossary_hits``, issue #1220). Полный состав того, что пишется, перечислен
в ``docs/dev/rules-insights.md`` § «История прогонов»: «появилось молча» про
пользовательские данные недопустимо.

Best-effort по всему модулю (принцип ``GraderCache``/``stats``): битая БД,
нет прав, полный диск — пропустить запись/вернуть пусто, никогда не ронять
грейдинг. «Best-effort» ≠ «молча» (issue #794): неустранимое — повреждённый
файл, схема от более новой версии грейдера — один раз за процесс называется
вслух с путём и подсказкой, иначе «пишем в никуда» неотличимо от «истории
нет». Транзиентная блокировка по-прежнему тиха: её лечит повтор.
Opt-in: путь передаётся явно (``db_path=``); по умолчанию
модуль ничего не создаёт (требование #134 — CLI без БД, пока не задан
``--history``). Таксономия ``failure_kind`` (§ 9.3) и наполнение
``lint_violations`` — за будущими #347/#346; здесь колонки лишь заводятся.

Рост БД ограничен retention (issue #642): ``record_run`` держит не более
``_MAX_RUNS_PER_TASK`` последних прогонов на ``task_key`` — backstop, которого
у истории (в отличие от ``cache.py``/``stats.py``) раньше не было.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypedDict, runtime_checkable

from stepik_grader import db

__all__ = [
    "CORRECTNESS_MODES",
    "HISTORY_DB_NAME",
    "ISOLATION_NONE",
    "PLATFORM_VERDICTS",
    "SCHEMA_VERSION",
    "CaseRecord",
    "GlossaryHitRecord",
    "HistoryRepository",
    "LintRecord",
    "PurgePreview",
    "RunRecord",
    "SqliteHistoryRepository",
    "StepikSubmissionRecord",
    "preview_purge",
    "purge_history",
    "read_glossary_hits",
    "read_recent_runs",
    "read_stepik_submissions",
    "read_task_progress",
    "record_glossary_hit",
    "record_run",
    "record_stepik_submission",
]

HISTORY_DB_NAME = ".grader_history.db"
SCHEMA_VERSION = 4

# issue #1220: значение колонки ``runs.isolation`` для прогона БЕЗ ОС-изоляции.
# ``NULL`` в этой колонке означает другое — «неизвестно»: так читаются записи,
# сделанные до появления колонки. Разница существенна: «точно без песочницы» —
# это факт о прогоне, а «неизвестно» — отсутствие факта, и смешивать их значило
# бы задним числом приписать старым записям условия, которых никто не измерял.
ISOLATION_NONE = "none"

# Режимы проверки корректности. Режимы 3/4 — бенчмарк: их вердикты
# (``ERR``/``SIMILAR``/``SLOWER``) отвечают на вопрос «быстрее ли», а не «верно
# ли», поэтому в метриках обучения (серия, попытки до первого зачёта) они не
# участвуют — ни как успех, ни как провал (issue #819).
CORRECTNESS_MODES = frozenset({1, 2})

# Терминальные вердикты платформы (issue #1175). ``evaluation`` (проверка ещё
# идёт) и ``error`` (сбой отправки) сюда не входят: это не ответ платформы о
# решении, и сравнивать их с нашим вердиктом нечем — расхождение считалось бы
# по отсутствию данных.
PLATFORM_VERDICTS = frozenset({"correct", "wrong"})

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

# Вывод через rich с graceful fallback на print() (инвариант CLAUDE.md). Свой
# локальный ``_console``, а не ``core/reporter._console`` — модуль leaf-совместим
# и не тянет core-UI (тот же приём, что ``downloader_config``/``glossary/coverage``).
try:
    from rich.console import Console

    _console: Console | None = Console(stderr=True)
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False


def _print(text: str) -> None:
    """Печать предупреждения в stderr (markup off — путь к БД безопасен в любом виде).

    ``soft_wrap=True`` обязателен: без него rich ломает длинный путь переносом по
    ширине 80 (а вне терминала ширина именно такая), и скопировать имя файла из
    подсказки «удалите его» становится нельзя — подсказка обесценивается.
    """
    if _RICH and _console is not None:
        _console.print(text, markup=False, soft_wrap=True)
    else:
        print(text, file=sys.stderr)


# issue #794 (MIGR-03): подстроки сообщений SQLite о НЕУСТРАНИМОМ повреждении
# файла. Класс исключения тут не помощник — «database disk image is malformed» и
# «file is not a database» приезжают тем же ``sqlite3.DatabaseError``, что и
# транзиентные сбои, поэтому различаем по тексту.
_CORRUPT_MARKERS = (
    "malformed",
    "not a database",
    "file is encrypted",
    "database corrupt",
)

# Что уже сказано пользователю: (путь, вид проблемы). Один раз за процесс —
# иначе папка из 40 кейсов дала бы 40 одинаковых абзацев.
_WARNED_DB_PROBLEMS: set[tuple[str, str]] = set()
_WARN_LOCK = threading.Lock()

_PROBLEM_HINTS = {
    "too-new": (
        "Обновите грейдер до версии, которая её создала, или удалите файл — "
        "история заведётся заново."
    ),
    "corrupt": (
        "Файл повреждён и не читается. Удалите его — история заведётся заново "
        "(прежние прогоны восстановить нельзя)."
    ),
}


def _db_problem_kind(exc: BaseException) -> str | None:
    """Вид неустранимой проблемы БД или ``None``, если сбой транзиентный (#794)."""
    if isinstance(exc, db.SchemaTooNewError):
        return "too-new"
    if any(marker in str(exc).lower() for marker in _CORRUPT_MARKERS):
        return "corrupt"
    return None


def _note_unusable_db(db_path: Path, exc: BaseException) -> None:
    """Один раз за процесс сообщить, что БД истории непригодна (issue #794).

    Транзиентная блокировка не печатает ничего: её лечит повтор, и шум только
    мешал бы. Молчать про НЕустранимое нельзя — повреждённая база выглядела
    ровно как «истории нет», и пользователь прогон за прогоном писал в никуда.
    """
    kind = _db_problem_kind(exc)
    if kind is None:
        return
    key = (str(db_path), kind)
    with _WARN_LOCK:
        if key in _WARNED_DB_PROBLEMS:
            return
        _WARNED_DB_PROBLEMS.add(key)
    _print(f"⚠️  История прогонов недоступна: {db_path} — {exc}")
    _print(f"   {_PROBLEM_HINTS[kind]}")


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
    # issue #990: человеческое имя задачи (папка). Ключ стал идентификатором
    # шага, а в «Прогрессе» пользователь ищет задачу по имени, а не по номеру.
    task_title: str | None = None
    solution_name: str | None = None
    solution_hash: str | None = None
    duration_s: float | None = None
    lint: list[LintRecord] = field(default_factory=list)
    # issue #1220: чем исполнялся прогон — ``ISOLATION_NONE`` или имя
    # sandbox-backend'а (``bwrap``/``sandbox-exec``/``job-objects``). Гарантии у
    # backend'ов разные (SECURITY.md), поэтому «под песочницей» одним битом не
    # описывается. ``None`` — «неизвестно» (записи до появления колонки).
    isolation: str | None = None


@dataclass(frozen=True)
class StepikSubmissionRecord:
    """Вердикт платформы по одной отправке решения (issue #1175).

    Собирается на пути отправки в ядре, поэтому наполняется и из веба, и из
    любого будущего потребителя (CLI, оркестратор прогона, серверный режим).

    ``verdict`` — статус Stepik (``correct``/``wrong``); ``our_verdict`` — наш
    последний вердикт по задаче (``AC`` либо код первого упавшего кейса) или
    ``None``, если локального прогона не было. ``run_id`` связывает запись с
    нашим прогоном и может быть ``None``: отправить решение можно и не запуская
    проверку локально.
    """

    step_id: int
    verdict: str
    task_key: str = ""
    submission_id: int | None = None
    hint: str | None = None
    run_id: int | None = None
    our_verdict: str | None = None


@dataclass(frozen=True)
class GlossaryHitRecord:
    """Переход в карточку глоссария ИЗ ОШИБКИ прогона (issue #1220).

    Пишется только deep-link из разбора ошибки, а не любое открытие раздела:
    ценно использование по назначению («упал → понял»), а не листание
    справочника. ``failure_kind``/``error_class`` — ключ ошибки, из которой
    пришли; оба могут быть ``None`` (подсказка есть, таксономия не сработала).
    """

    card_id: str
    failure_kind: str | None = None
    error_class: str | None = None


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
_SCHEMA_V2_VERSION = 2


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Есть ли колонка в таблице (``PRAGMA table_info``).

    Нужна миграции 2→3: ``ALTER TABLE ... ADD COLUMN`` не имеет формы
    ``IF NOT EXISTS``, а инициализация может идти из двух процессов сразу.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


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


# DDL схемы v3 (issue #990): человеческое имя задачи рядом с её ключом. Ключом
# стал устойчивый идентификатор шага (``step:<id>``), а его нельзя показывать в
# «Прогрессе» вместо названия папки — пользователь узнаёт задачу по имени, а не
# по номеру шага. Колонка обновляется на каждом прогоне: папку могли
# переименовать, и последнее известное имя точнее первого.
_SCHEMA_V3 = """
ALTER TABLE task_progress ADD COLUMN display_name TEXT;
"""


# DDL схемы v4, часть 1 (issue #1175): вердикт платформы рядом с нашим. Раньше
# обратная связь Stepik собиралась однобоко — вердикты приезжали при скачивании
# (``core/submission_archive``), а результат живой отправки не сохранялся нигде,
# и сверка «наш вердикт против платформы» оставалась разовой акцией. С этой
# таблицей расхождение фиксируется в момент, когда случилось, и накапливается
# между прогонами.
#
# ``run_id ... ON DELETE SET NULL``, а НЕ ``CASCADE``: retention (#642) вытесняет
# старые прогоны, и каскад унёс бы вместе с ними вердикт платформы — самую
# долгоживущую и невосстановимую часть записи (повторно её взять неоткуда, а наш
# прогон воспроизводится запуском). Связь теряется, факт остаётся.
_SCHEMA_V4_SUBMISSIONS = """
CREATE TABLE IF NOT EXISTS stepik_submissions (
    id            INTEGER PRIMARY KEY,
    ts_utc        TEXT    NOT NULL,
    step_id       INTEGER NOT NULL,
    task_key      TEXT    NOT NULL DEFAULT '',
    submission_id INTEGER,
    verdict       TEXT    NOT NULL,
    hint          TEXT,
    run_id        INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    our_verdict   TEXT,
    diverged      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_submissions_step     ON stepik_submissions(step_id, id);
CREATE INDEX IF NOT EXISTS idx_submissions_diverged ON stepik_submissions(diverged, id);
"""


# DDL схемы v4, часть 2 (issue #1220): обращения к глоссарию ИЗ ОШИБКИ.
# Отдельная таблица, а не колонка в ``runs``: одна ошибка может увести в
# несколько карточек, и связь тут «многие к одному», а не «один к одному».
# Ссылки на ``runs`` нет намеренно — переход случается уже после прогона, при
# разборе, и переживать retention он должен так же, как вердикт платформы.
_SCHEMA_V4_GLOSSARY = """
CREATE TABLE IF NOT EXISTS glossary_hits (
    id           INTEGER PRIMARY KEY,
    ts_utc       TEXT NOT NULL,
    card_id      TEXT NOT NULL,
    failure_kind TEXT,
    error_class  TEXT
);
CREATE INDEX IF NOT EXISTS idx_glossary_hits_card ON glossary_hits(card_id);
"""


# DDL схемы v4, часть 3 (issue #1220): чем исполнялся прогон. Прогон под
# песочницей медленнее, и без отметки сравнение времён между прогонами вводит в
# заблуждение — в «Прогрессе» и при сверке вердиктов особенно.
_SCHEMA_V4_ISOLATION = """
ALTER TABLE runs ADD COLUMN isolation TEXT;
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
    conn: sqlite3.Connection,
    *,
    task_key: str,
    ts_utc: str,
    full_ac: bool,
    display_name: str | None = None,
) -> None:
    """Учесть один прогон режима 1/2 в агрегате задачи (issue #819).

    ``first_ts_utc`` пишется один раз (первая попытка), ``runs_total`` растёт,
    а ``first_ac_ts_utc``/``attempts_to_first_ac`` фиксируются на первом полном
    AC и больше не меняются — «первый зачёт» по определению случается однажды.

    ``display_name`` (issue #990), наоборот, обновляется каждым прогоном:
    ключом стал идентификатор шага, а человеку в «Прогрессе» нужно имя папки, и
    последнее известное имя точнее первого — папку могли переименовать.
    ``COALESCE`` в UPDATE защищает уже сохранённое имя от затирания пустым:
    прогон из папки без имени (корень диска) не должен обезличивать задачу.
    """
    conn.execute(
        "INSERT INTO task_progress "
        "    (task_key, first_ts_utc, runs_total, first_ac_ts_utc, attempts_to_first_ac, "
        "     display_name) "
        "VALUES (?, ?, 1, ?, ?, ?) "
        "ON CONFLICT(task_key) DO UPDATE SET "
        "    runs_total = runs_total + 1, "
        "    first_ac_ts_utc = COALESCE(first_ac_ts_utc, excluded.first_ac_ts_utc), "
        "    attempts_to_first_ac = COALESCE("
        "        attempts_to_first_ac, "
        "        CASE WHEN excluded.first_ac_ts_utc IS NOT NULL THEN runs_total + 1 END), "
        "    display_name = COALESCE(excluded.display_name, display_name)",
        (
            task_key,
            ts_utc,
            ts_utc if full_ac else None,
            1 if full_ac else None,
            display_name or None,
        ),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция ``user_version`` 0→``SCHEMA_VERSION`` (#135).

    Делегирует общему ``db.apply_schema`` (issue #552) схему v1, затем
    инкрементально доводит до v2 (агрегат ``task_progress``, issue #819) с
    разовым заполнением по имеющимся прогонам, v3 (``display_name``, #990) и
    v4 (вердикт платформы, признак изоляции, переходы в глоссарий — #1175 и
    #1220). DDL идемпотентен (``CREATE ... IF NOT EXISTS``), поэтому
    параллельная инициализация двумя процессами безопасна (#393).

    Сверка с ИТОГОВОЙ ``SCHEMA_VERSION`` идёт здесь, а не в ``db.apply_schema``:
    примитив знает только про свою ступень, и на базе v2 вызов со ступенью v1
    выглядел бы как откат (issue #794).

    Вся миграция идёт под ОДНИМ write-lock'ом (``db.exclusive_transaction``,
    issue #947), и версия перечитывается уже под ним. Раньше версия читалась
    вне транзакции, а мигрирует и путь чтения (раздел «Прогресс» открывает базу
    так же, как запись прогона) — поэтому два процесса входили в миграцию 1→2
    одновременно, оба видели ``user_version = 1`` и оба гнали
    ``_backfill_task_progress``. Бэкфилл не идемпотентен: он зовёт
    ``_bump_task_progress``, а тот делает ``runs_total = runs_total + 1``, —
    и «попытки» в «Прогрессе» удваивались молча, без ошибки в логе. Лок делает
    вход в миграцию взаимоисключающим, а транзакция — атомарной: обрыв между
    DDL и ``set_user_version`` откатывается целиком, а не оставляет базу
    полумигрированной.

    Raises:
        db.SchemaTooNewError: база записана более новой версией схемы.
    """
    current = db.user_version(conn)
    if current > SCHEMA_VERSION:
        raise db.SchemaTooNewError(found=current, expected=SCHEMA_VERSION)
    if current == SCHEMA_VERSION:
        return  # быстрый путь: почти каждое открытие базы уже на актуальной схеме
    with db.exclusive_transaction(conn):
        current = db.user_version(conn)  # под локом: сосед мог мигрировать, пока мы ждали
        if current > SCHEMA_VERSION:
            raise db.SchemaTooNewError(found=current, expected=SCHEMA_VERSION)
        if current == SCHEMA_VERSION:
            return
        # issue #990: ступень 0→1 применяется ТОЛЬКО к базе, которая её не проходила.
        # Примитив `apply_schema` знает лишь про свою ступень и считает любую версию
        # выше запрошенной откатом: на базе v2 (а это все базы, созданные до этого
        # изменения) безусловный вызов со ступенью v1 бросал SchemaTooNewError, и
        # история отваливалась с «схема новее ожидаемой» сразу после обновления
        # грейдера. Поймано тестом миграции v2→v3.
        if current < _SCHEMA_V1_VERSION:
            db.apply_schema(conn, version=_SCHEMA_V1_VERSION, ddl=_SCHEMA_V1)
        # DDL — через `db.execute_ddl_script`, а не `conn.executescript`: последний
        # делает неявный COMMIT и снял бы взятый выше write-lock (issue #947).
        db.execute_ddl_script(conn, _SCHEMA_V2)
        # issue #990: ступень 2→3 идёт ДО заполнения агрегата — backfill пишет через
        # `_bump_task_progress`, а тот уже знает про `display_name`. Обратный порядок
        # ронял домиграцию старой базы на «no such column» и, из-за best-effort,
        # выглядел как молча пропавшая запись (поймано тестом миграции v1→v2).
        #
        # `ALTER TABLE ... ADD COLUMN` не идемпотентен (в отличие от
        # `CREATE ... IF NOT EXISTS`), поэтому ступень выполняется по факту наличия
        # колонки: параллельная инициализация двумя процессами иначе роняла бы
        # второго на «duplicate column name».
        if not _has_column(conn, "task_progress", "display_name"):
            db.execute_ddl_script(conn, _SCHEMA_V3)
        # Ступень 3→4 (issue #1175/#1220). Обе таблицы — идемпотентный
        # ``CREATE ... IF NOT EXISTS``; колонка ``runs.isolation`` — по факту
        # наличия, как ``display_name`` выше: ``ADD COLUMN`` не идемпотентен, и
        # параллельная домиграция двумя процессами роняла бы второго на
        # «duplicate column name».
        db.execute_ddl_script(conn, _SCHEMA_V4_SUBMISSIONS)
        db.execute_ddl_script(conn, _SCHEMA_V4_GLOSSARY)
        if not _has_column(conn, "runs", "isolation"):
            db.execute_ddl_script(conn, _SCHEMA_V4_ISOLATION)
        if current < _SCHEMA_V2_VERSION:
            _backfill_task_progress(conn)
        db.set_user_version(conn, SCHEMA_VERSION)


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

    def last_run_verdict(self, task_key: str) -> tuple[int | None, str | None]:
        """Наш последний вердикт по задаче: ``(run_id, verdict)`` (issue #1175)."""
        ...

    def add_stepik_submission(self, record: StepikSubmissionRecord) -> int | None:
        """Записать вердикт платформы по отправке решения (issue #1175)."""
        ...

    def stepik_submissions(
        self, *, step_id: int | None = None, diverged_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Отправки на Stepik (новые первыми), опционально только расхождения."""
        ...

    def add_glossary_hit(self, record: GlossaryHitRecord) -> int | None:
        """Записать переход в карточку глоссария из ошибки (issue #1220)."""
        ...

    def glossary_hits(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Переходы в глоссарий из ошибок (новые первыми, issue #1220)."""
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

        Best-effort: любая ``sqlite3.Error``/``OSError`` проглатывается (возврат
        ``None``), как у ``stats.record_run``/``GraderCache`` — история не должна
        ронять грейдинг. Неустранимая причина (повреждённый файл, схема от более
        новой версии) при этом называется вслух один раз за процесс, issue #794.
        """
        for attempt in range(_WRITE_ATTEMPTS):
            try:
                ts_utc = _utc_now_iso()
                with _WRITE_LOCK, contextlib.closing(_connect(self.db_path)) as conn:
                    cur = conn.execute(
                        "INSERT INTO runs (ts_utc, mode, source, task_key, solution_name, "
                        "solution_hash, duration_s, isolation) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            ts_utc,
                            record.mode,
                            record.source,
                            record.task_key,
                            record.solution_name,
                            record.solution_hash,
                            record.duration_s,
                            record.isolation,
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
                            display_name=record.task_title,
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
            except sqlite3.OperationalError as exc:
                # Транзиентная блокировка БД (SQLITE_BUSY/"database is locked") —
                # межпроцессная гонка мимо _WRITE_LOCK; короткий backoff и повтор,
                # best-effort сдача только после исчерпания попыток (issue #605).
                # Повреждение файла приезжает тем же классом, но повтором не
                # лечится — сдаёмся сразу и говорим об этом вслух (issue #794).
                if _db_problem_kind(exc) is not None:
                    _note_unusable_db(self.db_path, exc)
                    return None
                if attempt == _WRITE_ATTEMPTS - 1:
                    return None
                time.sleep(_WRITE_RETRY_DELAY_S * (attempt + 1))
            except (sqlite3.Error, OSError) as exc:
                _note_unusable_db(self.db_path, exc)
                return None
        return None

    def recent_runs(self, *, task_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Последние прогоны (новые первыми) с вложенными ``cases``.

        Отсутствующая/битая БД → пустой список (graceful, не ошибка); файл не
        создаётся, если его нет (#134). Фильтр по ``task_key`` опционален.
        Битая — с однократным предупреждением, чтобы не выглядеть как «истории
        пока нет» (issue #794).
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
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
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
                    "attempts_to_first_ac, display_name FROM task_progress "
                    "ORDER BY task_key LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return []

    def last_run_verdict(self, task_key: str) -> tuple[int | None, str | None]:
        """Наш последний вердикт по задаче: ``(run_id, verdict)`` (issue #1175).

        Вердикт прогона — ``"AC"``, если все кейсы зачтены, иначе код первого
        упавшего (``WA``/``RE``/``TL``…): именно он объясняет, ПОЧЕМУ мы не
        согласны с платформой. Прогон без кейсов вердикта не даёт.

        Берутся только прогоны проверки (``CORRECTNESS_MODES``): бенчмарк
        отвечает на вопрос «быстрее ли», и сравнивать его с «зачтено» нельзя.
        Нет базы, нет прогонов — ``(None, None)``, и запись об отправке уйдёт
        без нашей стороны сравнения.
        """
        if not self.db_path.is_file():
            return None, None
        placeholders = ",".join("?" * len(CORRECTNESS_MODES))
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    f"SELECT id FROM runs WHERE task_key = ? AND mode IN ({placeholders}) "
                    "ORDER BY id DESC LIMIT 1",
                    (task_key, *sorted(CORRECTNESS_MODES)),
                ).fetchone()
                if row is None:
                    return None, None
                run_id = int(row["id"])
                cases = conn.execute(
                    "SELECT verdict FROM case_results WHERE run_id = ? ORDER BY case_no",
                    (run_id,),
                ).fetchall()
                if not cases:
                    return run_id, None
                failed = next((c["verdict"] for c in cases if c["verdict"] != "AC"), None)
                return run_id, failed or "AC"
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return None, None

    def add_stepik_submission(self, record: StepikSubmissionRecord) -> int | None:
        """Записать вердикт платформы по отправке; вернуть id записи (issue #1175).

        Флаг расхождения считается ЗДЕСЬ, при записи, а не при чтении: наш
        вердикт — величина изменчивая (следующий прогон перепишет ``runs``,
        retention вытеснит старые записи), а вопрос «разошлись ли вердикты»
        задаётся про момент отправки. Посчитанный при чтении, он отвечал бы про
        сегодняшнее состояние базы, а не про то, что случилось.

        Best-effort, как ``add_run``: проблемы БД не должны ронять отправку —
        решение на платформу уже ушло, и падать после этого не на чем.
        """
        our = record.our_verdict
        diverged = 0
        if our is not None and record.verdict in PLATFORM_VERDICTS:
            diverged = int((record.verdict == "correct") != (our == "AC"))
        try:
            with _WRITE_LOCK, contextlib.closing(_connect(self.db_path)) as conn:
                cur = conn.execute(
                    "INSERT INTO stepik_submissions (ts_utc, step_id, task_key, submission_id, "
                    "verdict, hint, run_id, our_verdict, diverged) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _utc_now_iso(),
                        record.step_id,
                        record.task_key,
                        record.submission_id,
                        record.verdict,
                        record.hint,
                        record.run_id,
                        our,
                        diverged,
                    ),
                )
                conn.commit()
                return cur.lastrowid
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return None

    def stepik_submissions(
        self, *, step_id: int | None = None, diverged_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Отправки на Stepik (новые первыми); отсутствующая/битая БД → пусто."""
        if not self.db_path.is_file():
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if step_id is not None:
            clauses.append("step_id = ?")
            params.append(step_id)
        if diverged_only:
            clauses.append("diverged = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT * FROM stepik_submissions {where} ORDER BY id DESC LIMIT ?",
                    (*params, limit),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return []

    def add_glossary_hit(self, record: GlossaryHitRecord) -> int | None:
        """Записать переход в карточку глоссария из ошибки (issue #1220)."""
        try:
            with _WRITE_LOCK, contextlib.closing(_connect(self.db_path)) as conn:
                cur = conn.execute(
                    "INSERT INTO glossary_hits (ts_utc, card_id, failure_kind, error_class) "
                    "VALUES (?, ?, ?, ?)",
                    (_utc_now_iso(), record.card_id, record.failure_kind, record.error_class),
                )
                conn.commit()
                return cur.lastrowid
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return None

    def glossary_hits(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Переходы в глоссарий из ошибок (новые первыми); битая БД → пусто."""
        if not self.db_path.is_file():
            return []
        try:
            with contextlib.closing(_connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM glossary_hits ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError) as exc:
            _note_unusable_db(self.db_path, exc)
            return []


def record_run(
    mode: int,
    cases: list[CaseRecord],
    *,
    db_path: Path,
    source: str = "cli",
    task_key: str = "",
    task_title: str | None = None,
    solution_name: str | None = None,
    solution_hash: str | None = None,
    duration_s: float | None = None,
    lint: list[LintRecord] | None = None,
    isolation: str | None = None,
    max_runs_per_task: int | None = _MAX_RUNS_PER_TASK,
) -> int | None:
    """Записать один прогон в локальную историю; вернуть ``run_id`` (или ``None``).

    Тонкая обёртка над ``SqliteHistoryRepository.add_run`` (ADR-0009): собирает
    параметры в доменный ``RunRecord`` и делегирует локальному бэкенду. Сигнатура
    сохранена для существующих вызывающих (``cli``/``web``) и тестов — поведение,
    retention (#642) и best-effort те же. Путь передаётся явно (opt-in, #134).

    ``isolation`` (issue #1220) — чем исполнялся прогон; значение снимает
    ``history_recording.current_isolation()``. Не передали — в базе останется
    ``NULL``, то есть «неизвестно».
    """
    return SqliteHistoryRepository(db_path).add_run(
        RunRecord(
            mode=mode,
            cases=cases,
            source=source,
            task_key=task_key,
            task_title=task_title,
            solution_name=solution_name,
            solution_hash=solution_hash,
            duration_s=duration_s,
            lint=lint or [],
            isolation=isolation,
        ),
        max_runs_per_task=max_runs_per_task,
    )


def record_stepik_submission(
    step_id: int,
    verdict: str,
    *,
    db_path: Path,
    task_key: str = "",
    submission_id: int | None = None,
    hint: str | None = None,
) -> int | None:
    """Записать вердикт платформы по отправке решения (issue #1175).

    Нашу сторону сравнения ищет сама: последний прогон проверки по
    ``task_key``. Локального прогона не было — запись создаётся с
    ``run_id = NULL`` и без ``our_verdict``, потому что отправить решение можно
    и не запуская проверку.
    """
    repo = SqliteHistoryRepository(db_path)
    run_id, our_verdict = repo.last_run_verdict(task_key) if task_key else (None, None)
    return repo.add_stepik_submission(
        StepikSubmissionRecord(
            step_id=step_id,
            verdict=verdict,
            task_key=task_key,
            submission_id=submission_id,
            hint=hint,
            run_id=run_id,
            our_verdict=our_verdict,
        )
    )


def read_stepik_submissions(
    db_path: Path,
    *,
    step_id: int | None = None,
    diverged_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Отправки на Stepik из истории, новые первыми (issue #1175)."""
    return SqliteHistoryRepository(db_path).stepik_submissions(
        step_id=step_id, diverged_only=diverged_only, limit=limit
    )


def record_glossary_hit(
    card_id: str,
    *,
    db_path: Path,
    failure_kind: str | None = None,
    error_class: str | None = None,
) -> int | None:
    """Записать переход в карточку глоссария из ошибки (issue #1220)."""
    return SqliteHistoryRepository(db_path).add_glossary_hit(
        GlossaryHitRecord(card_id=card_id, failure_kind=failure_kind, error_class=error_class)
    )


def read_glossary_hits(db_path: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Переходы в глоссарий из ошибок, новые первыми (issue #1220)."""
    return SqliteHistoryRepository(db_path).glossary_hits(limit=limit)


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


def _step_key_from_meta(task_dir: Path) -> str | None:
    """``step:<id>`` из ``meta.json`` папки задачи или ``None`` (issue #990).

    ``meta.json`` пишет downloader при скачивании шага, и ``step_id`` в нём —
    единственный идентификатор задачи, устойчивый к переименованию и переезду
    папки. Имя папки таким идентификатором не является: две задачи из разных
    курсов, названные одинаково, получали один ключ на двоих — общую историю,
    общий зачёт и общее удаление.

    JSON читается здесь, а не через ``stepik_client.read_step_id``, намеренно:
    ``history`` не должен тянуть сетевой слой ради разбора локального файла —
    это ребро в DAG ради десяти строк stdlib. Любая проблема с файлом (нет,
    битый, поле не int) означает «устойчивого ключа не будет», а не ошибку:
    папка просто скачана не downloader'ом.
    """
    try:
        raw = (task_dir / "meta.json").read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    step_id = data.get("step_id")
    return f"step:{step_id}" if isinstance(step_id, int) else None


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
    # issue #818: относительный путь задачи считаем ОТ BASE, а не от cwd.
    # Иначе относительный `--file task.py` из папки задачи давал
    # `task_dir == "."`, ветка ниже ловила ValueError (относительный против
    # абсолютного) и возвращала ту же точку — фикс #817 до неё не доходил. С
    # единой базой истории это стало заметно: все задачи схлопывались в одну
    # строку «Прогресса» (поймано прогоном двух соседних папок).
    #
    # Резолвить оба пути от cwd нельзя: на CI рабочая копия и tmp лежат на
    # РАЗНЫХ дисках Windows, и такой резолв возвращал абсолютный путь вместо
    # ожидаемого имени (поймано тремя windows-job'ами, локально на одном диске
    # всё проходило).
    base = base.resolve()
    task_dir = (base / task_dir).resolve() if not task_dir.is_absolute() else task_dir.resolve()

    # issue #990 (JRN-4A-01/JRN-4B-02): устойчивый идентификатор шага важнее
    # любого пути. Пока ключом было имя папки, `~/курс-А/задача-3` и
    # `~/курс-Б/задача-3` были для базы одной задачей: `--purge-history задача-3`
    # стирал историю обеих, а зачёт в одной красил другую зелёным «✅ Решено».
    # Проверка идёт ДО разбора пути — путь остаётся запасным вариантом для папок,
    # скачанных не downloader'ом.
    step_key = _step_key_from_meta(task_dir)
    if step_key is not None:
        return step_key

    try:
        rel = task_dir.relative_to(base, walk_up=True)
    except ValueError:
        # Разные диски на Windows — отдаём как есть, а не роняем запись
        # истории (issue #440).
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


class PurgePreview(TypedDict):
    """Объём предстоящего удаления истории (issue #990)."""

    runs: int
    tasks: list[str]
    solutions: list[str]


def preview_purge(db_path: Path, *, task_key: str | None = None) -> PurgePreview:
    """Что именно исчезнет при ``purge_history`` — до удаления (issue #990).

    Сколько прогонов уйдёт, каких задач они касаются и какие файлы решений в
    них встречались. Удаление истории необратимо, поэтому пользователь обязан
    увидеть объём заранее — тем более что ключ задачи сейчас совпадает у
    одноимённых папок разных курсов, и «своя» задача может утащить чужую.

    Best-effort, как вся работа с историей: нет базы, битый файл, нечитаемый
    путь — пустой предпросмотр, а не исключение.
    """
    empty: PurgePreview = {"runs": 0, "tasks": [], "solutions": []}
    if not db_path.is_file():
        return empty
    where = "" if task_key is None else " WHERE task_key = ?"
    params: tuple[str, ...] = () if task_key is None else (task_key,)
    try:
        with contextlib.closing(db.connect(db_path)) as conn:
            runs = int(conn.execute(f"SELECT COUNT(*) FROM runs{where}", params).fetchone()[0])
            tasks = [
                str(row[0])
                for row in conn.execute(
                    f"SELECT DISTINCT task_key FROM runs{where} ORDER BY task_key", params
                )
                if row[0]
            ]
            solutions = [
                str(row[0])
                for row in conn.execute(
                    f"SELECT DISTINCT solution_name FROM runs{where} "
                    f"ORDER BY solution_name LIMIT 20",
                    params,
                )
                if row[0]
            ]
    except (sqlite3.DatabaseError, OSError):
        # Молча, как и остальной модуль: предпросмотр — вспомогательный шаг,
        # и его отказ не должен мешать самому удалению (битую базу как раз и
        # хотят снести).
        return empty
    return {"runs": runs, "tasks": tasks, "solutions": solutions}


def purge_history(db_path: Path, *, task_key: str | None = None) -> int:
    """Удалить историю прогонов; вернуть число удалённых прогонов (issue #813).

    ``task_key=None`` — удалить БД целиком вместе с ``-wal``/``-shm``
    спутниками: у пользователя должен быть способ убрать журнал обучения, не
    зная о существовании этих файлов. С ``task_key`` удаляются прогоны только
    этой задачи (``cases``/``lint`` уходят каскадом по FK, вердикты платформы —
    явным запросом), а база остаётся.

    Обращения к глоссарию (``glossary_hits``, issue #1220) точечному удалению
    не поддаются: у них нет задачи — только ключ ошибки и карточка. Они уходят
    при удалении базы целиком, и это названо в документации истории, а не
    оставлено на догадку.

    Best-effort, как и вся работа с историей: отсутствующая БД — это 0
    удалённых, а не ошибка. Битая база — тоже: цель команды и есть «истории не
    стало», поэтому нечитаемый файл удаляется целиком вместо трейсбека
    ``sqlite3.DatabaseError`` (issue #990, находки PROD-2-02/FZZ-5-05) —
    пользователь, у которого журнал повреждён, приходит сюда как раз за этим.
    """
    if not db_path.is_file():
        return 0
    if task_key is None:
        return _drop_database(db_path)
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            cursor = conn.execute("DELETE FROM runs WHERE task_key = ?", (task_key,))
            # issue #819 + #813: агрегат «до первого зачёта» живёт отдельной строкой
            # и каскадом FK не уносится — без этого «историю удалили» оставляло бы
            # число попыток и время первого зачёта по задаче лежать в базе.
            conn.execute("DELETE FROM task_progress WHERE task_key = ?", (task_key,))
            # issue #1175: вердикты платформы FK-каскадом тоже не уносятся —
            # связь с прогоном намеренно ``ON DELETE SET NULL``, чтобы retention
            # не стирал невосстановимое. Здесь удаление запрошено человеком, и
            # «историю по этой задаче удалили» обязано означать всю историю.
            conn.execute("DELETE FROM stepik_submissions WHERE task_key = ?", (task_key,))
            conn.commit()
            # VACUUM возвращает место ОС: иначе удалённые записи остаются
            # вычитываемыми из файла сырым чтением, а обещание «удалили» — неполным.
            conn.execute("VACUUM")
            return int(cursor.rowcount or 0)
    except (sqlite3.DatabaseError, OSError):
        # Точечное удаление на битой базе невозможно: SQL не выполнить. Сносим
        # файл целиком — это шире запрошенного, но честнее молчаливого отказа,
        # и соответствует смыслу команды. Число прогонов неизвестно: база не
        # читается, поэтому возвращаем 0, а не выдуманную цифру.
        _drop_database(db_path)
        return 0


def _drop_database(db_path: Path) -> int:
    """Удалить файл БД вместе с WAL/SHM-спутниками; вернуть число прогонов в ней."""
    removed = 0
    with contextlib.suppress(sqlite3.DatabaseError, OSError):
        with contextlib.closing(db.connect(db_path)) as conn:
            removed = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)
    return removed
