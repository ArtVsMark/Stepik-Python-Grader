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
import os
import pathlib
import statistics
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from stepik_grader.config import CONFIG

__all__ = [
    "BenchStats",
    "TestCase",
    "is_function_only_solution",
    "is_solution_file",
    "find_all_solution_files",
    "collect_grouped_files",
    "load_test_cases",
    "load_text_lines",
    "run_single_test",
    "run_tests",
    "run_benchmark",
    "run_microbench_mode",
    "resolve_test_dir",
]
# TIMEOUT_SECONDS/ENCODING/SIMILAR_THRESHOLD/MUCH_SLOWER_THRESHOLD/
# MEASURE_CHILD_MEMORY/MICROBENCH_MAX_CASES — намеренно НЕ в __all__ (issue #52
# Q-03). Это просто module-level алиасы значений CONFIG (см. ниже), а не
# самостоятельный публичный API; их присутствие в __all__ создавало неявную
# зависимость на конкретные имена констант вместо GraderConfig. grader.py
# по-прежнему реэкспортирует их явно по имени (backward-compat __all__ этого
# фасада не менялся) — новый код должен читать stepik_grader.config.CONFIG.

# executor.py — вспомогательный модуль для запуска кода из строки (не из файла).
# run_solution() используется в тестах (tests/test_executor.py); grader сам его не вызывает.
# Импортируем RunResult для аннотаций и совместимости.
try:
    from stepik_grader.core.executor import (
        RunResult as _ExecutorRunResult,  # noqa: F401  (реэкспорт для тестов)
    )
except ImportError:
    _ExecutorRunResult = None  # type: ignore[assignment,misc]

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
    _detect_run_mode,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _is_python_code_block,
    _is_safe_constant,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _read_meta_function_name,
    is_function_only_solution,
)
from stepik_grader.core.normalizers import normalize_floats as _normalize_output_line
from stepik_grader.core.runner import (
    LocalRunner,
    Runner,  # noqa: F401  (реэкспорт — часть публичного API Runner-абстракции)
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

# Runner активен на весь процесс — сегодня всегда LocalRunner (issue #138).
# Инъекция другого Runner (напр. будущий SandboxRunner, issue #157) — задача
# server mode, не CLI/Web; grader_core не знает, какой Runner активен (см.
# docs/server-mode.md § Runner-слой, инвариант 2).
_RUNNER: Runner = LocalRunner()


def run_single_test(
    solution_path: str,
    case: TestCase,
    *,
    timeout: float = TIMEOUT_SECONDS,
    measure_memory: bool = MEASURE_CHILD_MEMORY,
) -> dict[str, Any]:
    """Запустить одно решение на одном тест-кейсе и вернуть словарь с результатами.

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
    """
    # --- Выбор стратегии запуска ---
    tmp_wrapper: Any = None  # NamedTemporaryFile или None
    run_path = solution_path
    stdin_bytes: bytes | None

    if case.test_type == "function":
        input_data = "\n".join(case.input_lines)
        if _is_python_code_block(input_data):
            # python-generation function-call: блок уже содержит print(func(...))
            wrapper_src = _build_call_wrapper(solution_path, input_data)
        else:
            # legacy function-mode: блок задаёт переменные, вызов собираем сами
            func_name = _read_meta_function_name(solution_path) or _ast_function_name(solution_path)
            if func_name is None:
                return {
                    "passed": False,
                    "output": [],
                    "expected": case.expected_lines,
                    "diff": "",
                    "time": 0.0,
                    "memory": 0.0,
                    "error": (
                        "function_name not found"
                        " (meta.json missing and no function def in solution)"
                    ),
                    "timed_out": False,
                    "verdict": "RE",
                }
            try:
                wrapper_src = _build_function_wrapper(solution_path, input_data, func_name)
            except ValueError as exc:
                return {
                    "passed": False,
                    "output": [],
                    "expected": case.expected_lines,
                    "diff": "",
                    "time": 0.0,
                    "memory": 0.0,
                    "error": str(exc),
                    "timed_out": False,
                    "verdict": "RE",
                }
        # Записываем wrapper во временный файл; удаляется после запуска
        tmp_wrapper = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            encoding=ENCODING,
            delete=False,
        )
        tmp_wrapper.write(wrapper_src)
        tmp_wrapper.flush()
        tmp_wrapper.close()
        run_path = tmp_wrapper.name
        stdin_bytes = None  # wrapper не читает stdin
    else:
        stdin_data = "\n".join(case.input_lines) + "\n"
        stdin_bytes = stdin_data.encode(ENCODING)

    spec = RunSpec(
        path=run_path,
        stdin=stdin_bytes,
        timeout=timeout,
        measure_memory=measure_memory,
        max_memory_mb=CONFIG.max_memory_mb,
    )
    try:
        outcome = _RUNNER.run(spec)
    finally:
        # Удаляем временный wrapper-файл (contextlib.suppress — безопасно при краше)
        if tmp_wrapper is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_wrapper.name)

    if outcome.launch_error is not None:
        return {
            "passed": False,
            "output": [],
            "expected": case.expected_lines,
            "diff": "",
            "time": 0.0,
            "memory": 0.0,
            "error": outcome.launch_error,
            "timed_out": False,
            "verdict": "RE",
        }

    if outcome.timed_out:
        return {
            "passed": False,
            "output": [],
            "expected": case.expected_lines,
            "diff": "",
            "time": outcome.elapsed,
            "memory": 0.0,
            "error": f"Timeout after {timeout}s",
            "timed_out": True,
            "verdict": "TLE",
        }

    stdout = outcome.stdout.decode(ENCODING, errors="replace")
    stderr = outcome.stderr.decode(ENCODING, errors="replace")

    if outcome.returncode != 0:
        return {
            "passed": False,
            "output": [],
            "expected": case.expected_lines,
            "diff": "",
            "time": outcome.elapsed,
            "memory": outcome.peak_memory_mb,
            "error": stderr.strip(),
            "timed_out": False,
            "verdict": "RE",
        }

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
    }


