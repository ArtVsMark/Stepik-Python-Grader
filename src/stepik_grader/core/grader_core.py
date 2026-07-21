"""grader_core.py — исполнение решений и агрегация статистики.

Архитектурный слой: Application / Business logic.
Отвечает за:
  - исполнение одного тест-кейса в subprocess (run_single_test) — выбор
    stdin/wrapper-стратегии, лимит памяти, точный тайминг;
  - агрегацию статистики по всем тест-кейсам (run_tests, run_benchmark,
    run_microbench_mode).

Обнаружение файлов-решений, загрузка тест-кейсов и резолюция test_dir —
core/test_loader.py. Определение режима запуска (stdin vs function) —
core/mode_detector.py. Генерация wrapper-скриптов — core/wrapper_builder.py.
Все три реэкспортируются здесь по имени для обратной совместимости (Issue
#45 A-01 — этот файл был 1200+ строк).

Не содержит вывода (rich-таблицы) — это core/reporter.py; не содержит CLI/меню —
это cli.py.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 2).
Перенесён в core/ (Issue #26).
"""

from __future__ import annotations

import contextlib
import difflib
import pathlib
import statistics
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from stepik_grader.config import CONFIG

__all__ = [
    "BenchStats",
    "TestCase",
    "active_runner",
    "collect_grouped_files",
    "find_all_solution_files",
    "is_function_only_solution",
    "is_solution_file",
    "load_test_cases",
    "load_text_lines",
    "resolve_test_dir",
    "run_benchmark",
    "run_microbench_mode",
    "run_single_test",
    "run_spec",
    "run_tests",
    "set_runner",
]
# TIMEOUT_SECONDS/ENCODING/SIMILAR_THRESHOLD/MUCH_SLOWER_THRESHOLD/
# MEASURE_CHILD_MEMORY/MICROBENCH_MAX_CASES — намеренно НЕ в __all__ (issue #52
# Q-03). Это просто module-level алиасы значений CONFIG (см. ниже), а не
# самостоятельный публичный API; их присутствие в __all__ создавало неявную
# зависимость на конкретные имена констант вместо GraderConfig. grader.py
# по-прежнему реэкспортирует их явно по имени (backward-compat __all__ этого
# фасада не менялся) — новый код должен читать stepik_grader.config.CONFIG.

# run_single_test() делегирует фактический subprocess-запуск LocalRunner'у
# (issue #136/#137/#138, docs/server-mode.md § Runner-слой) — не меняет
# поведение, только выделяет абстракцию Runner для будущего SandboxRunner
# (issue #157). _apply_memory_limit/_measure_peak_memory реэкспортированы по
# имени (тот же паттерн, что для test_loader.py и др. — Issue #45 A-01):
# grader_core._apply_memory_limit/._measure_peak_memory и grader.py facade
# продолжают работать без изменений.
# test_loader.py / mode_detector.py / wrapper_builder.py — извлечены из этого
# файла (Issue #45 A-01). Реэкспортируются по имени (не через `import *`),
# чтобы __all__ и приватные имена, на которые опирается grader.py/cli.py/тесты,
# остались доступны как grader_core.X независимо от физического места
# определения. microbench_runner.py / normalizers.py — первоисточники
# timeit-бенчмарка и нормализации float-вывода, не затронуты этим разбиением.
from stepik_grader.core.microbench_runner import apply_relative_ranking, run_microbench
from stepik_grader.core.mode_detector import (
    _ast_function_name,
    _block_invokes_solution,
    _detect_run_mode,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _is_python_code_block,
    _is_safe_constant,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _read_meta_function_name,
    is_function_only_solution,
)
from stepik_grader.core.normalizers import normalize_floats as _normalize_output_line
from stepik_grader.core.result import CaseResult, Verdict
from stepik_grader.core.runner import (
    LocalRunner,
    Runner,
    RunOutcome,
    RunSpec,
    _apply_memory_limit,  # noqa: F401  (реэкспорт для тестов/grader.py facade)
    _measure_peak_memory,  # noqa: F401  (реэкспорт для тестов/grader.py facade)
)
from stepik_grader.core.test_loader import (
    _SOLUTION_FILE_RE,  # noqa: F401  (реэкспорт для grader.py)
    TestCase,
    _apply_run_mode_override,
    _parse_testblock_file,  # noqa: F401  (реэкспорт для grader.py)
    collect_grouped_files,
    find_all_solution_files,
    is_solution_file,
    load_test_cases,
    load_text_lines,
    resolve_test_dir,
)
from stepik_grader.core.wrapper_builder import (
    _build_call_wrapper,
    _build_function_wrapper,
)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Значения читаются из config.CONFIG (единая точка правды, Sprint 6.3) —
# переопределяются через [tool.stepik-grader] в pyproject.toml.
TIMEOUT_SECONDS: float = CONFIG.timeout_seconds
ENCODING: str = CONFIG.encoding
SIMILAR_THRESHOLD: float = CONFIG.similar_threshold
MUCH_SLOWER_THRESHOLD: float = CONFIG.much_slower_threshold
MEASURE_CHILD_MEMORY: bool = CONFIG.measure_child_memory
MICROBENCH_MAX_CASES: int = CONFIG.microbench_max_cases

