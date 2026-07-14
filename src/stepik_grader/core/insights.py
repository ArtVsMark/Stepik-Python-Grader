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
  материализуется.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from stepik_grader.core import glossary, history, normalizers

__all__ = [
    "DEFAULT_WINDOW_N",
    "DEFAULT_ACTIVE_T",
    "DEFAULT_CLEAN_K",
    "CardStatus",
    "InsightCard",
    "failure_kind",
    "classify_status",
    "learning_cards",
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