def run_tests(
    solution_path: str,
    test_dir: str,
    *,
    verbose: bool = False,
    verbose_callback: Callable[[TestCase, dict[str, Any]], None] | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить все тест-кейсы для решения и собрать статистику.

    verbose_callback: вызывается для каждого кейса при verbose=True (получает
        TestCase и результирующий dict run_single_test()); печать — забота
        вызывающей стороны (core/reporter.print_case_verbose), а не этой
        функции (issue #45 A-02 — устраняет обратный импорт Application/Logic
        → Application/UI). Если verbose=True, а callback не передан — кейсы
        просто не печатаются.

    Возвращаемый словарь:
        total      (int)   — число тест-кейсов
        passed     (int)   — прошло
        failed     (int)   — провалилось
        errors     (int)   — ошибки выполнения
        total_time (float) — суммарное время
        avg_time   (float) — среднее время на тест
        peak_memory_mb (float) — пик памяти (МБ)
        first_fail (int | None) — индекс первого упавшего теста
        cases      (list)  — детальные результаты по каждому кейсу
    """
    test_cases = load_test_cases(test_dir)
    # Определяем режим запуска один раз для всех тест-кейсов.
    _apply_run_mode_override(test_cases, solution_path, test_dir)

    results = []
    total_time = 0.0
    passed = 0
    failed = 0
    errors = 0
    first_fail: int | None = None
    peak_mb = 0.0

    for case in test_cases:
        r = run_single_test(solution_path, case, timeout=timeout)
        results.append(r)
        total_time += r["time"]
        peak_mb = max(peak_mb, r["memory"])

        if r["error"]:
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

    total = len(test_cases)
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
    solution_path: str,
    test_dir: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
    repeats: int = 15,
) -> dict[str, Any]:
    """Запустить все тест-кейсы в режиме benchmark и собрать статистику времени.

    Аргумент repeats задаёт число повторений каждого тест-кейса.
    Соответствует профилям нагрузки: low=5, medium=15, high=50, custom=5..100.

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
            r = run_single_test(solution_path, case, timeout=timeout)
            if r["error"] or r["timed_out"]:
                return {"error": r["error"] or "timeout", "runs": 0}
            times.append(r["time"])
            peak_mb = max(peak_mb, r["memory"])

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
    solution_paths: list[str],
    test_dir: str,
    *,
    number: int = 1000,
) -> dict[str, Any]:
    """Запустить timeit-microbench для нескольких решений и вернуть сводную статистику.

    peak_memory_mb (Issue #25) — максимум по всем кейсам решения: RSS через
    psutil для function-call блоков (run_single_test, как в run_benchmark),
    пик Python-heap через tracemalloc для stdin-блоков (run_microbench) —
    два разных метода измерения, см. докстринг core.microbench_runner.
    """
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    cases_to_bench = test_cases[:MICROBENCH_MAX_CASES]
    results: dict[str, dict[str, Any]] = {}

    for path in solution_paths:
        code = pathlib.Path(path).read_text(encoding=ENCODING)

        all_times: list[float] = []
        peak_mb = 0.0
        for case in cases_to_bench:
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

    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    return results
