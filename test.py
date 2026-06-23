from __future__ import annotations

import ast
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from threading import Thread

import chardet
import psutil

from microbench_runner import (
    SIMILAR_THRESHOLD_PERCENT,
    MicrobenchResult,
    apply_relative_micro,
    run_microbench,
)

MEASURE_CHILD_MEMORY = False
CHILD_MEMORY_POLL_INTERVAL = 0.01
# SIMILAR_THRESHOLD_PERCENT импортируется из microbench_runner — единый источник истины
MICROBENCH_MAX_CASES = 5  # ≤5 тест-кейсов в microbench: достаточно для стабильного std-dev,
# не перегружает timeit при большом числе repeats
SUBPROCESS_TIMEOUT = 10.0  # секунд: защита от бесконечных циклов в решениях студентов

# Кешируем один раз — значение константно на всё время запуска
PYTHON_CMD: str = "python3" if sys.platform in {"linux", "linux2", "darwin"} else "python"

# Паттерн имён файлов-решений.
# Принимает оба стиля именования:
#   task_1.py, task_2.py        — стиль at_first.py
#   task1.py, task1_2.py        — альтернативный стиль
#   task.py                     — базовое имя
_SOLUTION_FILE_RE = re.compile(r"task_?\d*(?:_\d+)?\.py")


@dataclass
class TestRunResult:
    passed: bool
    elapsed_time: float
    memory_mb: float
    error_message: str = ""


@dataclass
class VerificationResult:
    file: str
    total_tests: int
    passed_tests: int
    total_time: float
    avg_time: float
    peak_memory_mb: float
    status: str
    failed_test_index: int | None = None
    error_message: str = ""


@dataclass
class BenchmarkStats:
    file: str
    repeats: int
    total_tests: int
    total_runs: int
    min_time: float
    median_time: float
    mean_time: float
    max_time: float
    std_dev_time: float
    peak_memory_mb: float
    relative_percent: float = 100.0
    verdict: str = "OK"


@dataclass
class GradeCase:
    """Один тест-кейс: входные строки и ожидаемый вывод.

    Переименован из TestCase, чтобы pytest не пытался его собирать
    как тестовый класс (PytestCollectionWarning).
    """

    index: int
    input_lines: list[str]
    expected_lines: list[str]


def _is_safe_constant(node: ast.expr) -> bool:
    """Вернуть True, если узел — безопасное константное выражение без вызовов.

    Рекурсивно проверяет AST-узел: принимает литералы (Constant), арифметику
    из констант (BinOp, UnaryOp) и вложенные контейнеры (List/Tuple/Set/Dict).
    Отклоняет любые вызовы (Call), обращения к атрибутам (Attribute) и Name.

    Не использует ast.literal_eval — его поведение на AST-узлах не документировано
    и изменилось в Python 3.14.
    """
    match node:
        case ast.Constant():
            return True
        case ast.UnaryOp(op=ast.USub() | ast.UAdd() | ast.Invert(), operand=operand):
            return _is_safe_constant(operand)
        case ast.BinOp(left=left, right=right):
            return _is_safe_constant(left) and _is_safe_constant(right)
        case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
            return all(_is_safe_constant(e) for e in elts)
        case ast.Dict(keys=keys, values=values):
            return all(_is_safe_constant(k) for k in keys if k is not None) and all(
                _is_safe_constant(v) for v in values
            )
        case _:
            return False


def is_function_only_solution(file_content: str) -> bool:
    """Вернуть True, если файл содержит только определения функций (без точки входа).

    При SyntaxError в исходнике возвращает False — файл будет запущен как скрипт
    напрямую, и ошибка будет поймана subprocess'ом с нормальным выводом в stderr.
    """
    try:
        tree = ast.parse(file_content)
    except SyntaxError:
        return False  # 🔴 ИСПРАВЛЕНО: раньше SyntaxError пробрасывался наверх

    allowed_nodes = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,
        ast.Pass,
    )

    for node in tree.body:
        if not isinstance(node, allowed_nodes):
            return False

        if isinstance(node, ast.Expr):
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                return False

        if isinstance(node, ast.Assign):
            # 🔴 ИСПРАВЛЕНО: заменена isinstance-проверка на _is_safe_constant —
            # теперь принимаются BinOp/UnaryOp (напр. MOD = 10 ** 9 + 7),
            # но по-прежнему отклоняются вызовы функций (data = sys.stdin.read()).
            if not _is_safe_constant(node.value):
                return False

        if isinstance(node, ast.AnnAssign):
            # 🔴 ИСПРАВЛЕНО: аналогично для аннотированных присваиваний
            # (напр. MOD: int = 10 ** 9 + 7).
            if node.value is not None and not _is_safe_constant(node.value):
                return False

    return any(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) for node in tree.body)