# ---------------------------------------------------------------------------
# Вспомогательные типы
# ---------------------------------------------------------------------------


@dataclass
class BenchStats:
    """Унифицированная статистика замеров для режимов 3 и 4.

    Устраняет дублирование вычислений между run_benchmark() и _micro_stats().
    """

    timings: list[float]

    @property
    def min(self) -> float:
        """Минимальное время замера."""
        return min(self.timings)

    @property
    def median(self) -> float:
        """Медианное время — основной ориентир при сравнении решений."""
        return statistics.median(self.timings)

    @property
    def mean(self) -> float:
        """Среднее время замера."""
        return statistics.mean(self.timings)

    @property
    def stdev(self) -> float:
        """Среднеквадратичное отклонение; 0.0 при единственном замере."""
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0

    @property
    def max(self) -> float:
        """Максимальное время замера."""
        return max(self.timings)

    def relative_to(self, baseline: float) -> float:
        """Возвращает median / baseline * 100 (процент от эталона)."""
        return (self.median / baseline * 100) if baseline > 0 else 0.0


# _apply_memory_limit/_measure_peak_memory перенесены в core/runner.py вместе
# с самим subprocess-запуском (issue #136/#137/#138, Runner-абстракция —
# docs/server-mode.md § Runner-слой). Реэкспортированы по имени ниже — тот же
# паттерн, что и для test_loader.py/mode_detector.py/wrapper_builder.py
# (Issue #45 A-01): grader_core._apply_memory_limit/._measure_peak_memory и
# grader.py facade продолжают работать без изменений.


# ---------------------------------------------------------------------------
# Исполнение и агрегация
# ---------------------------------------------------------------------------

# Runner активен на весь процесс — по умолчанию LocalRunner (issue #138);
# CLI подменяет его на SandboxRunner (issue #266, core/sandbox/) через
# set_runner() при --sandbox. grader_core не знает, какой Runner активен —
# только вызывает run(spec) (см. docs/server-mode.md § Runner-слой,
# инвариант 2); никакой логики этого модуля инъекция не меняет.
_RUNNER: Runner = LocalRunner()


def set_runner(runner: Runner) -> None:
    """Подменить активный ``Runner`` на весь процесс (issue #266).

    Единственная точка инъекции ``SandboxRunner``/иной реализации — вызывается
    один раз при старте CLI (``--sandbox``), до диспетчеризации в конкретный
    режим. Не влияет на поведение, если не вызывается: дефолт — ``LocalRunner``.
    """
    global _RUNNER
    _RUNNER = runner


