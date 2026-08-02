"""insights.py — таксономия падений и затухание карточек «Подучить» (issue #347).

Архитектурный слой: Infrastructure / Utilities. «Мозг» раздела «Подучить»
(эпик #342, `docs/audit-2026-07.md` § 9.3): классифицирует каждое падение в
ключ карточки, агрегирует повторяющиеся ошибки по истории прогонов
(`core/history.py`, #344) и считает статус карточки — активна → угасает →
архив — по мере исправления.

Два инварианта дизайна (§ 11 аудита):

- **Затухание по номерам прогонов, а не по календарю** — статус есть чистая
  функция от последовательности «встречалась/нет» в последних N прогонах.
  Тестируется таблично, без ``freezegun``/часов.
- **Без хранимого состояния карточек** — всё считается из истории на лету
  (агрегация читает `history.read_recent_runs`), карточка нигде не
  материализуется. Исключение — `time_to_first_green`: «попытки до первого
  зачёта» ведутся агрегатом в БД, потому что retention истории удаляет ранние
  прогоны и считать эту метрику по остатку значит занижать её (issue #819).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from stepik_grader.core import glossary, history, normalizers

__all__ = [
    "DEFAULT_ACTIVE_T",
    "DEFAULT_CLEAN_K",
    "DEFAULT_WINDOW_N",
    "CardStatus",
    "InsightCard",
    "TaskProgress",
    "classify_status",
    "current_streak",
    "failure_kind",
    "learning_cards",
    "time_to_first_green",
    "violated_rule_codes",
]

# Пороги затухания по умолчанию (переопределяются из [tool.stepik-grader]):
DEFAULT_WINDOW_N = 10  # окно последних N прогонов
DEFAULT_ACTIVE_T = 2  # ключ активен при ≥T попаданиях в окне
DEFAULT_CLEAN_K = 3  # ≥K чистых прогонов подряд → архив

CardStatus = Literal["active", "fading", "archived", "watch"]

# Вердикты режимов 3/4, которые считаются «медленным» падением (§ 9.3).
_BENCH_SLOW = frozenset({"SLOWER", "MUCH_SLOWER"})


def failure_kind(
    verdict: str,
    *,
    error: str = "",
    output: list[str] | None = None,
    expected: list[str] | None = None,
) -> str | None:
    """Классифицировать исход кейса в ключ карточки «Подучить» (§ 9.3).

    Возвращает ``None``, если исход не является падением (AC/OK/SIMILAR и т.п.).
    Чистая функция — все данные приходят аргументами:

    - ``timeout`` — TLE;
    - ``runtime-error:<Класс>`` — RE (имя исключения из ``error`` тем же
      ``glossary.lookup_from_error``, что и CLI-подсказка; ``runtime-error``
      без класса, если распознать не удалось);
    - ``output-format`` — WA, где ``output``/``expected`` различаются только
      форматированием (пробелы/пустые строки/регистр);
    - ``wrong-answer`` — прочие WA;
    - ``slow`` — SLOWER/MUCH_SLOWER режимов 3/4.
    """
    if verdict == "TLE":
        return "timeout"
    if verdict == "RE":
        entry = glossary.lookup_from_error(error) if error else None
        return f"runtime-error:{entry.exception}" if entry else "runtime-error"
    if verdict == "WA":
        return "output-format" if _is_format_only(output, expected) else "wrong-answer"
    if verdict in _BENCH_SLOW:
        return "slow"
    return None


def _is_format_only(output: list[str] | None, expected: list[str] | None) -> bool:
    """True, если ``output`` и ``expected`` различаются только форматированием.

    Смысловой вывод совпал, «промах» — в trailing-пробелах / пустых строках /
    регистре (``normalizers.normalize_whitespace`` + сравнение без регистра).
    """
    if output is None or expected is None:
        return False

    def norm(lines: list[str]) -> str:
        return normalizers.normalize_whitespace("\n".join(lines)).casefold()

    return norm(output) == norm(expected)


def classify_status(
    history_flags: list[bool],
    *,
    n: int = DEFAULT_WINDOW_N,
    t: int = DEFAULT_ACTIVE_T,
    k: int = DEFAULT_CLEAN_K,
) -> CardStatus:
    """Статус карточки по истории её ключа (``True`` = встречался в прогоне).

    ``history_flags`` — в хронологическом порядке (последний = самый свежий
    прогон). Учитываются только последние ``n``:

    - ``archived`` — ≥``k`` чистых прогонов подряд с конца (исправлено, § 9.3);
    - ``active`` — ≥``t`` попаданий в окне и последний прогон «грязный»;
    - ``fading`` — был активен, но 1..``k``-1 последних прогонов чистые;
    - ``watch`` — появлялся, но ниже порога активности (первое появление,
      «мигающая» ошибка).

    Пустая история → ``watch`` (защитное значение; агрегатор такие ключи
    не создаёт).
    """
    window = history_flags[-n:]
    clean_streak = 0
    for seen in reversed(window):
        if seen:
            break
        clean_streak += 1
    if clean_streak >= k:
        return "archived"
    hits = sum(1 for seen in window if seen)
    if hits >= t:
        return "fading" if clean_streak >= 1 else "active"
    return "watch"


@dataclass(frozen=True)
class InsightCard:
    """Агрегированная карточка «Подучить» по одному ключу (падение или lint)."""

    key: str  # failure_kind ("runtime-error:KeyError") или rule_code ("E501")
    category: Literal["failure", "lint"]
    status: CardStatus
    hits: int  # число прогонов окна, где ключ встретился
    runs_considered: int  # размер рассмотренного окна прогонов
    glossary_id: str | None = None  # для runtime-error:<Класс> — id карточки исключения


@dataclass(frozen=True)
class TaskProgress:
    """Метрика «попыток/времени до первого полного AC» по одной задаче (issue #431).

    ``attempts`` — число прогонов до первого полного AC включительно (если задача
    ещё не решена — всего прогонов). ``seconds_to_first_ac`` — секунды от первого
    прогона задачи до первого полного AC (``None``, если не решена или метки
    времени не распарсились). Считаются ТОЛЬКО прогоны проверки (режимы 1/2).

    Единственная метрика с хранимым состоянием (таблица ``task_progress``,
    issue #819): на лету по ``runs`` её считать нельзя — retention удаляет
    ранние прогоны, и «первая попытка» уезжает в середину истории.
    """

    task_key: str
    attempts: int
    solved: bool
    total_runs: int
    seconds_to_first_ac: float | None = None


def _is_bench_run(run: dict[str, Any]) -> bool:
    """Прогон бенчмарка (режимы 3/4) — не участвует в метриках корректности.

    Вердикты режимов 3/4 (``ERR``/``SIMILAR``/``SLOWER``) отвечают на вопрос
    «быстрее ли», а не «верно ли»: ``AC`` там не бывает никогда. Прежде такой
    прогон считался провалом и обнулял серию — пользователь, сделавший ровно
    то, ради чего инструмент и нужен (проверил решение, затем сравнил
    варианты), терял видимое достижение (issue #819).

    Режим, которого в записи нет (старые/синтетические данные), считается
    прогоном проверки — прежнее поведение.
    """
    mode = run.get("mode")
    return mode is not None and mode not in history.CORRECTNESS_MODES


def _run_is_full_ac(run: dict[str, Any]) -> bool:
    """Прогон «решён» — прогон проверки, где есть кейсы и все вердикты AC."""
    if _is_bench_run(run):
        return False
    cases = run.get("cases", [])
    return bool(cases) and all(c.get("verdict") == "AC" for c in cases)


def _parse_ts(raw: Any) -> datetime | None:
    """ISO-метка времени из истории → ``datetime`` (``None`` при сбое)."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def time_to_first_green(db_path: Path, *, limit: int = 1000) -> list[TaskProgress]:
    """Сколько попыток/времени заняло дойти до первого полного AC по каждой задаче.

    issue #431: главный измеримый показатель ценности для ученика («архив
    побед», retention). Источник — агрегат ``task_progress`` (issue #819),
    который ведётся при записи прогонов режимов 1/2. Прежде метрика считалась
    по таблице ``runs`` на лету и потому зависела от того, что в ней осталось:
    retention (200 прогонов на задачу) вытеснял ранние записи, «первой
    попыткой» становилась произвольная середина истории, и у самых активных
    пользователей метрика тихо занижалась. Бенчмарк-прогоны (режимы 3/4) в
    агрегат не попадают вовсе.

    ``limit`` — потолок числа ЗАДАЧ в отчёте (агрегат хранит одну строку на
    задачу). Пустая история → ``[]`` (дружелюбный empty state — забота
    вызывающей стороны).
    """
    progress: list[TaskProgress] = []
    for row in history.read_task_progress(db_path, limit=limit):
        first_ts = _parse_ts(row.get("first_ts_utc"))
        ac_ts = _parse_ts(row.get("first_ac_ts_utc"))
        total_runs = int(row.get("runs_total") or 0)
        attempts = row.get("attempts_to_first_ac")
        seconds = (
            (ac_ts - first_ts).total_seconds()
            if first_ts is not None and ac_ts is not None
            else None
        )
        progress.append(
            TaskProgress(
                task_key=row.get("task_key") or "",
                attempts=int(attempts) if attempts is not None else total_runs,
                solved=attempts is not None,
                total_runs=total_runs,
                seconds_to_first_ac=seconds,
            )
        )
    return sorted(progress, key=lambda p: p.task_key)


def current_streak(db_path: Path, *, limit: int = 1000) -> int:
    """Текущая серия: сколько последних прогонов ПРОВЕРКИ подряд — полный AC.

    issue #540. ``read_recent_runs`` отдаёт новые первыми, поэтому считаем с
    начала списка до первого не-AC прогона. Прогоны бенчмарка (режимы 3/4)
    пропускаются, а не рвут серию (issue #819): в них не бывает вердикта AC, и
    прежде любое сравнение вариантов обнуляло KPI «Серия» и снимало бейджи.
    Пустая/отсутствующая история → 0.
    """
    streak = 0
    for run in history.read_recent_runs(db_path, limit=limit):
        if _is_bench_run(run):
            continue
        if not _run_is_full_ac(run):
            break
        streak += 1
    return streak


def violated_rule_codes(db_path: Path, *, limit: int = DEFAULT_WINDOW_N) -> set[str]:
    """Коды правил, которые пользователь лично нарушал (issue #403).

    Для подсветки персональных нарушений в разделе «Правила»: множество
    ``rule_code`` из lint последних ``limit`` прогонов истории. Пустая/
    отсутствующая история → пустое множество (не ошибка).
    """
    return {
        code
        for run in history.read_recent_runs(db_path, limit=limit)
        for code in run.get("lint", [])
        if code
    }


def learning_cards(
    db_path: Path,
    *,
    n: int = DEFAULT_WINDOW_N,
    t: int = DEFAULT_ACTIVE_T,
    k: int = DEFAULT_CLEAN_K,
    include_archived: bool = False,
) -> list[InsightCard]:
    """Собрать карточки «Подучить» из истории прогонов (issue #347).

    Читает последние ``n`` прогонов (``history.read_recent_runs``), строит для
    каждого ключа последовательность «встречался/нет» и классифицирует статус.
    По умолчанию ``archived`` исключаются («исчезает из Подучить», хотелка №5) —
    ``include_archived=True`` вернёт и их (для «архива побед»).

    Пустая история → ``[]`` (не ошибка). Ключи сортируются: сначала failure,
    потом lint, внутри — по ключу, для стабильного вывода.
    """
    runs = history.read_recent_runs(db_path, limit=n)
    if not runs:
        return []
    # read_recent_runs отдаёт новые первыми — разворачиваем в хронологию.
    chronological = list(reversed(runs))

    per_run_keys: list[set[str]] = []
    category: dict[str, Literal["failure", "lint"]] = {}
    glossary_ref: dict[str, str | None] = {}
    for run in chronological:
        keys: set[str] = set()
        for case in run.get("cases", []):
            fk = case.get("failure_kind")
            if fk:
                keys.add(fk)
                category[fk] = "failure"
                if fk.startswith("runtime-error:"):
                    glossary_ref[fk] = fk.split(":", 1)[1].casefold()
        for code in run.get("lint", []):
            if code:
                keys.add(code)
                category[code] = "lint"
        per_run_keys.append(keys)

    cards: list[InsightCard] = []
    for key in sorted(category, key=lambda kk: (category[kk] != "failure", kk)):
        flags = [key in run_keys for run_keys in per_run_keys]
        status = classify_status(flags, n=n, t=t, k=k)
        if status == "archived" and not include_archived:
            continue
        cards.append(
            InsightCard(
                key=key,
                category=category[key],
                status=status,
                hits=sum(1 for f in flags if f),
                runs_considered=len(per_run_keys),
                glossary_id=glossary_ref.get(key),
            )
        )
    return cards
