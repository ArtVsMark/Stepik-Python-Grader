#!/usr/bin/env python3
"""scripts/corpus_sweep.py — сквозной прогон подсистем грейдера по локальной базе задач.

Прогонный корпус (`corpus_run.py`) проверяет одну подсистему — вердикт. Этот
стенд шире: берёт **скачанную базу задач целиком** и на каждой задаче дёргает
все пользовательские подсистемы, собирая, что где сломалось. Смысл — поймать
дефекты, которые видны только на реальных данных: карточка «Подучить», не
собравшаяся из настоящей истории; RE без карточки в глоссарии; падение
микробенчмарка на задаче с вводом; экспорт прогресса, споткнувшийся о юникод.

Четыре группы проверок (`--modules`, по умолчанию все):

- ``core`` — вердикт эталона (обязан быть ``AC``) и каталог мутаций из
  `corpus_mutations.py` (обязан дать записанный вердикт). Ловит ложный AC и
  ложный WA.
- ``learning`` — «Подучить»: прогоны пишутся в историю, из неё собираются
  карточки (`core/insights`), считается экспорт прогресса
  (`core/progress_export`).
- ``glossary`` — на каждом RE ищется карточка ошибки (`core/error_glossary`),
  на каждом решении — правила PEP-8 (`core/lint`, если доступен ruff) и
  пробелы глоссария (`glossary/detector`).
- ``extras`` — микробенчмарк, пошаговый трейс, кэш результатов, статистика.

**Побочные эффекты изолированы.** История, статистика и кэш пишутся в рабочую
директорию прогона (`--work-dir`, по умолчанию — временная, удаляется после),
а НЕ в пользовательские `.grader_history.db` / `.grader_stats.jsonl` рядом с
задачами: стенд не имеет права портить реальный прогресс. База задач читается
только на чтение — мутанты живут во временных файлах.

**Сеть не используется.** Скачивание задач — отдельный шаг, выполняется до
прогона (см. `docs/agent/local-sweep.md`); здесь только локальные файлы.

Запуск::

    python scripts/corpus_sweep.py --base StepikTasks --inventory
    python scripts/corpus_sweep.py --base StepikTasks
    python scripts/corpus_sweep.py --base StepikTasks --modules core,learning
    python scripts/corpus_sweep.py --base StepikTasks --limit 20 --output json

Полная инструкция локального прогона (авторизация, скачивание базы, разбор
находок) — [`docs/agent/local-sweep.md`](../docs/agent/local-sweep.md).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from corpus_mutations import MUTATIONS, Mutation
from corpus_run import DEFAULT_TIMEOUT, expected_lines_of, run_verdict

from stepik_grader.core import error_glossary, history, history_recording, insights, stats
from stepik_grader.core.cache import GraderCache, hash_solution, hash_tests
from stepik_grader.core.grader_core import run_tests
from stepik_grader.core.microbench_runner import run_microbench
from stepik_grader.core.progress_export import build_progress_report, render_markdown
from stepik_grader.core.runprofile import current_profile
from stepik_grader.core.storage import load_json_file
from stepik_grader.core.test_loader import load_test_cases
from stepik_grader.core.tracer import trace_code
from stepik_grader.glossary.detector import scan_code_concepts

__all__ = [
    "MODULE_KEYS",
    "Finding",
    "SweepTask",
    "discover_base",
    "main",
    "pick_reference",
    "sweep_core",
    "sweep_extras",
    "sweep_glossary",
    "sweep_learning",
]

# Порядок групп — от самой ценной к самой дешёвой: если прогон прервут,
# успеет отработать то, ради чего он затевался.
MODULE_KEYS: tuple[str, ...] = ("core", "learning", "glossary", "extras")

# Имена файлов-решений, которые кладёт downloader: `solution.py` — код,
# отправленный на Stepik самим пользователем (downloader сохраняет его при
# скачивании), `task*.py` — рабочие файлы. Приоритет у `solution.py`: это
# принятое платформой решение, лучший кандидат в эталоны.
_REFERENCE_PRIORITY = ("solution.py",)


@dataclass(frozen=True)
class SweepTask:
    """Одна задача локальной базы в раскладке downloader'а.

    Attributes:
        slug: путь задачи относительно корня базы — идентификатор в отчётах.
        task_dir: директория задачи.
        test_dir: директория тест-кейсов (``tests/``).
        solutions: кандидаты в эталоны, в порядке предпочтения.
        meta: содержимое ``meta.json`` (пустой dict, если файла нет).
    """

    slug: str
    task_dir: pathlib.Path
    test_dir: pathlib.Path | None
    solutions: tuple[pathlib.Path, ...]
    meta: dict[str, Any]

    @property
    def runnable(self) -> bool:
        """Готова ли задача к прогону: есть и тесты, и хотя бы одно решение."""
        return self.test_dir is not None and bool(self.solutions)


@dataclass(frozen=True)
class Finding:
    """Находка прогона: что и на какой задаче не сработало.

    Attributes:
        task: slug задачи.
        module: группа проверок (``core``/``learning``/``glossary``/``extras``).
        kind: вид находки — ``mismatch`` (вердикт не совпал с ожидаемым),
            ``error`` (подсистема упала с исключением), ``missing`` (данных для
            проверки не хватило: нет эталона, нет тестов, нет карточки).
        detail: подробность для отчёта.
    """

    task: str
    module: str
    kind: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        """Представление для JSON-отчёта."""
        return {"task": self.task, "module": self.module, "kind": self.kind, "detail": self.detail}


@dataclass
class SweepReport:
    """Накопитель результатов прогона.

    Attributes:
        findings: все находки в порядке обнаружения.
        checked: сколько проверок выполнено по каждой группе.
    """

    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=lambda: dict.fromkeys(MODULE_KEYS, 0))

    def record(self, module: str, *findings: Finding) -> None:
        """Учесть выполненную проверку и приложить найденные проблемы."""
        self.checked[module] = self.checked.get(module, 0) + 1
        self.findings.extend(findings)

    def by_module(self, module: str) -> list[Finding]:
        """Находки одной группы."""
        return [f for f in self.findings if f.module == module]


def discover_base(base_dir: pathlib.Path) -> list[SweepTask]:
    """Обойти базу задач и собрать инвентарь (отсортировано по пути).

    Задачей считается любая директория, где есть ``tests/`` **или** файл-решение:
    неполные задачи тоже попадают в инвентарь — именно их полезно увидеть
    отчётом, а не молча пропустить.

    Args:
        base_dir: корень базы (например, ``StepikTasks``).

    Returns:
        Список задач; пустой, если корня нет.
    """
    if not base_dir.is_dir():
        return []

    tasks: list[SweepTask] = []
    for task_dir in sorted(p for p in base_dir.rglob("*") if p.is_dir()):
        if task_dir.name == "tests":
            continue

        tests_dir = task_dir / "tests"
        test_dir = tests_dir if tests_dir.is_dir() else None
        solutions = _collect_solutions(task_dir)
        if test_dir is None and not solutions:
            continue

        meta_path = task_dir / "meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = load_json_file(meta_path)
            except (OSError, ValueError):
                meta = {}

        tasks.append(
            SweepTask(
                slug=str(task_dir.relative_to(base_dir)),
                task_dir=task_dir,
                test_dir=test_dir,
                solutions=solutions,
                meta=meta,
            )
        )
    return tasks


def _collect_solutions(task_dir: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """Собрать кандидатов в эталоны: сперва ``solution.py``, затем ``task*.py``."""
    preferred = [task_dir / name for name in _REFERENCE_PRIORITY if (task_dir / name).is_file()]
    others = sorted(
        path
        for path in task_dir.glob("task*.py")
        if path.is_file() and path.name not in _REFERENCE_PRIORITY
    )
    return tuple(preferred + others)


def pick_reference(
    task: SweepTask, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[pathlib.Path | None, str]:
    """Выбрать эталон: первое решение задачи, проходящее её тесты.

    Мутации имеют смысл только поверх заведомо верного решения, поэтому кандидат
    обязан дать ``AC``. Ни один не дал — эталона нет, и это отдельная находка,
    а не повод считать задачу сломанной.

    Args:
        task: задача базы.
        timeout: таймаут одного кейса.

    Returns:
        Пара «путь к эталону или ``None``, пояснение для отчёта».
    """
    if task.test_dir is None:
        return None, "нет директории tests/"
    if not task.solutions:
        return None, "нет файлов-решений"

    tried: list[str] = []
    for candidate in task.solutions:
        try:
            verdict, _ = run_verdict(candidate, task.test_dir, timeout=timeout)
        except Exception as exc:
            tried.append(f"{candidate.name}: прогон упал ({type(exc).__name__})")
            continue
        if verdict == "AC":
            return candidate, candidate.name
        tried.append(f"{candidate.name}: {verdict}")

    return None, "ни одно решение не прошло тесты — " + ", ".join(tried)


def sweep_core(
    task: SweepTask,
    reference: pathlib.Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    mutations: Sequence[Mutation] | None = None,
) -> list[Finding]:
    """Группа ``core``: сверка вердиктов ядра на эталоне и его мутациях.

    Args:
        task: задача базы.
        reference: эталонное решение (уже проверено, что даёт ``AC``).
        timeout: таймаут одного кейса.
        mutations: чем портить; ``None`` — весь каталог.

    Returns:
        Находки: расхождение ожидаемого вердикта с фактическим.
    """
    assert task.test_dir is not None  # гарантировано pick_reference
    catalog = list(MUTATIONS if mutations is None else mutations)
    findings: list[Finding] = []

    try:
        expected_lines = expected_lines_of(task.test_dir)
        source = reference.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        return [Finding(task.slug, "core", "error", f"не прочитать задачу: {exc}")]

    with tempfile.TemporaryDirectory(prefix="sweep-mutant-") as tmp_name:
        mutant_path = pathlib.Path(tmp_name) / "task_1.py"
        for mutation in catalog:
            if not mutation.applies_to(expected_lines):
                continue
            try:
                mutant_path.write_text(mutation.apply(source), encoding="utf-8")
                verdict, detail = run_verdict(mutant_path, task.test_dir, timeout=timeout)
            except Exception as exc:
                findings.append(
                    Finding(
                        task.slug,
                        "core",
                        "error",
                        f"мутация {mutation.key} уронила прогон: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if verdict != mutation.expected:
                findings.append(
                    Finding(
                        task.slug,
                        "core",
                        "mismatch",
                        f"{mutation.key}: ожидался {mutation.expected}, "
                        f"получен {verdict} ({detail})",
                    )
                )
    return findings


def sweep_learning(
    task: SweepTask,
    reference: pathlib.Path,
    *,
    db_path: pathlib.Path,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Finding]:
    """Группа ``learning``: история прогонов и карточки «Подучить».

    Пишет в историю два прогона — эталон (``AC``) и заведомо неверное решение
    (``WA``): карточка «Подучить» рождается только из падений, и без второго
    прогона проверять было бы нечего.

    Args:
        task: задача базы.
        reference: эталонное решение.
        db_path: файл истории прогона стенда (не пользовательский).
        timeout: таймаут одного кейса.

    Returns:
        Находки: падения записи истории или сборки карточек.
    """
    assert task.test_dir is not None
    findings: list[Finding] = []

    for solution, label in ((reference, "эталон"), (None, "порча")):
        try:
            path = solution
            if path is None:
                broken = _write_broken(task, reference)
                if broken is None:
                    continue
                path, cleanup = broken
            else:
                cleanup = None

            result = run_tests(path, task.test_dir, timeout=timeout)
            records = history_recording.cases_from_test_results(result["cases"])
            history.record_run(
                1,
                records,
                db_path=db_path,
                source="corpus_sweep",
                task_key=task.slug,
                solution_name=path.name,
            )
        except Exception as exc:
            findings.append(
                Finding(
                    task.slug,
                    "learning",
                    "error",
                    f"запись истории ({label}) упала: {type(exc).__name__}: {exc}",
                )
            )
        finally:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)

    try:
        cards = insights.learning_cards(db_path)
    except Exception as exc:
        findings.append(
            Finding(
                task.slug, "learning", "error", f"карточки «Подучить»: {type(exc).__name__}: {exc}"
            )
        )
        return findings

    if not cards:
        findings.append(
            Finding(
                task.slug,
                "learning",
                "missing",
                "история содержит падение, но ни одной карточки «Подучить» не собралось",
            )
        )
    return findings


def _write_broken(
    task: SweepTask, reference: pathlib.Path
) -> tuple[pathlib.Path, pathlib.Path] | None:
    """Положить во временную папку заведомо неверную версию эталона.

    Returns:
        Пара «файл, временная директория для удаления» либо ``None``, если
        эталон не читается.
    """
    mutation = next((m for m in MUTATIONS if m.key == "extra_line"), None)
    if mutation is None:
        return None
    try:
        source = reference.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="sweep-broken-"))
    path = tmp_dir / "task_1.py"
    path.write_text(mutation.apply(source), encoding="utf-8")
    return path, tmp_dir


def sweep_glossary(
    task: SweepTask,
    reference: pathlib.Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    run_ruff: bool = True,
) -> list[Finding]:
    """Группа ``glossary``: карточка ошибки при RE, правила PEP-8, пробелы глоссария.

    Args:
        task: задача базы.
        reference: эталонное решение.
        timeout: таймаут одного кейса.
        run_ruff: гонять ли линтер (выключается, если ruff недоступен).

    Returns:
        Находки: RE без карточки, падения линтера или детектора.
    """
    assert task.test_dir is not None
    findings: list[Finding] = []
    source = reference.read_text(encoding="utf-8", errors="replace")

    # RE-путь: намеренно ломаем решение исключением и проверяем, что подсказка
    # по трейсбеку нашлась. Без карточки студент видит голый трейсбек.
    mutation = next((m for m in MUTATIONS if m.key == "runtime_error"), None)
    if mutation is not None:
        with tempfile.TemporaryDirectory(prefix="sweep-re-") as tmp_name:
            path = pathlib.Path(tmp_name) / "task_1.py"
            path.write_text(mutation.apply(source), encoding="utf-8")
            try:
                result = run_tests(path, task.test_dir, timeout=timeout)
                error_text = next(
                    (c.get("error") or "" for c in result["cases"] if c.get("error")), ""
                )
                if not error_text:
                    findings.append(
                        Finding(task.slug, "glossary", "missing", "RE-прогон не дал текста ошибки")
                    )
                elif error_glossary.resolve_error_hint(error_text) is None:
                    findings.append(
                        Finding(
                            task.slug,
                            "glossary",
                            "missing",
                            "для RuntimeError не нашлось карточки в глоссарии ошибок",
                        )
                    )
            except Exception as exc:
                findings.append(
                    Finding(task.slug, "glossary", "error", f"RE-путь: {type(exc).__name__}: {exc}")
                )

    if run_ruff:
        try:
            from stepik_grader.core import lint

            lint.run_lint(reference)
        except Exception as exc:
            findings.append(
                Finding(task.slug, "glossary", "error", f"линтер упал: {type(exc).__name__}: {exc}")
            )

    try:
        scan_code_concepts(source)
    except Exception as exc:
        findings.append(
            Finding(
                task.slug, "glossary", "error", f"детектор концептов: {type(exc).__name__}: {exc}"
            )
        )
    return findings


def sweep_extras(
    task: SweepTask,
    reference: pathlib.Path,
    *,
    work_dir: pathlib.Path,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Finding]:
    """Группа ``extras``: микробенчмарк, трейс, кэш, статистика.

    Args:
        task: задача базы.
        reference: эталонное решение.
        work_dir: рабочая директория стенда (кэш и статистика пишутся туда).
        timeout: таймаут одного кейса.

    Returns:
        Находки: падения любой из подсистем.
    """
    assert task.test_dir is not None
    findings: list[Finding] = []
    cases = load_test_cases(task.test_dir)
    stdin_text = "\n".join(cases[0].input_lines) if cases else ""
    source = reference.read_text(encoding="utf-8", errors="replace")

    try:
        # number=1 — стенду важно, что микробенчмарк отработал на реальной
        # задаче, а не насколько она быстрая; дефолтные 1000 повторов растянули
        # бы прогон базы на часы.
        run_microbench(source, stdin_data=stdin_text, number=1)
    except Exception as exc:
        findings.append(
            Finding(task.slug, "extras", "error", f"микробенчмарк: {type(exc).__name__}: {exc}")
        )

    try:
        trace_code(source, stdin_text, timeout=timeout)
    except Exception as exc:
        findings.append(
            Finding(task.slug, "extras", "error", f"трейсер: {type(exc).__name__}: {exc}")
        )

    try:
        cache = GraderCache(work_dir)
        solution_sha = hash_solution(reference)
        tests_sha = hash_tests(task.test_dir)
        # issue #984: отпечаток условий прогона — третий ключ кэша наравне с
        # хешами решения и тестов; стенд снимает его так же, как это делает CLI.
        env = current_profile().fingerprint
        cache.get(reference, solution_sha, tests_sha, env=env)
        cache.put(
            reference,
            solution_sha,
            tests_sha,
            {"total": len(cases), "passed": len(cases)},
            env=env,
        )
        cache.save()
    except Exception as exc:
        findings.append(Finding(task.slug, "extras", "error", f"кэш: {type(exc).__name__}: {exc}"))

    try:
        stats.record_run(
            1,
            {"AC": len(cases)},
            0.0,
            stats_path=work_dir / stats.STATS_FILE_NAME,
        )
    except Exception as exc:
        findings.append(
            Finding(task.slug, "extras", "error", f"статистика: {type(exc).__name__}: {exc}")
        )
    return findings


def _sweep_task(
    task: SweepTask,
    report: SweepReport,
    *,
    modules: Sequence[str],
    work_dir: pathlib.Path,
    db_path: pathlib.Path,
    timeout: float,
    run_ruff: bool,
) -> None:
    """Прогнать одну задачу по выбранным группам, копя результат в отчёт."""
    reference, note = pick_reference(task, timeout=timeout)
    if reference is None:
        report.record("core", Finding(task.slug, "core", "missing", f"нет эталона: {note}"))
        return

    if "core" in modules:
        report.record("core", *sweep_core(task, reference, timeout=timeout))
    if "learning" in modules:
        report.record(
            "learning", *sweep_learning(task, reference, db_path=db_path, timeout=timeout)
        )
    if "glossary" in modules:
        report.record(
            "glossary", *sweep_glossary(task, reference, timeout=timeout, run_ruff=run_ruff)
        )
    if "extras" in modules:
        report.record("extras", *sweep_extras(task, reference, work_dir=work_dir, timeout=timeout))


def _print_inventory(tasks: Sequence[SweepTask]) -> None:
    """Напечатать инвентарь базы: что найдено и что готово к прогону."""
    runnable = [t for t in tasks if t.runnable]
    no_tests = [t for t in tasks if t.test_dir is None]
    no_solution = [t for t in tasks if t.test_dir is not None and not t.solutions]

    print(f"Задач в базе: {len(tasks)}")
    print(f"  готовы к прогону (есть тесты и решение): {len(runnable)}")
    print(f"  без тестов: {len(no_tests)}")
    print(f"  без решения: {len(no_solution)}")

    for title, group in (("Без тестов", no_tests), ("Без решения", no_solution)):
        if not group:
            continue
        print(f"\n{title}:")
        for task in group[:20]:
            print(f"  - {task.slug}")
        if len(group) > 20:
            print(f"  … и ещё {len(group) - 20}")


def _print_report(report: SweepReport, tasks_run: int) -> None:
    """Напечатать итог прогона по группам."""
    print(f"\nПрогнано задач: {tasks_run}")
    for module in MODULE_KEYS:
        checked = report.checked.get(module, 0)
        if not checked:
            continue
        found = report.by_module(module)
        mark = "✗" if found else "✓"
        print(f"  {mark} {module:<9} проверок: {checked:<4} находок: {len(found)}")

    if not report.findings:
        print("\nOK: все подсистемы отработали без находок.")
        return

    print("\nНаходки:")
    for finding in report.findings:
        print(f"  [{finding.module}/{finding.kind}] {finding.task}: {finding.detail}")


def _parse_modules(raw: str) -> list[str]:
    """Разобрать ``--modules`` в список групп; неизвестная группа — ошибка."""
    if raw == "all":
        return list(MODULE_KEYS)
    modules = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [m for m in modules if m not in MODULE_KEYS]
    if unknown:
        raise SystemExit(
            f"Неизвестные группы: {', '.join(unknown)}. Известные: {', '.join(MODULE_KEYS)}"
        )
    return modules


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: прогнать базу задач по выбранным группам подсистем."""
    parser = argparse.ArgumentParser(
        description="Сквозной прогон подсистем грейдера по локальной базе задач."
    )
    parser.add_argument(
        "--base",
        type=pathlib.Path,
        required=True,
        help="корень базы скачанных задач (например, StepikTasks)",
    )
    parser.add_argument(
        "--modules",
        default="all",
        help=f"группы через запятую: {', '.join(MODULE_KEYS)} (по умолчанию все)",
    )
    parser.add_argument("--limit", type=int, default=0, help="прогнать не больше N задач (0 — все)")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="таймаут одного кейса, секунд"
    )
    parser.add_argument(
        "--work-dir",
        type=pathlib.Path,
        default=None,
        help="куда писать историю/кэш/статистику прогона (по умолчанию временная папка)",
    )
    parser.add_argument(
        "--inventory", action="store_true", help="только инвентарь базы, без прогонов"
    )
    parser.add_argument("--output", choices=("text", "json"), default="text", help="формат отчёта")
    parser.add_argument("--report", action="store_true", help="не падать при находках (exit 0)")
    args = parser.parse_args(argv)

    tasks = discover_base(args.base)
    if not tasks:
        print(f"База пуста или не найдена: {args.base}", file=sys.stderr)
        return 1

    if args.inventory:
        _print_inventory(tasks)
        return 0

    modules = _parse_modules(args.modules)
    runnable = [t for t in tasks if t.runnable]
    if args.limit > 0:
        total_runnable = sum(1 for t in tasks if t.runnable)
        runnable = runnable[: args.limit]
        # Служебные сообщения — в stderr: при ``--output json`` stdout обязан
        # содержать только отчёт, иначе его нечем разобрать из скрипта.
        print(
            f"Ограничение --limit: берём {len(runnable)} задач из {total_runnable}",
            file=sys.stderr,
        )

    run_ruff = True
    if "glossary" in modules:
        from stepik_grader.core import lint

        run_ruff = lint.ruff_available()
        if not run_ruff:
            print(
                "ruff недоступен — проверка правил PEP-8 пропущена (extra [lint])",
                file=sys.stderr,
            )

    owned_work_dir = args.work_dir is None
    work_dir = (
        pathlib.Path(tempfile.mkdtemp(prefix="corpus-sweep-")) if owned_work_dir else args.work_dir
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / history.HISTORY_DB_NAME

    report = SweepReport()
    try:
        for task in runnable:
            _sweep_task(
                task,
                report,
                modules=modules,
                work_dir=work_dir,
                db_path=db_path,
                timeout=args.timeout,
                run_ruff=run_ruff,
            )

        if "learning" in modules and runnable:
            report.findings.extend(_check_progress_export(db_path))

        if args.output == "json":
            print(
                json.dumps(
                    {
                        "tasks_total": len(tasks),
                        "tasks_run": len(runnable),
                        "checked": report.checked,
                        "findings": [f.as_dict() for f in report.findings],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            _print_report(report, len(runnable))
    finally:
        if owned_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)

    if report.findings and not args.report:
        return 1
    return 0


def _check_progress_export(db_path: pathlib.Path) -> list[Finding]:
    """Проверить экспорт прогресса на накопленной истории прогона."""
    try:
        report = build_progress_report(db_path)
        render_markdown(report)
    except Exception as exc:
        return [Finding("(вся база)", "learning", "error", f"экспорт прогресса: {exc}")]
    return []


if __name__ == "__main__":
    sys.exit(main())