def run_spec(spec: RunSpec) -> RunOutcome:
    """Исполнить один ``RunSpec`` через активный ``Runner`` и вернуть сырой итог.

    Публичная точка запуска для потребителей вне грейдинга (web-песочница,
    issue #317): прячет выбор backend'а (``LocalRunner``/``SandboxRunner``) за
    публичной поверхностью — вызывающему не нужно (и нельзя, ADR-0010) трогать
    приватный синглтон ``_RUNNER``. Читает module-global при каждом вызове,
    поэтому ``set_runner()`` и тестовые подмены ``_RUNNER`` видны немедленно.
    """
    return _RUNNER.run(spec)


def active_runner() -> Runner:
    """Активный ``Runner`` процесса — публичный аксессор его capability-флагов.

    Замена прямому доступу к приватному ``_RUNNER`` (issue #550): ``core/tracer``
    консультирует ``active_runner().supports_project_imports``, чтобы решить,
    доступен ли пошаговый трейс, вместо хрупкого ``type(_RUNNER).__name__ ==
    "SandboxRunner"``. Читает module-global при каждом вызове — ``set_runner()``
    и тестовые подмены видны немедленно.
    """
    return _RUNNER


def _fail_result(
    case: TestCase,
    *,
    error: str,
    verdict: Verdict,
    time: float = 0.0,
    memory: float = 0.0,
    timed_out: bool = False,
    exit_code: int | None = None,
) -> CaseResult:
    """Case-result dict для неуспешного раннего исхода ``run_single_test``.

    Общая форма всех возвратов до сравнения вывода
    (RE/TLE/CANCELLED/SANDBOX_VIOLATION): ``passed=False``, пустой ``output``,
    ``expected`` из кейса, без ``diff``. Различаются только
    ``error``/``verdict``/``time``/``memory``/``timed_out``/``exit_code``
    (issue #354 — убирает семь почти одинаковых литералов).
    """
    return {
        "passed": False,
        "output": [],
        "expected": case.expected_lines,
        "diff": "",
        "time": time,
        "memory": memory,
        "error": error,
        "timed_out": timed_out,
        "verdict": verdict,
        "exit_code": exit_code,
    }


@dataclass(frozen=True)
class _RunPlan:
    """План запуска одного кейса в ``run_single_test`` (issue #406).

    Либо готовый ``spec`` (плюс ``tmp_wrapper_path`` — временный wrapper-файл
    function-mode, который ``run_single_test`` удаляет после запуска), либо
    ``error`` — ошибка подготовки (нет ``function_name`` / невалидный wrapper),
    при которой запуск не производится и кейс сразу маппится в RE. Инвариант:
    ровно одно из ``spec``/``error`` заполнено.
    """

    spec: RunSpec | None = None
    tmp_wrapper_path: pathlib.Path | None = None
    error: str | None = None


