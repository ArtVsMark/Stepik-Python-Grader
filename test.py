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
from typing import Optional

import chardet
import psutil


MEASURE_CHILD_MEMORY = False
CHILD_MEMORY_POLL_INTERVAL = 0.01
SIMILAR_THRESHOLD_PERCENT = 5.0


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
    failed_test_index: Optional[int] = None
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
    stdev_time: float
    peak_memory_mb: float
    relative_percent: float = 100.0
    verdict: str = "OK"


@dataclass
class TestCase:
    index: int
    input_lines: list[str]
    expected_lines: list[str]


def is_function_only_solution(file_content: str) -> bool:
    tree = ast.parse(file_content)

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
            if not isinstance(node.value, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict)):
                return False

        if isinstance(node, ast.AnnAssign):
            if node.value is not None and not isinstance(
                node.value, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict)
            ):
                return False

    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body)


def is_solution_file(file_name: str) -> bool:
    return bool(re.fullmatch(r"task(?:\d+)?(?:_\d+)?\.py", file_name))


def find_all_solution_files(directory: str) -> list[str]:
    scripts = []

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                scripts.append(os.path.join(root, file_name))

    return sorted(scripts)


def load_text_lines(
    file_path: str, return_encoding: bool = False
) -> list[str] | tuple[list[str], str | None]:
    with open(file_path, "rb") as binary_file:
        raw_data = binary_file.read()

    file_encoding = chardet.detect(raw_data)["encoding"] or "utf-8"
    file_content = raw_data.decode(file_encoding, errors="replace").strip().splitlines()

    if return_encoding:
        return file_content, file_encoding
    return file_content


def log_error(file: str) -> None:
    with open("./errors.txt", "a", encoding="utf-8") as errors_file:
        print(file, file=errors_file)


def get_python_cmd() -> str:
    return "python3" if sys.platform in {"linux", "linux2", "darwin"} else "python"


def resolve_input_path(root_dir: pathlib.Path, user_input: str) -> pathlib.Path:
    path = pathlib.Path(user_input.strip())
    return path if path.is_absolute() else (root_dir / path).resolve()


def print_test_mismatch(test_index: int, test_data: list[str], correct: list[str], result: list[str]) -> None:
    print(f"Test#{test_index} Input:\n" + "\n".join(test_data))
    print(f"Test#{test_index} Expected Output:\n" + "\n".join(correct))
    print(f"Test#{test_index} Actual Output:\n" + "\n".join(result))