def is_solution_file(file_name: str) -> bool:
    """Вернуть True, если имя файла соответствует шаблону решения.

    Принимаемые форматы:
        task.py, task1.py, task1_2.py   — исторический стиль
        task_1.py, task_2.py            — стиль, создаваемый at_first.py
    """
    return bool(_SOLUTION_FILE_RE.fullmatch(file_name))


def find_all_solution_files(directory: str) -> list[str]:
    scripts = []

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                scripts.append(os.path.join(root, file_name))

    return sorted(scripts)


def collect_grouped_files(target_dir: pathlib.Path, base_dir: pathlib.Path) -> dict[str, list[str]]:
    """Найти все solution-файлы в target_dir и сгруппировать по папке задачи.

    Вынесено из трёх mode-runner'ов для устранения дублирования.
    Ключ — rel_path папки задачи от base_dir; значение — список rel_path файлов.
    """
    all_files = find_all_solution_files(str(target_dir))
    grouped: dict[str, list[str]] = defaultdict(list)
    for abs_path in all_files:
        rel_path = os.path.relpath(abs_path, base_dir)
        task_folder = os.path.dirname(rel_path)
        grouped[task_folder].append(rel_path)
    return grouped


def resolve_and_validate_dir(
    base_dir: pathlib.Path, user_input: str, prompt: str = "папка"
) -> pathlib.Path | None:
    """Резолвить пользовательский ввод в Path и проверить, что это существующая директория.

    Возвращает Path при успехе или None с выводом ошибки при неудаче.
    Вынесено из трёх mode-runner'ов для устранения дублирования.
    """
    target = resolve_input_path(base_dir, user_input)
    if not target.exists():
        print(f"{prompt.capitalize()} не найдена: {target}")
        return None
    if not target.is_dir():
        print(f"Это не директория: {target}")
        return None
    return target


def load_text_lines(file_path: str) -> list[str]:
    """Загрузить текстовый файл построчно с авто-определением кодировки."""
    with open(file_path, "rb") as binary_file:
        raw_data = binary_file.read()
    file_encoding = chardet.detect(raw_data)["encoding"] or "utf-8"
    return raw_data.decode(file_encoding, errors="replace").strip().splitlines()


def load_text_lines_with_encoding(file_path: str) -> tuple[list[str], str | None]:
    """Загрузить текстовый файл и вернуть (строки, кодировка).

    🟡 УЛУЧШЕНО: разделено из load_text_lines(return_encoding=True) —
    одна функция с bool-флагом возвращала два разных типа (нарушение PEP 20).
    """
    with open(file_path, "rb") as binary_file:
        raw_data = binary_file.read()
    file_encoding = chardet.detect(raw_data)["encoding"] or "utf-8"
    lines = raw_data.decode(file_encoding, errors="replace").strip().splitlines()
    return lines, file_encoding


def log_error(file: str) -> None:
    """Записать путь файла с ошибкой в errors.txt.

    🟡 УЛУЧШЕНО: обёрнуто в try/except — при отсутствии прав на запись
    grader продолжает работу вместо падения с PermissionError.
    """
    try:
        with open("./errors.txt", "a", encoding="utf-8") as errors_file:
            print(file, file=errors_file)
    except OSError as exc:
        print(f"Warning: не удалось записать в errors.txt: {exc}")


def resolve_input_path(base_dir: pathlib.Path, user_input: str) -> pathlib.Path:
    path = pathlib.Path(user_input.strip())
    return path if path.is_absolute() else (base_dir / path).resolve()


def print_test_mismatch(
    test_index: int,
    test_data: list[str],
    correct: list[str],
    result: list[str],
) -> None:
    print(f"Test#{test_index} Input:\n" + "\n".join(test_data))
    print(f"Test#{test_index} Expected Output:\n" + "\n".join(correct))
    print(f"Test#{test_index} Actual Output:\n" + "\n".join(result))