def _prepare_run_spec(
    solution_path: pathlib.Path,
    case: TestCase,
    *,
    timeout: float,
    measure_memory: bool,
    cancel_event: threading.Event | None,
) -> _RunPlan:
    """Выбрать стратегию запуска кейса и собрать ``RunSpec`` (issue #406).

    stdin-режим → ``RunSpec`` со stdin-байтами кейса (``tmp_wrapper_path`` None).
    function-режим → сгенерировать wrapper (python-generation call-блок или
      legacy function-mode), записать во временный ``.py`` и указать на него
      ``RunSpec.path`` (``tmp_wrapper_path`` задан — вызывающая сторона удаляет
      файл после запуска; сам файл решения не модифицируется). Ошибка
      подготовки (нет ``function_name`` / ``ValueError`` из wrapper-builder) →
      ``_RunPlan(error=...)``: запуск не делается, кейс сразу RE.

    Выделена из ``run_single_test`` — вся не-исполняющая «стратегия+wrapper»
    логика в одном месте, тестируемая без реального subprocess-запуска.
    """
    if case.test_type != "function":
        stdin_data = "\n".join(case.input_lines) + "\n"
        return _RunPlan(
            spec=RunSpec(
                path=solution_path,
                stdin=stdin_data.encode(ENCODING),
                timeout=timeout,
                measure_memory=measure_memory,
                max_memory_mb=CONFIG.max_memory_mb,
                max_output_bytes=CONFIG.max_output_bytes,
                cancel_event=cancel_event,
            )
        )

    input_data = "\n".join(case.input_lines)
    # Маршрут выбирается по тому, печатает ли блок результат сам (формат 3),
    # а не по «похоже ли на Python-код»: присваивание `a = 5` — это данные
    # legacy-теста, а не драйвер (issue #622).
    func_name = _read_meta_function_name(solution_path) or _ast_function_name(solution_path)
    if _block_invokes_solution(input_data, func_name):
        # python-generation function-call: блок уже содержит print(func(...))
        wrapper_src = _build_call_wrapper(solution_path, input_data)
    else:
        # legacy function-mode: блок задаёт данные, вызов собираем сами
        if func_name is None:
            return _RunPlan(
                error=(
                    "function_name not found (meta.json missing and no function def in solution)"
                )
            )
        try:
            wrapper_src = _build_function_wrapper(solution_path, input_data, func_name)
        except ValueError as exc:
            return _RunPlan(error=str(exc))

    # Записываем wrapper во временный файл; run_single_test удалит его после запуска.

    # путь уходит в RunSpec раннеру), удаляется вызывающей стороной после запуска.
    tmp_wrapper = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w",
        suffix=".py",
        encoding=ENCODING,
        delete=False,
    )
    tmp_wrapper.write(wrapper_src)
    tmp_wrapper.flush()
    tmp_wrapper.close()
    wrapper_path = pathlib.Path(tmp_wrapper.name)
    return _RunPlan(
        spec=RunSpec(
            path=wrapper_path,
            stdin=None,  # wrapper не читает stdin
            timeout=timeout,
            measure_memory=measure_memory,
            max_memory_mb=CONFIG.max_memory_mb,
            max_output_bytes=CONFIG.max_output_bytes,
            cancel_event=cancel_event,
        ),
        tmp_wrapper_path=wrapper_path,
    )