def run_process(
    python_cmd: str,
    executor_file: str,
    input_data: str,
    measure_child_memory: bool = False,
    poll_interval: float = 0.01,
) -> tuple[subprocess.CompletedProcess | None, float, float, str]:
    parent_process = psutil.Process(os.getpid())
    start_time = time.perf_counter()

    if not measure_child_memory:
        completed = subprocess.run(
            [python_cmd, executor_file],
            input=input_data,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        elapsed_time = time.perf_counter() - start_time
        memory_mb = parent_process.memory_info().rss / 1024 / 1024
        return completed, elapsed_time, memory_mb, ""

    proc = subprocess.Popen(
        [python_cmd, executor_file],
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

    stdout_data, stderr_data = proc.communicate(input=input_data)
    monitor_thread.join(timeout=1)

    completed = subprocess.CompletedProcess(
        args=[python_cmd, executor_file],
        returncode=proc.returncode,
        stdout=stdout_data,
        stderr=stderr_data,
    )

    elapsed_time = time.perf_counter() - start_time
    memory_mb = peak_rss / 1024 / 1024
    return completed, elapsed_time, memory_mb, monitor_error


def run_test_once(
    file: str,
    test_case: TestCase,
    executor_file: str,
    input_data: str,
    python_cmd: str,
    measure_child_memory: bool = False,
    poll_interval: float = 0.01,
    show_details_on_fail: bool = True,
) -> TestRunResult:
    try:
        completed, elapsed_time, memory_mb, monitor_error = run_process(
            python_cmd=python_cmd,
            executor_file=executor_file,
            input_data=input_data,
            measure_child_memory=measure_child_memory,
            poll_interval=poll_interval,
        )

        if monitor_error:
            print(f"Warning: child memory monitor failed: {monitor_error}")

        if completed is None:
            return TestRunResult(False, 0.0, 0.0, "Process did not start.")

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


def load_test_cases(tests_dir: pathlib.Path) -> list[TestCase]:
    test_numbers = sorted(int(name) for name in os.listdir(tests_dir) if name.isdigit())
    test_cases = []

    for test_number in test_numbers:
        test_file_path = tests_dir / f"{test_number}.clue"
        input_file_path = tests_dir / str(test_number)

        expected_lines, _ = load_text_lines(str(test_file_path), return_encoding=True)
        input_lines = load_text_lines(str(input_file_path))
        test_cases.append(
            TestCase(
                index=test_number,
                input_lines=input_lines,
                expected_lines=expected_lines,
            )
        )

    return test_cases


def prepare_execution(script_path: pathlib.Path, executor: str) -> tuple[list[str], bool, str]:
    program_lines = load_text_lines(str(script_path))
    solution_syntax = "\n".join(program_lines)
    is_function_only = is_function_only_solution(solution_syntax)
    executor_file = executor if is_function_only else str(script_path)
    return program_lines, is_function_only, executor_file


def build_input_data(program_lines: list[str], is_function_only: bool, test_case: TestCase) -> str:
    if is_function_only:
        return "\n".join(program_lines + test_case.input_lines)
    return "\n".join(test_case.input_lines)


def verify_file(script_file: str, root_dir: pathlib.Path, executor: str) -> VerificationResult:
    program_path = root_dir / script_file
    module_folder = os.path.dirname(script_file)
    tests_dir = root_dir / module_folder / "tests"

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

    program_lines, is_function_only, executor_file = prepare_execution(program_path, executor)
    test_cases = load_test_cases(tests_dir)
    python_cmd = get_python_cmd()

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
            python_cmd=python_cmd,
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
    root_dir: pathlib.Path,
    executor: str,
    repeats: int,
) -> BenchmarkStats | None:
    verification = verify_file(script_file, root_dir, executor)
    if verification.status != "OK":
        return None

    program_path = root_dir / script_file
    module_folder = os.path.dirname(script_file)
    tests_dir = root_dir / module_folder / "tests"

    program_lines, is_function_only, executor_file = prepare_execution(program_path, executor)
    test_cases = load_test_cases(tests_dir)
    python_cmd = get_python_cmd()

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
                python_cmd=python_cmd,
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

    stdev_time = statistics.stdev(timings) if len(timings) > 1 else 0.0

    return BenchmarkStats(
        file=script_file,
        repeats=repeats,
        total_tests=len(test_cases),
        total_runs=len(timings),
        min_time=min(timings),
        median_time=statistics.median(timings),
        mean_time=statistics.mean(timings),
        max_time=max(timings),
        stdev_time=stdev_time,
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


def print_verification_table(task_folder: str, results: list[VerificationResult]) -> None:
    print(f"\n📂 {task_folder}")
    print("-" * 118)
    print(
        f"{'File':40}"
        f"{'Passed':>12}"
        f"{'Total time':>14}"
        f"{'Avg time':>14}"
        f"{'Peak memory':>16}"
        f"{'Status':>12}"
        f"{'Fail test':>10}"
    )
    print("-" * 118)

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
            f"{result.file:40}"
            f"{f'{result.passed_tests}/{result.total_tests}':>12}"
            f"{result.total_time:>14.4f}"
            f"{result.avg_time:>14.4f}"
            f"{result.peak_memory_mb:>16.2f}"
            f"{result.status:>12}"
            f"{fail_test:>10}"
        )


def print_benchmark_table(task_folder: str, results: list[BenchmarkStats]) -> None:
    print(f"\n🚀 Benchmark: {task_folder}")
    print("-" * 154)
    print(
        f"{'File':40}"
        f"{'Runs':>8}"
        f"{'Min':>12}"
        f"{'Median':>12}"
        f"{'Mean':>12}"
        f"{'Max':>12}"
        f"{'Stdev':>12}"
        f"{'Memory':>12}"
        f"{'Relative':>12}"
        f"{'Verdict':>12}"
    )
    print("-" * 154)

    sorted_results = sorted(
        results,
        key=lambda r: (r.median_time, r.mean_time, r.peak_memory_mb, r.file),
    )

    for result in sorted_results:
        print(
            f"{result.file:40}"
            f"{result.total_runs:>8}"
            f"{result.min_time:>12.5f}"
            f"{result.median_time:>12.5f}"
            f"{result.mean_time:>12.5f}"
            f"{result.max_time:>12.5f}"
            f"{result.stdev_time:>12.5f}"
            f"{result.peak_memory_mb:>12.2f}"
            f"{result.relative_percent:>11.1f}%"
            f"{result.verdict:>12}"
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


def run_single_mode(root_dir: pathlib.Path, executor: str) -> None:
    script_file = input("Enter py-file's path from the content root: ").strip()
    result = verify_file(script_file, root_dir, executor)
    print_single_result(result)


def run_compare_mode(root_dir: pathlib.Path, executor: str) -> None:
    folder = input("Enter top-level folder from the content root: ").strip()
    target_dir = resolve_input_path(root_dir, folder)

    if not target_dir.exists():
        print(f"Folder not found: {target_dir}")
        return

    if not target_dir.is_dir():
        print(f"Not a directory: {target_dir}")
        return

    all_files = find_all_solution_files(str(target_dir))
    if not all_files:
        print(f"No solution files found in: {target_dir}")
        print("Expected names like: task.py, task_1.py, task_2.py, task2.py, task2_1.py")
        return

    grouped_files: dict[str, list[str]] = defaultdict(list)
    for abs_path in all_files:
        rel_path = os.path.relpath(abs_path, root_dir)
        task_folder = os.path.dirname(rel_path)
        grouped_files[task_folder].append(rel_path)

    for task_folder, files in sorted(grouped_files.items()):
        results = [verify_file(file, root_dir, executor) for file in files]
        print_verification_table(task_folder, results)


def run_benchmark_mode(root_dir: pathlib.Path, executor: str) -> None:
    folder = input("Enter top-level folder from the content root: ").strip()
    target_dir = resolve_input_path(root_dir, folder)

    if not target_dir.exists():
        print(f"Folder not found: {target_dir}")
        return

    if not target_dir.is_dir():
        print(f"Not a directory: {target_dir}")
        return

    repeats = ask_benchmark_repeats()

    all_files = find_all_solution_files(str(target_dir))
    if not all_files:
        print(f"No solution files found in: {target_dir}")
        return

    grouped_files: dict[str, list[str]] = defaultdict(list)
    for abs_path in all_files:
        rel_path = os.path.relpath(abs_path, root_dir)
        task_folder = os.path.dirname(rel_path)
        grouped_files[task_folder].append(rel_path)

    for task_folder, files in sorted(grouped_files.items()):
        stats = []

        for file in files:
            benchmark_stats = benchmark_file(file, root_dir, executor, repeats)
            if benchmark_stats is not None:
                stats.append(benchmark_stats)

        stats = apply_relative_metrics(stats)

        if stats:
            print_benchmark_table(task_folder, stats)
        else:
            print(f"\n🚀 Benchmark: {task_folder}")
            print("No fully passed solutions available for benchmark.")


if __name__ == "__main__":
    root_dir = pathlib.Path(__file__).parent.resolve()
    executor = str(root_dir / "executor.py")

    print("Choose mode:")
    print("1 - test single file")
    print("2 - compare all solutions in top-level folder")
    print("3 - benchmark passed solutions")
    print(
        "Memory mode: "
        + (
            "child process (more honest, slower)"
            if MEASURE_CHILD_MEMORY
            else "parent process (fast, rough)"
        )
    )

    mode = input("Enter mode (1/2/3): ").strip()

    if mode == "1":
        run_single_mode(root_dir, executor)
    elif mode == "2":
        run_compare_mode(root_dir, executor)
    elif mode == "3":
        run_benchmark_mode(root_dir, executor)
    else:
        print("Unknown mode.")