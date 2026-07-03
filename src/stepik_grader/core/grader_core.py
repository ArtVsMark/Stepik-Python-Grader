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
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil

from stepik_grader.config import CONFIG

# resource — POSIX-only (RLIMIT_AS для best-effort memory cap, issue #43 S-01).
# На Windows модуль отсутствует; лимит памяти там не применяется (как и
# SIGALRM-таймаут в executor.py — тот же паттерн graceful degradation).
try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

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
# run_single_test() в grader_core.py использует subprocess.Popen напрямую,
# чтобы иметь доступ к замеру памяти (psutil) и точному времени.
# Импортируем RunResult для аннотаций и совместимости.
try:
    from stepik_grader.core.executor import (
        RunResult as _ExecutorRunResult,  # noqa: F401  (реэкспорт для тестов)
    )
except ImportError:
    _ExecutorRunResult = None  # type: ignore[assignment,misc]

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


def _make_memory_limiter(max_memory_mb: int | None) -> Callable[[], None] | None:
    """Вернуть preexec_fn, ограничивающий адресное пространство (RLIMIT_AS)
    дочернего процесса, или None, если лимит недоступен/отключён.

    Best-effort защита от неограниченного потребления памяти запускаемым
    решением (issue #43 S-01) — не замена полноценному OS-sandbox, у
    дочернего процесса по-прежнему нет изоляции файловой системы/сети.
    POSIX-only: на Windows модуль ``resource`` отсутствует, лимит не
    применяется (решение выполняется как раньше, без ограничения памяти).
    """
    if resource is None or max_memory_mb is None:
        return None

    limit_bytes = max_memory_mb * 1024 * 1024

    def _limit() -> None:
        # POSIX-only, typeshed excludes resource.setrlimit/RLIMIT_AS on win32.
        # Runs in the forked child before exec() -- any uncaught exception here
        # surfaces to the parent as subprocess.SubprocessError and aborts the
        # whole Popen() call (discovered via macOS CI, Sprint D): RLIMIT_AS
        # enforcement is unreliable on macOS, setrlimit can fail even for a
        # generous limit (e.g. due to how much virtual address space is
        # already mapped via the dyld shared cache before exec). Swallow the
        # failure so an unsupported/broken platform still runs the child,
        # just without the memory cap, instead of crashing subprocess
        # creation entirely.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))  # type: ignore[attr-defined]
        except (ValueError, OSError):
            pass

    return _limit


def _measure_peak_memory(
    proc: subprocess.Popen[bytes], result: list[float], stop: threading.Event
) -> None:
    """Поток: замерять RSS дочернего процесса до его завершения.

    Делает первый замер немедленно (до первого sleep), чтобы уловить
    даже очень короткие процессы (< 20 мс). Затем продолжает опрос
    каждые 20 мс до сигнала stop.

    Записывает пик памяти (МБ) в result[0].
    """

    # issue #48 R-05: proc.pid is read after Popen but before communicate() --
    # on a very short-lived child (especially on Windows) the process can exit
    # before psutil.Process(pid)/memory_info() ever samples it. The except
    # branches below already handle that, but previously did so silently,
    # returning peak=0.0 indistinguishable from "the process genuinely used
    # ~0 memory" -- warn so a caller doesn't mistake an unreliable reading for
    # a real measurement.
    def _warn_unreliable() -> None:
        warnings.warn(
            f"peak memory measurement unreliable for pid={proc.pid}: process "
            "exited before it could be sampled (reported peak may be 0.0 or "
            "an undercount)",
            stacklevel=2,
        )

    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        try:
            rss = ps_proc.memory_info().rss / 1024 / 1024
            if rss > peak:
                peak = rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _warn_unreliable()
            result[0] = peak
            return
        while not stop.is_set():
            try:
                rss = ps_proc.memory_info().rss / 1024 / 1024
                if rss > peak:
                    peak = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                _warn_unreliable()
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _warn_unreliable()
    result[0] = peak


# ---------------------------------------------------------------------------
# Исполнение и агрегация
# ---------------------------------------------------------------------------


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

    peak_mb_result: list[float] = [0.0]
    stop_event = threading.Event()
    mem_thread: threading.Thread | None = None

    # Гарантируем UTF-8 в stdout/stderr дочернего процесса на всех платформах
    # (на Windows по умолчанию используется cp1251, что ломает кириллицу в выводе).
    _child_env = os.environ.copy()
    _child_env["PYTHONIOENCODING"] = "utf-8"
    _child_env["PYTHONUTF8"] = "1"

    start = time.perf_counter()
    try:
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, run_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env,
            preexec_fn=_make_memory_limiter(CONFIG.max_memory_mb),
        )

        if measure_memory:
            mem_thread = threading.Thread(
                target=_measure_peak_memory,
                args=(proc, peak_mb_result, stop_event),
                daemon=True,
            )
            mem_thread.start()

        try:
            stdout_bytes, stderr_bytes = proc.communicate(input=stdin_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            stop_event.set()
            return {
                "passed": False,
                "output": [],
                "expected": case.expected_lines,
                "diff": "",
                "time": timeout,
                "memory": 0.0,
                "error": f"Timeout after {timeout}s",
                "timed_out": True,
                "verdict": "TLE",
            }
        finally:
            stop_event.set()
            # Удаляем временный wrapper-файл (contextlib.suppress — безопасно при краше)
            if tmp_wrapper is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_wrapper.name)

        elapsed = time.perf_counter() - start
        if mem_thread is not None:
            mem_thread.join(timeout=0.5)
        peak_mb = peak_mb_result[0]

        stdout = stdout_bytes.decode(ENCODING, errors="replace")
        stderr = stderr_bytes.decode(ENCODING, errors="replace")

        if proc.returncode != 0:
            return {
                "passed": False,
                "output": [],
                "expected": case.expected_lines,
                "diff": "",
                "time": elapsed,
                "memory": peak_mb,
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
            "time": elapsed,
            "memory": peak_mb,
            "error": "",
            "timed_out": False,
            "verdict": "AC" if passed else "WA",
        }

    except OSError as exc:
        stop_event.set()
        if tmp_wrapper is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_wrapper.name)
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