def _map_outcome_to_result(
    outcome: RunOutcome,
    case: TestCase,
    timeout: float,
) -> CaseResult:
    """Чистая функция: сырой ``RunOutcome`` → словарь-результат кейса (issue #406).

    Порядок веток verdict: ``launch_error`` → RE; ``sandbox_violation`` →
    SANDBOX_VIOLATION; ``cancelled`` → CANCELLED; ``timed_out`` → TLE;
    ненулевой ``returncode`` → RE; иначе сравнение вывода (с нормализацией
    float-строк) → AC/WA (+unified diff). Без I/O и без subprocess — каждую
    ветку verdict можно проверить изолированно (issue #406 acceptance).
    """
    if outcome.launch_error is not None:
        return _fail_result(case, error=outcome.launch_error, verdict="RE")

    if outcome.sandbox_violation is not None:
        # issue #266 — SandboxRunner (core/sandbox/) proactively killed the
        # process for exceeding a quota it detects itself (memory/output_size/
        # cpu), not a genuine timeout or a plain crash: distinct verdict,
        # additive to AC/WA/RE/TLE/CANCELLED (docs/server-mode.md § Классы
        # ошибок). Network/filesystem/process-count violations are rejected
        # by the kernel INSIDE the sandbox and surface as an ordinary non-zero
        # exit (RE) instead -- Runner doesn't inspect the child's traceback to
        # relabel those. LocalRunner never sets this field.
        return _fail_result(
            case,
            error=f"Sandbox violation: {outcome.sandbox_violation}",
            verdict="SANDBOX_VIOLATION",
            time=outcome.elapsed,
            memory=outcome.peak_memory_mb,
        )

    if outcome.cancelled:
        # issue #262 — async job model cancellation, distinct from a genuine
        # solution timeout (TLE): a cancelled run must never be mislabeled as
        # "your solution is too slow" in the UI.
        return _fail_result(
            case,
            error="Cancelled by user",
            verdict="CANCELLED",
            time=outcome.elapsed,
        )

    if outcome.timed_out:
        return _fail_result(
            case,
            error=f"Timeout after {timeout}s",
            verdict="TLE",
            time=outcome.elapsed,
            timed_out=True,
        )

    stdout = outcome.stdout.decode(ENCODING, errors="replace")
    stderr = outcome.stderr.decode(ENCODING, errors="replace")

    if outcome.returncode != 0:
        # Процесс, убитый сигналом (segfault, OOM-killer, отрицательный
        # returncode), не оставляет stderr. Пустая строка ошибки делала такой
        # результат неотличимым от WA в статистике run_tests — она считает
        # ошибки по truthiness поля error, и RE молча попадал в failed
        # (issue #625). Подставляем осмысленное сообщение с кодом выхода.
        return _fail_result(
            case,
            error=stderr.strip() or f"Process exited with code {outcome.returncode} (no stderr)",
            verdict="RE",
            time=outcome.elapsed,
            memory=outcome.peak_memory_mb,
            exit_code=outcome.returncode,
        )

    actual_lines = [line.rstrip("\n") for line in stdout.splitlines()]
    passed = actual_lines == case.expected_lines
    if not passed and len(actual_lines) == len(case.expected_lines):
        passed = all(
            _normalize_output_line(a) == _normalize_output_line(e)
            for a, e in zip(actual_lines, case.expected_lines, strict=True)
        )
    diff_str = ""
    if not passed:
        diff_str = "\n".join(
            difflib.unified_diff(
                case.expected_lines,
                actual_lines,
                fromfile="expected",
                tofile="actual",
                lineterm="",
            )
        )

    return {
        "passed": passed,
        "output": actual_lines,
        "expected": case.expected_lines,
        "diff": diff_str,
        "time": outcome.elapsed,
        "memory": outcome.peak_memory_mb,
        "error": "",
        "timed_out": False,
        "verdict": "AC" if passed else "WA",
        "exit_code": outcome.returncode,
    }


def run_single_test(
    solution_path: pathlib.Path,
    case: TestCase,
    *,
    timeout: float = TIMEOUT_SECONDS,
    measure_memory: bool = MEASURE_CHILD_MEMORY,
    cancel_event: threading.Event | None = None,
) -> CaseResult:
    """Запустить одно решение на одном тест-кейсе и вернуть словарь с результатами.

    Тонкий оркестратор (issue #406): ``_prepare_run_spec`` выбирает стратегию
    (stdin/function-wrapper) и собирает ``RunSpec`` либо возвращает prep-ошибку;
    ``run_spec()`` исполняет spec через активный Runner; ``_map_outcome_to_result``
    маппит сырой ``RunOutcome`` в словарь-результат. Временный wrapper-файл
    function-mode удаляется здесь, после запуска.

    Для test_type='stdin'  — запускает решение напрямую, подаёт stdin.
    Для test_type='function' — генерирует временный wrapper-скрипт,
      который импортирует функцию и вызывает её с аргументами из input_data.
      Файл решения при этом не модифицируется.

    Возвращаемый словарь:
        passed    (bool)   — прошёл ли тест
        output    (list)   — фактический вывод (строки)
        expected  (list)   — ожидаемый вывод (строки)
        diff      (str)    — unified diff при несовпадении
        time      (float)  — время выполнения в секундах
        memory    (float)  — пик памяти в МБ (0 если measure_memory=False)
        error     (str)    — сообщение об ошибке (пустая = нет ошибки)
        timed_out (bool)   — истёк ли таймаут
        exit_code (int | None) — код возврата процесса решения; None, если
            процесс не завершился нормально (ошибка запуска/таймаут) —
            issue #125, ErrorCard.exit_code в web-слое.
    """
    plan = _prepare_run_spec(
        solution_path,
        case,
        timeout=timeout,
        measure_memory=measure_memory,
        cancel_event=cancel_event,
    )
    if plan.error is not None:
        # prep-ошибка (нет function_name / невалидный wrapper) → RE без запуска.
        return _fail_result(case, error=plan.error, verdict="RE")
    if plan.spec is None:  # pragma: no cover — инвариант _RunPlan: error is None ⇒ spec задан
        return _fail_result(case, error="run spec preparation failed", verdict="RE")

    try:
        # issue #640: через публичный run_spec(), а не прямой _RUNNER.run — так
        # `_RUNNER.run(spec)` живёт в единственном месте (run_spec), которое и
        # станет точкой выбора per-request Runner'а при серверном пивоте.
        outcome = run_spec(plan.spec)
    finally:
        # Удаляем временный wrapper-файл (contextlib.suppress — безопасно при краше)
        if plan.tmp_wrapper_path is not None:
            with contextlib.suppress(OSError):
                plan.tmp_wrapper_path.unlink()

    return _map_outcome_to_result(outcome, case, timeout)