def run_process(
    executor_file: str,
    input_data: str,
    measure_child_memory: bool = False,
    poll_interval: float = 0.01,
) -> tuple[subprocess.CompletedProcess | None, float, float, str]:
    """Запустить subprocess с решением.

    🔴 ИСПРАВЛЕНО: добавлен timeout=SUBPROCESS_TIMEOUT во все ветки —
    бесконечный цикл в решении студента больше не подвешивает grader.
    Параметр python_cmd убран — используется модульная константа PYTHON_CMD.
    """
    parent_process = psutil.Process(os.getpid())
    start_time = time.perf_counter()

    if not measure_child_memory:
        try:
            completed = subprocess.run(
                [PYTHON_CMD, executor_file],
                input=input_data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=SUBPROCESS_TIMEOUT,  # 🔴 ИСПРАВЛЕНО
            )
        except subprocess.TimeoutExpired:
            elapsed_time = time.perf_counter() - start_time
            memory_mb = parent_process.memory_info().rss / 1024 / 1024
            return None, elapsed_time, memory_mb, f"TimeoutExpired (>{SUBPROCESS_TIMEOUT}s)"
        elapsed_time = time.perf_counter() - start_time
        memory_mb = parent_process.memory_info().rss / 1024 / 1024
        return completed, elapsed_time, memory_mb, ""

    proc = subprocess.Popen(
        [PYTHON_CMD, executor_file],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    peak_rss = 0
    monitor_error = ""

    def monitor_memory() -> None:
        nonlocal peak_rss, monitor_error
        try:
            child = psutil.Process(proc.pid)

            while proc.poll() is None:
                try:
                    rss = child.memory_info().rss
                    peak_rss = max(peak_rss, rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    break
                time.sleep(poll_interval)

            try:
                rss = child.memory_info().rss
                peak_rss = max(peak_rss, rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        except Exception as error:
            monitor_error = str(error)

    monitor_thread = Thread(target=monitor_memory, daemon=True)
    monitor_thread.start()

    try:
        stdout_data, stderr_data = proc.communicate(
            input=input_data,
            timeout=SUBPROCESS_TIMEOUT,  # 🔴 ИСПРАВЛЕНО
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        monitor_thread.join(timeout=1)
        elapsed_time = time.perf_counter() - start_time
        memory_mb = peak_rss / 1024 / 1024
        return None, elapsed_time, memory_mb, f"TimeoutExpired (>{SUBPROCESS_TIMEOUT}s)"

    monitor_thread.join(timeout=1)

    completed = subprocess.CompletedProcess(
        args=[PYTHON_CMD, executor_file],
        returncode=proc.returncode,
        stdout=stdout_data,
        stderr=stderr_data,
    )

    elapsed_time = time.perf_counter() - start_time
    memory_mb = peak_rss / 1024 / 1024
    return completed, elapsed_time, memory_mb, monitor_error


def run_test_once(
    file: str,
    test_case: GradeCase,
    executor_file: str,
    input_data: str,
    measure_child_memory: bool = False,
    poll_interval: float = 0.01,
    show_details_on_fail: bool = True,
) -> TestRunResult:
    """Запустить один тест-кейс через subprocess и вернуть результат."""
    try:
        completed, elapsed_time, memory_mb, monitor_error = run_process(
            executor_file=executor_file,
            input_data=input_data,
            measure_child_memory=measure_child_memory,
            poll_interval=poll_interval,
        )

        if monitor_error:
            print(f"Warning: child memory monitor failed: {monitor_error}")

        if completed is None:
            timeout_msg = monitor_error or "Process did not start or timed out."
            if show_details_on_fail:
                print(f"\n⏱️  Тест №{test_case.index} — {timeout_msg}")
            log_error(file)
            return TestRunResult(False, elapsed_time, memory_mb, timeout_msg)

        if completed.returncode != 0:
            if show_details_on_fail:
                print(f"\n💀 Тест №{test_case.index} провален")
                print(f"\nError message:\n{completed.stderr}\n")
            log_error(file)
            return TestRunResult(False, elapsed_time, memory_mb, completed.stderr.strip())

        result = completed.stdout.strip().splitlines()
        if result != test_case.expected_lines:
            if show_details_on_fail:
                print_test_mismatch(
                    test_case.index,
                    test_case.input_lines,
                    test_case.expected_lines,
                    result,
                )
            return TestRunResult(
                False,
                elapsed_time,
                memory_mb,
                f"Wrong answer on test #{test_case.index}",
            )

        return TestRunResult(True, elapsed_time, memory_mb)

    except Exception as error:
        print(f"\n😱 Test#{test_case.index} failed with an unexpected error: {error}")
        print(f"Error type: {type(error).__name__}")
        traceback.print_exc()
        log_error(file)
        return TestRunResult(False, 0.0, 0.0, repr(error))


def load_test_cases(tests_dir: pathlib.Path) -> list[GradeCase]:
    test_numbers = sorted(int(name) for name in os.listdir(tests_dir) if name.isdigit())
    test_cases = []

    for test_number in test_numbers:
        test_file_path = tests_dir / f"{test_number}.clue"
        input_file_path = tests_dir / str(test_number)

        expected_lines, _ = load_text_lines_with_encoding(str(test_file_path))
        input_lines = load_text_lines(str(input_file_path))
        test_cases.append(
            GradeCase(
                index=test_number,
                input_lines=input_lines,
                expected_lines=expected_lines,
            )
        )

    return test_cases


def prepare_execution(script_path: pathlib.Path, exec_file: str) -> tuple[list[str], bool, str]:
    program_lines = load_text_lines(str(script_path))
    solution_syntax = "\n".join(program_lines)
    is_function_only = is_function_only_solution(solution_syntax)
    executor_file = exec_file if is_function_only else str(script_path)
    return program_lines, is_function_only, executor_file


def build_input_data(program_lines: list[str], is_function_only: bool, test_case: GradeCase) -> str:
    if is_function_only:
        return "\n".join(program_lines + test_case.input_lines)
    return "\n".join(test_case.input_lines)


def verify_file(script_file: str, base_dir: pathlib.Path, exec_file: str) -> VerificationResult:
    program_path = base_dir / script_file
    module_folder = os.path.dirname(script_file)
    tests_dir = base_dir / module_folder / "tests"

    if not tests_dir.exists():
        return VerificationResult(
            file=script_file,
            total_tests=0,
            passed_tests=0,
            total_time=0.0,
            avg_time=0.0,
            peak_memory_mb=0.0,
            status="NO TESTS",
        )

    program_lines, is_function_only, executor_file = prepare_execution(program_path, exec_file)
    test_cases = load_test_cases(tests_dir)

    passed_tests = 0
    total_time = 0.0
    peak_memory_mb = 0.0

    for test_case in test_cases:
        input_data = build_input_data(program_lines, is_function_only, test_case)
        test_result = run_test_once(
            file=script_file,
            test_case=test_case,
            executor_file=executor_file,
            input_data=input_data,
            measure_child_memory=MEASURE_CHILD_MEMORY,
            poll_interval=CHILD_MEMORY_POLL_INTERVAL,
            show_details_on_fail=True,
        )

        total_time += test_result.elapsed_time
        peak_memory_mb = max(peak_memory_mb, test_result.memory_mb)

        if test_result.passed:
            passed_tests += 1
        else:
            total_tests = len(test_cases)
            avg_time = total_time / passed_tests if passed_tests else total_time
            return VerificationResult(
                file=script_file,
                total_tests=total_tests,
                passed_tests=passed_tests,
                total_time=total_time,
                avg_time=avg_time,
                peak_memory_mb=peak_memory_mb,
                status="FAILED",
                failed_test_index=test_case.index,
                error_message=test_result.error_message,
            )

    total_tests = len(test_cases)
    avg_time = total_time / total_tests if total_tests else 0.0
    status = "OK" if total_tests else "NO TESTS"

    return VerificationResult(
        file=script_file,
        total_tests=total_tests,
        passed_tests=passed_tests,
        total_time=total_time,
        avg_time=avg_time,
        peak_memory_mb=peak_memory_mb,
        status=status,
    )


def benchmark_file(
    script_file: str,
    base_dir: pathlib.Path,
    exec_file: str,
    repeats: int,
) -> BenchmarkStats | None:
    """Запустить subprocess-бенчмарк для одного файла.

    🟠 УЛУЧШЕНО: устранена двойная верификация — раньше verify_file вызывался полностью,
    потом prepare_execution и load_test_cases вызывались снова. Теперь одна верификация,
    данные переиспользуются.
    """
    program_path = base_dir / script_file
    module_folder = os.path.dirname(script_file)
    tests_dir = base_dir / module_folder / "tests"

    if not tests_dir.exists():
        return None

    program_lines, is_function_only, executor_file = prepare_execution(program_path, exec_file)
    test_cases = load_test_cases(tests_dir)

    # Сначала быстрая верификация одним проходом
    for test_case in test_cases:
        input_data = build_input_data(program_lines, is_function_only, test_case)
        check = run_test_once(
            file=script_file,
            test_case=test_case,
            executor_file=executor_file,
            input_data=input_data,
            measure_child_memory=False,
            show_details_on_fail=True,
        )
        if not check.passed:
            return None

    # Основной бенчмарк
    timings: list[float] = []
    peak_memory_mb = 0.0

    for _ in range(repeats):
        for test_case in test_cases:
            input_data = build_input_data(program_lines, is_function_only, test_case)
            test_result = run_test_once(
                file=script_file,
                test_case=test_case,
                executor_file=executor_file,
                input_data=input_data,
                measure_child_memory=MEASURE_CHILD_MEMORY,
                poll_interval=CHILD_MEMORY_POLL_INTERVAL,
                show_details_on_fail=False,
            )

            if not test_result.passed:
                return None

            timings.append(test_result.elapsed_time)
            peak_memory_mb = max(peak_memory_mb, test_result.memory_mb)

    if not timings:
        return None

    std_dev_time = statistics.stdev(timings) if len(timings) > 1 else 0.0

    return BenchmarkStats(
        file=script_file,
        repeats=repeats,
        total_tests=len(test_cases),
        total_runs=len(timings),
        min_time=min(timings),
        median_time=statistics.median(timings),
        mean_time=statistics.mean(timings),
        max_time=max(timings),
        std_dev_time=std_dev_time,
        peak_memory_mb=peak_memory_mb,
    )


def apply_relative_metrics(stats: list[BenchmarkStats]) -> list[BenchmarkStats]:
    if not stats:
        return stats

    best = min(item.median_time for item in stats)

    for item in stats:
        item.relative_percent = (item.median_time / best) * 100 if best > 0 else 100.0
        delta_percent = item.relative_percent - 100

        if delta_percent <= SIMILAR_THRESHOLD_PERCENT:
            item.verdict = "SIMILAR"
        elif delta_percent <= 15:
            item.verdict = "SLOWER"
        else:
            item.verdict = "MUCH SLOWER"

    return stats


def print_single_result(result: VerificationResult) -> None:
    print(
        f"{result.file}: "
        f"{result.passed_tests}/{result.total_tests} tests, "
        f"total={result.total_time:.4f}s, "
        f"avg={result.avg_time:.4f}s, "
        f"peak_memory={result.peak_memory_mb:.2f} MB, "
        f"status={result.status}"
    )


def _file_col_width(files: list[str], header: str = "File", min_width: int = 20) -> int:
    return max(min_width, len(header), max((len(f) for f in files), default=0)) + 2


def print_verification_table(task_folder: str, results: list[VerificationResult]) -> None:
    fw = _file_col_width([r.file for r in results])
    total_width = fw + 12 + 14 + 14 + 16 + 12 + 10

    print(f"\n📂 {task_folder}")
    print("-" * total_width)
    print(
        f"{'File':{fw}}"
        f"{'Passed':>12}"
        f"{'Total time':>14}"
        f"{'Avg time':>14}"
        f"{'Peak memory':>16}"
        f"{'Status':>12}"
        f"{'Fail test':>10}"
    )
    print("-" * total_width)

    sorted_results = sorted(
        results,
        key=lambda r: (
            r.status != "OK",
            -r.passed_tests,
            r.total_time,
            r.peak_memory_mb,
            r.file,
        ),
    )

    for result in sorted_results:
        fail_test = str(result.failed_test_index) if result.failed_test_index is not None else "-"
        print(
            f"{result.file:{fw}}"
            f"{f'{result.passed_tests}/{result.total_tests}':>12}"
            f"{result.total_time:>14.4f}"
            f"{result.avg_time:>14.4f}"
            f"{result.peak_memory_mb:>16.2f}"
            f"{result.status:>12}"
            f"{fail_test:>10}"
        )


def print_benchmark_table(task_folder: str, results: list[BenchmarkStats]) -> None:
    fw = _file_col_width([r.file for r in results])
    total_width = fw + 8 + 12 * 6 + 12 + 12 + 12

    print(f"\n🚀 Benchmark: {task_folder}")
    print("-" * total_width)
    print(
        f"{'File':{fw}}"
        f"{'Runs':>8}"
        f"{'Min':>12}"
        f"{'Median':>12}"
        f"{'Mean':>12}"
        f"{'Max':>12}"
        f"{'Std dev':>12}"
        f"{'Memory':>12}"
        f"{'Relative':>12}"
        f"{'Verdict':>12}"
    )
    print("-" * total_width)

    sorted_results = sorted(
        results,
        key=lambda r: (r.median_time, r.mean_time, r.peak_memory_mb, r.file),
    )

    for result in sorted_results:
        print(
            f"{result.file:{fw}}"
            f"{result.total_runs:>8}"
            f"{result.min_time:>12.5f}"
            f"{result.median_time:>12.5f}"
            f"{result.mean_time:>12.5f}"
            f"{result.max_time:>12.5f}"
            f"{result.std_dev_time:>12.5f}"
            f"{result.peak_memory_mb:>12.2f}"
            f"{result.relative_percent:>11.1f}%"
            f"{result.verdict:>12}"
        )


def print_microbench_table(task_folder: str, results: list[MicrobenchResult]) -> None:
    fw = _file_col_width([r.file for r in results])
    total_width = fw + 10 + 12 * 5 + 12 + 12

    print(f"\n⚡ Micro-bench (timeit): {task_folder}")
    print("-" * total_width)
    print(
        f"{'File':{fw}}"
        f"{'Repeats':>10}"
        f"{'Min, us':>12}"
        f"{'Median, us':>12}"
        f"{'Mean, us':>12}"
        f"{'Max, us':>12}"
        f"{'Std dev, us':>12}"
        f"{'Relative':>12}"
        f"{'Verdict':>12}"
    )
    print("-" * total_width)

    sorted_results = sorted(
        results,
        key=lambda r: (r.median_time if r.timings else float("inf"), r.file),
    )

    for r in sorted_results:
        if r.error:
            print(f"{r.file:{fw}}{'ERROR':>10}  {r.error.splitlines()[0]}")
            continue
        us = 1_000_000
        print(
            f"{r.file:{fw}}"
            f"{r.repeats:>10}"
            f"{r.min_time * us:>12.2f}"
            f"{r.median_time * us:>12.2f}"
            f"{r.mean_time * us:>12.2f}"
            f"{r.max_time * us:>12.2f}"
            f"{r.std_dev_time * us:>12.2f}"
            f"{r.relative_percent:>11.1f}%"
            f"{r.verdict:>12}"
        )


def ask_benchmark_repeats() -> int:
    print("\nBenchmark load:")
    print("1 - low (5 repeats)")
    print("2 - medium (15 repeats)")
    print("3 - high (50 repeats)")
    print("4 - custom")

    choice = input("Choose load (1/2/3/4): ").strip()
    mapping = {"1": 5, "2": 15, "3": 50}

    if choice in mapping:
        return mapping[choice]

    while True:
        raw = input("Enter repeats count (5-100): ").strip()
        if raw.isdigit() and 5 <= int(raw) <= 100:
            return int(raw)
        print("Please enter integer from 5 to 100.")


def ask_microbench_repeats() -> int:
    print("\nMicro-bench load:")
    print("1 - low (100 repeats)")
    print("2 - medium (500 repeats)")
    print("3 - high (2000 repeats)")
    print("4 - custom")

    choice = input("Choose load (1/2/3/4): ").strip()
    mapping = {"1": 100, "2": 500, "3": 2000}

    if choice in mapping:
        return mapping[choice]

    while True:
        raw = input("Enter repeats count (100-10000): ").strip()
        if raw.isdigit() and 100 <= int(raw) <= 10000:
            return int(raw)
        print("Please enter integer from 100 to 10000.")
