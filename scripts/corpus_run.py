#!/usr/bin/env python3
"""scripts/corpus_run.py — прогонный корпус: сверка вердиктов грейдера с ожиданием.

Стенд отвечает на один вопрос: **выдаёт ли ядро тот вердикт, который обязано
выдать?** Для каждой задачи корпуса прогоняются эталонное решение (ожидание —
`AC`) и каждая применимая мутация из `scripts/corpus_mutations.py` (ожидание —
вердикт, записанный в каталоге). Расхождение факта с ожиданием — дефект ядра с
готовым репро: известны задача, мутация и обе стороны сравнения.

Чем это дополняет `tests/`: юнит-тесты проверяют функции ядра поодиночке и на
синтетике, а корпус гоняет **полный путь** «файл решения → subprocess → сравнение
вывода → вердикт» на задачах целиком. Ложный AC (грейдер принял неверное решение)
виден только на этом уровне.

Запуск::

    python scripts/corpus_run.py                          # весь корпус, отчёт + exit 0/1
    python scripts/corpus_run.py --task fizzbuzz          # одна задача
    python scripts/corpus_run.py --mutation trailing_space --mutation upper_case
    python scripts/corpus_run.py --output json            # машиночитаемый отчёт
    python scripts/corpus_run.py --report                 # отчёт без падения (exit 0)

Корпус расширяется: см. `corpus/README.md` — там же правило о том, какие
задачи в него класть можно, а какие нельзя.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from corpus_mutations import MUTATIONS, Mutation, mutation_by_key

from stepik_grader.core.grader_core import run_tests
from stepik_grader.core.result import CaseResult, Verdict
from stepik_grader.core.storage import load_json_file
from stepik_grader.core.test_loader import load_test_cases
from stepik_grader.stdio_encoding import force_utf8_stdio

__all__ = [
    "BASELINE",
    "DEFAULT_CORPUS_DIR",
    "DEFAULT_TIMEOUT",
    "CheckOutcome",
    "CorpusTask",
    "EmptyTestSuite",
    "check_task",
    "discover_tasks",
    "expected_lines_of",
    "main",
    "run_verdict",
    "tasks_without_cases",
]

# Корень корпуса относительно корня репозитория (scripts/ → ..).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR: pathlib.Path = _REPO_ROOT / "corpus" / "tasks"

# Таймаут кейса в стенде — короче рабочих 10 с из GraderConfig: мутация
# ``timeout`` намеренно виснет на каждом кейсе, и дефолт растянул бы прогон
# корпуса в пять раз без единого дополнительного сигнала.
DEFAULT_TIMEOUT: float = 2.0

# Псевдоключ «мутации» для непорченого эталона — чтобы отчёт и JSON имели
# однородную форму: у каждой строки есть задача, ключ проверки и вердикт.
BASELINE: str = "baseline"


class EmptyTestSuite(RuntimeError):
    """У задачи не загрузилось ни одного кейса — сверять нечего (issue #921).

    Находка `QA-3-03`: пустой список кейсов проходил цикл «есть ли непрошедший»
    ни разу и давал ``AC`` с пояснением «все кейсы пройдены (0)». Вакуумный
    ``AC`` неотличим от настоящего, а стенд ради вердиктов и заведён — поэтому
    отсутствие кейсов теперь ошибка, а не самый зелёный из возможных исходов.
    """


@dataclass(frozen=True)
class CorpusTask:
    """Одна задача корпуса: эталонное решение, тест-кейсы и метаданные.

    Attributes:
        slug: имя директории задачи, оно же идентификатор в отчётах.
        solution_path: путь к эталонному решению (``task_1.py``).
        test_dir: директория тест-кейсов задачи.
        meta: содержимое ``meta.json`` (тема, условие, теги глоссария).
    """

    slug: str
    solution_path: pathlib.Path
    test_dir: pathlib.Path
    meta: dict[str, Any]

    @property
    def title(self) -> str:
        """Человекочитаемое название задачи (или slug, если его нет в meta)."""
        title = self.meta.get("title")
        return title if isinstance(title, str) and title else self.slug


@dataclass(frozen=True)
class CheckOutcome:
    """Результат одной проверки: ожидаемый вердикт против фактического.

    Attributes:
        task: slug задачи.
        check: ключ мутации либо :data:`BASELINE` для эталона.
        expected: вердикт, который обязано выдать ядро.
        actual: вердикт, который ядро выдало.
        detail: пояснение для отчёта — что именно проверялось.
    """

    task: str
    check: str
    expected: Verdict
    actual: Verdict
    detail: str
    alternatives: tuple[Verdict, ...] = ()
    may_be_neutral: bool = False

    @property
    def accepted(self) -> tuple[Verdict, ...]:
        """Все вердикты, которые считаются совпадением с ожиданием."""
        return (self.expected, *self.alternatives)

    @property
    def matched(self) -> bool:
        """Попал ли фактический вердикт в число приемлемых."""
        return self.actual in self.accepted

    @property
    def neutral(self) -> bool:
        """Мутация не проявилась на данных этой задачи (issue #1057).

        Только для алгоритмических мутаций: ошибка в решении может не задеть ни
        одного тест-кейса — тогда `AC` означает «портить было нечего», а не
        «ядро проглядело поломку». Такой исход не совпадение и не расхождение,
        поэтому считается отдельно и прогон им не роняется.
        """
        return self.may_be_neutral and not self.matched and self.actual == "AC"

    @property
    def mismatched(self) -> bool:
        """Расхождение, которое требует разбора: не совпало и не нейтрально."""
        return not self.matched and not self.neutral

    def as_dict(self) -> dict[str, Any]:
        """Представление для JSON-отчёта."""
        return {
            "task": self.task,
            "check": self.check,
            "expected": self.expected,
            "accepted": list(self.accepted),
            "actual": self.actual,
            "matched": self.matched,
            "neutral": self.neutral,
            "detail": self.detail,
        }


def discover_tasks(corpus_dir: pathlib.Path) -> list[CorpusTask]:
    """Собрать задачи корпуса из директории (отсортировано по slug).

    Задачей считается поддиректория с эталоном ``task_1.py`` и папкой
    ``tests/``. Прочие поддиректории молча пропускаются — так в корпусе рядом
    уживаются служебные папки (``reports/``, локальные выгрузки).

    Args:
        corpus_dir: корень корпуса (обычно ``corpus/tasks``).

    Returns:
        Список задач; пустой, если корень не существует.
    """
    if not corpus_dir.is_dir():
        return []

    tasks: list[CorpusTask] = []
    for task_dir in sorted(corpus_dir.iterdir()):
        solution_path = task_dir / "task_1.py"
        test_dir = task_dir / "tests"
        if not solution_path.is_file() or not test_dir.is_dir():
            continue

        meta_path = task_dir / "meta.json"
        meta = load_json_file(meta_path) if meta_path.is_file() else {}
        tasks.append(
            CorpusTask(
                slug=task_dir.name,
                solution_path=solution_path,
                test_dir=test_dir,
                meta=meta,
            )
        )
    return tasks


def expected_lines_of(test_dir: pathlib.Path) -> list[str]:
    """Собрать ожидаемые строки всех кейсов задачи — вход для предикатов мутаций."""
    lines: list[str] = []
    for case in load_test_cases(test_dir):
        lines.extend(case.expected_lines)
    return lines


def tasks_without_cases(tasks: Sequence[CorpusTask]) -> list[str]:
    """Slug'и задач, у которых не загрузилось ни одного тест-кейса.

    Проверяется до прогона: запускать решение по пустому набору бессмысленно, а
    один сломанный вход не должен ронять отчёт по остальным задачам.
    """
    return [task.slug for task in tasks if not load_test_cases(task.test_dir)]


def run_verdict(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Verdict, str]:
    """Прогнать решение по всем кейсам и свести результат к одному вердикту.

    Вердикт прогона — ``AC``, если прошли все кейсы, иначе вердикт **первого**
    непрошедшего: именно его увидит студент в отчёте грейдера, и именно его
    предсказывает каталог мутаций.

    Args:
        solution_path: файл решения (эталон или мутант).
        test_dir: директория тест-кейсов задачи.
        timeout: таймаут одного кейса в секундах.

    Returns:
        Пара «вердикт прогона, краткое пояснение для отчёта».
    """
    stats = run_tests(solution_path, test_dir, timeout=timeout)
    cases: list[CaseResult] = stats["cases"]
    if not cases:
        raise EmptyTestSuite(
            f"ни одного тест-кейса не загрузилось: {test_dir}. «Все кейсы "
            "пройдены (0)» — это не AC, а отсутствие проверки"
        )

    for number, case in enumerate(cases, start=1):
        if not case["passed"]:
            verdict: Verdict = case["verdict"]
            return verdict, f"кейс {number} из {len(cases)}"

    return "AC", f"все кейсы пройдены ({len(cases)})"


def check_task(
    task: CorpusTask,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    mutations: Sequence[Mutation] | None = None,
) -> list[CheckOutcome]:
    """Проверить одну задачу: эталон и все применимые к ней мутации.

    Мутант пишется во временную директорию, а тест-кейсы берутся из исходной
    задачи — корпус на диске остаётся нетронутым даже при прерывании прогона.

    Args:
        task: задача корпуса.
        timeout: таймаут одного кейса в секундах.
        mutations: чем портить решение; ``None`` — весь каталог.

    Returns:
        Список исходов: сначала эталон, затем мутации в порядке каталога.
    """
    catalog = list(MUTATIONS if mutations is None else mutations)
    expected_lines = expected_lines_of(task.test_dir)
    source = task.solution_path.read_text(encoding="utf-8")

    verdict, detail = run_verdict(task.solution_path, task.test_dir, timeout=timeout)
    outcomes = [
        CheckOutcome(
            task=task.slug,
            check=BASELINE,
            expected="AC",
            actual=verdict,
            detail=f"эталонное решение: {detail}",
        )
    ]

    with tempfile.TemporaryDirectory(prefix="corpus-mutant-") as tmp_name:
        mutant_path = pathlib.Path(tmp_name) / "task_1.py"
        for mutation in catalog:
            if not mutation.applies_to(expected_lines, source):
                continue
            mutant_path.write_text(mutation.apply(source), encoding="utf-8")
            verdict, detail = run_verdict(mutant_path, task.test_dir, timeout=timeout)
            outcomes.append(
                CheckOutcome(
                    task=task.slug,
                    check=mutation.key,
                    expected=mutation.expected,
                    actual=verdict,
                    detail=f"{mutation.title}: {detail}",
                    alternatives=mutation.alternatives,
                    may_be_neutral=mutation.may_be_neutral,
                )
            )

    return outcomes


def _select_mutations(keys: Sequence[str]) -> list[Mutation]:
    """Отобрать мутации по ключам; неизвестный ключ — ошибка аргументов."""
    if not keys:
        return list(MUTATIONS)

    selected: list[Mutation] = []
    for key in keys:
        mutation = mutation_by_key(key)
        if mutation is None:
            known = ", ".join(m.key for m in MUTATIONS)
            raise SystemExit(f"Неизвестная мутация: {key}. Известные: {known}")
        selected.append(mutation)
    return selected


def _select_tasks(tasks: Sequence[CorpusTask], slugs: Sequence[str]) -> list[CorpusTask]:
    """Отобрать задачи по slug'ам; неизвестный slug — ошибка аргументов."""
    if not slugs:
        return list(tasks)

    by_slug = {task.slug: task for task in tasks}
    selected: list[CorpusTask] = []
    for slug in slugs:
        task = by_slug.get(slug)
        if task is None:
            known = ", ".join(sorted(by_slug)) or "(корпус пуст)"
            raise SystemExit(f"Неизвестная задача: {slug}. Известные: {known}")
        selected.append(task)
    return selected


def _print_text_report(tasks: Sequence[CorpusTask], outcomes: Sequence[CheckOutcome]) -> None:
    """Напечатать человекочитаемый отчёт, сгруппированный по задачам."""
    titles = {task.slug: task.title for task in tasks}
    for task in tasks:
        rows = [o for o in outcomes if o.task == task.slug]
        if not rows:
            continue
        mismatched = sum(1 for o in rows if o.mismatched)
        head = "✗" if mismatched else "✓"
        print(f"\n{head} {task.slug} — {titles[task.slug]} ({len(rows)} проверок)")
        for outcome in rows:
            if outcome.matched:
                print(f"    ✓ {outcome.check:<18} {outcome.actual:<3} — {outcome.detail}")
            elif outcome.neutral:
                print(
                    f"    ~ {outcome.check:<18} не проявилась на данных этой задачи "
                    f"— {outcome.detail}"
                )
            else:
                print(
                    f"    ✗ {outcome.check:<18} ожидался {'/'.join(outcome.accepted)}, "
                    f"получен {outcome.actual} — {outcome.detail}"
                )


def _print_summary(outcomes: Sequence[CheckOutcome]) -> None:
    """Напечатать итоговую сводку, расхождения и нейтральные мутации."""
    mismatches = [o for o in outcomes if o.mismatched]
    neutral = [o for o in outcomes if o.neutral]
    tail = f", нейтральных мутаций: {len(neutral)}" if neutral else ""
    print(f"\nПроверок: {len(outcomes)}, расхождений: {len(mismatches)}{tail}")

    if neutral:
        # Не дефект, но и не проверка: мутация не задела ни одного кейса, то есть
        # эта задача её просто не проверяет. Молчать нельзя — иначе «✓ по всему
        # корпусу» читается как «всё покрыто».
        print("\nНе проявились на данных задачи (покрытия здесь нет):")
        for outcome in neutral:
            print(f"  ~ {outcome.task}/{outcome.check}: {outcome.detail}")

    if not mismatches:
        print("\nOK: вердикты ядра совпали с ожиданием на всём корпусе.")
        return

    print("\nРасхождения (каждое — дефект ядра с готовым репро):")
    for outcome in mismatches:
        print(
            f"  - {outcome.task}/{outcome.check}: ожидался {'/'.join(outcome.accepted)}, "
            f"получен {outcome.actual} ({outcome.detail})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: прогнать корпус и сообщить о расхождениях."""
    # issue #1108: вывод несёт названия задач и diff'ы чужого вывода.
    force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Прогонный корпус: сверка вердиктов грейдера с ожидаемыми."
    )
    parser.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"корень корпуса (по умолчанию {DEFAULT_CORPUS_DIR})",
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        metavar="SLUG",
        help="прогнать только эту задачу (можно повторять)",
    )
    parser.add_argument(
        "--mutation",
        action="append",
        default=[],
        metavar="KEY",
        help="прогнать только эту мутацию (можно повторять)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"таймаут одного кейса в секундах (по умолчанию {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="формат отчёта",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="только отчёт, без падения при расхождениях (exit 0)",
    )
    args = parser.parse_args(argv)

    tasks = _select_tasks(discover_tasks(args.corpus), args.task)
    if not tasks:
        print(f"Корпус пуст: {args.corpus}", file=sys.stderr)
        return 1

    broken = tasks_without_cases(tasks)
    if broken:
        print(
            "Задачи без тест-кейсов — сверять нечего: " + ", ".join(broken),
            file=sys.stderr,
        )
        print(
            "Пустой набор дал бы «AC: все кейсы пройдены (0)» — это отсутствие "
            "проверки, а не зелёный вердикт.",
            file=sys.stderr,
        )
        return 1

    mutations = _select_mutations(args.mutation)

    outcomes: list[CheckOutcome] = []
    for task in tasks:
        outcomes.extend(check_task(task, timeout=args.timeout, mutations=mutations))

    mismatches = [o for o in outcomes if o.mismatched]

    if args.output == "json":
        print(
            json.dumps(
                {
                    "checks": [o.as_dict() for o in outcomes],
                    "total": len(outcomes),
                    "mismatched": len(mismatches),
                    "neutral": sum(1 for o in outcomes if o.neutral),
                },
                ensure_ascii=False,
            )
        )
    else:
        _print_text_report(tasks, outcomes)
        _print_summary(outcomes)

    if mismatches and not args.report:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