def run_tests(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    verbose: bool = False,
    verbose_callback: Callable[[TestCase, CaseResult], None] | None = None,
    timeout: float = TIMEOUT_SECONDS,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Запустить все тест-кейсы для решения и собрать статистику.

    verbose_callback: вызывается для каждого кейса при verbose=True (получает
        TestCase и результирующий dict run_single_test()); печать — забота
        вызывающей стороны (core/reporter.print_case_verbose), а не этой
        функции (issue #45 A-02 — устраняет обратный импорт Application/Logic
        → Application/UI). Если verbose=True, а callback не передан — кейсы
        просто не печатаются.

    progress_callback/cancel_event (issue #262, оба по умолчанию None — CLI и
    синхронный ``/api/grade`` их не передают, поведение не меняется):
    ``progress_callback`` вызывается с ``1`` после каждого завершённого кейса
    (сколько именно кейсов — знает вызывающая сторона, эта функция ничего не
    знает про глобальный total из web-job; см. ``web/runs.py``).
    ``cancel_event`` проверяется ПЕРЕД каждым кейсом (не начинать новый
    subprocess-запуск после отмены) и прокидывается в ``run_single_test()``,
    которая может прервать уже запущенный кейс через ``LocalRunner``-поллинг.

    Возвращаемый словарь:
        total      (int)   — число тест-кейсов
        passed     (int)   — прошло
        failed     (int)   — провалилось
        errors     (int)   — ошибки выполнения
        total_time (float) — суммарное время
        avg_time   (float) — среднее время на тест
        peak_memory_mb (float) — пик памяти (МБ)
        first_fail (int | None) — индекс первого упавшего теста
        cases      (list)  — детальные результаты по каждому кейсу; каждый
                             включает "stdin" (вход кейса, issue #397)
    """
    test_cases = load_test_cases(test_dir)
    # Определяем режим запуска один раз для всех тест-кейсов.
    _apply_run_mode_override(test_cases, solution_path, test_dir)

    results: list[CaseResult] = []
    total_time = 0.0
    passed = 0
    failed = 0
    errors = 0
    first_fail: int | None = None
    peak_mb = 0.0

    for case in test_cases:
        if cancel_event is not None and cancel_event.is_set():
            break
        r = run_single_test(solution_path, case, timeout=timeout, cancel_event=cancel_event)
        # issue #397: приложить stdin кейса к результату — web-презентация
        # (grade_path → ErrorCard) больше не перечитывает тест-кейсы вторым
        # проходом ради stdin. Дешевле и без zip по позиции (снимает хрупкость
        # #422 на отмене).
        r["stdin"] = "\n".join(case.input_lines)
        results.append(r)
        total_time += r["time"]
        peak_mb = max(peak_mb, r["memory"])

        if r.get("verdict") == "CANCELLED":
            # Отмена прогона — не вердикт о решении (issue #625). Раньше кейс
            # попадал в errors и выставлял first_fail, из-за чего UI показывал
            # «первый упавший тест» там, где пользователь просто нажал «Отмена».
            # Кейс остаётся в cases/total, чтобы было видно, на чём прервались.
            pass
        elif r["error"]:
            errors += 1
            if first_fail is None:
                first_fail = case.index
        elif r["passed"]:
            passed += 1
        else:
            failed += 1
            if first_fail is None:
                first_fail = case.index

        if verbose and verbose_callback is not None:
            verbose_callback(case, r)
        if progress_callback is not None:
            progress_callback(1)

    # len(results), не len(test_cases): при отмене (cancel_event) цикл
    # прерывается раньше — total должен отражать фактически прогнанные кейсы,
    # иначе avg_time занижается на незавершённые. В обычном (не отменённом)
    # случае оба значения совпадают — существующее поведение не меняется.
    total = len(results)
    avg_time = total_time / total if total else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_time": total_time,
        "avg_time": avg_time,
        "peak_memory_mb": peak_mb,
        "first_fail": first_fail,
        "cases": results,
    }


def run_benchmark(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    timeout: float = TIMEOUT_SECONDS,
    repeats: int = 15,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Запустить все тест-кейсы в режиме benchmark и собрать статистику времени.

    Аргумент repeats задаёт число повторений каждого тест-кейса.
    Соответствует профилям нагрузки: low=5, medium=15, high=50, custom=5..100.

    progress_callback/cancel_event (issue #262, по умолчанию None — поведение
    без них не меняется): ``progress_callback`` тикает после каждого
    единичного повтора (case × repeat), а не только после кейса целиком —
    самая мелкая гранулярность, где это дешевле всего (bench — самый долгий
    путь, который и жаловался issue). ``cancel_event`` проверяется перед
    каждым повтором; ранний выход помечается ``"cancelled": True`` в
    результате (отличимо от истечения timeout/иной ошибки).

    Возвращаемый словарь:
        runs       (int)   — число запусков (test_cases * repeats)
        min/max/mean/median/stdev (float) — статистика времени (секунды)
        peak_memory_mb (float)
        relative   (float) — задаётся снаружи при сравнении
        verdict    (str)   — задаётся снаружи
        error      (str)   — пустая строка если нет ошибок
    """
    test_cases = load_test_cases(test_dir)
    # Определяем режим запуска один раз — как в run_tests().
    # Иначе function-mode задачи прогоняются в неверном stdin-режиме.
    _apply_run_mode_override(test_cases, solution_path, test_dir)

    times: list[float] = []
    peak_mb = 0.0

    for case in test_cases:
        for _ in range(max(1, repeats)):
            if cancel_event is not None and cancel_event.is_set():
                return {"error": "cancelled", "runs": 0, "cancelled": True}
            r = run_single_test(solution_path, case, timeout=timeout, cancel_event=cancel_event)
            if r.get("verdict") == "CANCELLED":
                return {"error": r["error"], "runs": 0, "cancelled": True}
            if r["error"] or r["timed_out"]:
                return {"error": r["error"] or "timeout", "runs": 0}
            times.append(r["time"])
            peak_mb = max(peak_mb, r["memory"])
            if progress_callback is not None:
                progress_callback(1)

    if not times:
        return {"error": "no test cases", "runs": 0}

    bench_stats = BenchStats(timings=times)
    return {
        "runs": len(times),
        "min": bench_stats.min,
        "max": bench_stats.max,
        "mean": bench_stats.mean,
        "median": bench_stats.median,
        "stdev": bench_stats.stdev,
        "peak_memory_mb": peak_mb,
        "relative": 1.0,
        "verdict": "SIMILAR",
        "error": "",
    }


def _micro_stats(times: list[float]) -> dict[str, float]:
    """Вычислить статистику по списку замеров времени."""
    bench_stats = BenchStats(timings=times)
    return {
        "min": bench_stats.min,
        "max": bench_stats.max,
        "mean": bench_stats.mean,
        "median": bench_stats.median,
        "stdev": bench_stats.stdev,
    }


def _verdict(relative: float) -> str:
    """Вернуть текстовый вердикт по относительному времени."""
    if relative <= SIMILAR_THRESHOLD:
        return "SIMILAR"
    if relative <= MUCH_SLOWER_THRESHOLD:
        return "SLOWER"
    return "MUCH_SLOWER"


def run_microbench_mode(
    solution_paths: list[pathlib.Path],
    test_dir: pathlib.Path,
    *,
    number: int = 1000,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[pathlib.Path, dict[str, Any]]:
    """Запустить timeit-microbench для нескольких решений и вернуть сводную статистику.

    peak_memory_mb (Issue #25) — максимум по всем кейсам решения: RSS через
    psutil для function-call блоков (run_single_test, как в run_benchmark),
    пик Python-heap через tracemalloc для stdin-блоков (run_microbench) —
    два разных метода измерения, см. докстринг core.microbench_runner.

    progress_callback/cancel_event (issue #262, по умолчанию None):
    гранулярность здесь ГРУБЕЕ, чем в ``run_benchmark`` — один тик на решение
    целиком, не на кейс/повтор. ``timeit``-цикл внутри ``run_microbench()``
    не прерываем без переделки ``core/microbench_runner.py`` (вне scope
    issue #262) — отмена проверяется только МЕЖДУ решениями, текущее
    решение в процессе микробенчмарка досчитывается до конца.
    """
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    cases_to_bench = test_cases[:MICROBENCH_MAX_CASES]
    results: dict[pathlib.Path, dict[str, Any]] = {}

    for path in solution_paths:
        if cancel_event is not None and cancel_event.is_set():
            break
        code = path.read_text(encoding=ENCODING)

        # issue #623: режим запуска определяется РЕШЕНИЕМ (meta.json/AST), а не
        # только per-case .type — как это уже делают run_tests и run_benchmark.
        # Без override function-only решение уходило на stdin-путь, где микробенч
        # мерит лишь время определения функций и выдаёт бессмысленный SIMILAR.
        # Копия кейсов обязательна: _apply_run_mode_override мутирует список на
        # месте и не откатывает "function" обратно, поэтому общий список протёк
        # бы на следующие решения группы.
        cases_for_path = _apply_run_mode_override(
            [replace(case) for case in cases_to_bench], path, test_dir
        )

        all_times: list[float] = []
        peak_mb = 0.0
        for case in cases_for_path:
            input_data = "\n".join(case.input_lines)

            if case.test_type == "function" and _is_python_code_block(input_data):
                # Function-call блок — это Python-код, а не stdin.
                # timeit/exec тут не годится: используем subprocess-тайминг
                # через run_single_test (менее точно, зато корректно).
                # run_single_test уже измеряет RSS через psutil (как в режиме 3).
                sub_repeats = max(1, number // 50)
                case_times: list[float] = []
                for _ in range(sub_repeats):
                    r = run_single_test(path, case, timeout=60.0)
                    if r["error"] or r["timed_out"]:
                        results[path] = {"error": f"test {case.index}: {r['error'] or 'timeout'}"}
                        break
                    case_times.append(r["time"])
                    peak_mb = max(peak_mb, r["memory"])
                else:
                    all_times.extend(case_times)
                    continue
                break

            stdin_data = input_data + "\n"
            bench = run_microbench(
                code, stdin_data=stdin_data, number=number, max_memory_mb=CONFIG.max_memory_mb
            )
            if bench["error"]:
                results[path] = {"error": f"test {case.index}: {bench['error']}"}
                break
            all_times.extend(bench["times"])
            peak_mb = max(peak_mb, bench["peak_memory_mb"])
        else:
            stats = _micro_stats(all_times)
            stats["runs"] = len(all_times)
            stats["peak_memory_mb"] = peak_mb
            results[path] = stats

        if progress_callback is not None:
            progress_callback(1)

    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    return results
