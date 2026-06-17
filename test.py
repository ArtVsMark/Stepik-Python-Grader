import ast
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from threading import Thread
from typing import List

import chardet
import psutil

MEASURE_CHILD_MEMORY = False
CHILD_MEMORY_POLL_INTERVAL = 0.01


@dataclass
class TestRunResult:
    passed: bool
    elapsed_time: float
    memory_mb: float
    error_message: str = ""


@dataclass
class FileBenchmarkResult:
    file: str
    total_tests: int
    passed_tests: int
    total_time: float
    avg_time: float
    peak_memory_mb: float
    status: str


def is_function_only_solution(file_content: str) -> bool:
    tree = ast.parse(file_content)
    allowed_nodes = (
        ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom,
        ast.Assign, ast.AnnAssign, ast.Expr, ast.Pass,
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


def load_test_file(
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
    if path.is_absolute():
        return path
    return (root_dir / path).resolve()


def run_test(
    file: str, test_index: int, executor_file: str, input_data: str,
    correct: List[str], python_version: str, test_data: List[str],
    measure_child_memory: bool = False, poll_interval: float = 0.01,
) -> "TestRunResult":
    parent_process = psutil.Process(os.getpid())
    start_time = time.perf_counter()
    try:
        if not measure_child_memory:
            completed_process = subprocess.run(
                [python_version, executor_file],
                input=input_data, capture_output=True, text=True, encoding="utf-8", check=True,
            )
            result = completed_process.stdout.strip().splitlines()
            if result != correct:
                print(f"Test#{test_index} Input:\n" + "\n".join(test_data))
                print(f"Test#{test_index} Expected Output:\n" + "\n".join(correct))
                print(f"Test#{test_index} Actual Output:\n" + "\n".join(result))
            assert result == correct, (
                f"Test#{test_index}\n{'-' * 69}\n"
                f"expect:{repr(correct)}\nresult:{repr(result)}\n"
            )
            elapsed_time = time.perf_counter() - start_time
            memory_mb = parent_process.memory_info().rss / 1024 / 1024
            return TestRunResult(True, elapsed_time, memory_mb)

        proc = subprocess.Popen(
            [python_version, executor_file],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        peak_rss = 0
        monitor_error = None

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
                monitor_error = error

        monitor_thread = Thread(target=monitor_memory, daemon=True)
        monitor_thread.start()
        stdout_data, stderr_data = proc.communicate(input=input_data)
        monitor_thread.join(timeout=1)
        if monitor_error is not None:
            print(f"Warning: child memory monitor failed: {monitor_error}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, [python_version, executor_file],
                output=stdout_data, stderr=stderr_data,
            )
        result = stdout_data.strip().splitlines()
        if result != correct:
            print(f"Test#{test_index} Input:\n" + "\n".join(test_data))
            print(f"Test#{test_index} Expected Output:\n" + "\n".join(correct))
            print(f"Test#{test_index} Actual Output:\n" + "\n".join(result))
        assert result == correct, (
            f"Test#{test_index}\n{'-' * 69}\n"
            f"expect:{repr(correct)}\nresult:{repr(result)}\n"
        )
        elapsed_time = time.perf_counter() - start_time
        memory_mb = peak_rss / 1024 / 1024
        return TestRunResult(True, elapsed_time, memory_mb)

    except subprocess.CalledProcessError as e:
        print(f"\n 💀 💀 💀 Тест №{test_index} провален: {e}")
        print(f"\n\tError message: {e.stderr}\n")
        log_error(file)
        elapsed_time = time.perf_counter() - start_time
        memory_mb = parent_process.memory_info().rss / 1024 / 1024
        return TestRunResult(False, elapsed_time, memory_mb, str(e))

    except Exception as e:
        print(f"\n 😱 😱 😱 Test#{test_index} failed with an unexpected error: {e}")
        print(f"Error type: {type(e).__name__}")
        traceback.print_exc()
        log_error(file)
        elapsed_time = time.perf_counter() - start_time
        memory_mb = parent_process.memory_info().rss / 1024 / 1024
        return TestRunResult(False, elapsed_time, memory_mb, repr(e))


def benchmark_file(script_file: str, root_dir: pathlib.Path, executor: str) -> "FileBenchmarkResult":
    program_path = root_dir / script_file
    module_folder, _ = os.path.split(script_file)
    tests_dir = root_dir / module_folder / "tests"
    if not tests_dir.exists():
        return FileBenchmarkResult(
            file=script_file, total_tests=0, passed_tests=0,
            total_time=0.0, avg_time=0.0, peak_memory_mb=0.0, status="NO TESTS",
        )
    program = load_test_file(str(program_path))
    solution_syntax = "\n".join(program)
    is_function_only = is_function_only_solution(solution_syntax)
    python_version = get_python_cmd()
    test_numbers = sorted(int(name) for name in os.listdir(tests_dir) if name.isdigit())
    passed_tests = 0
    total_time = 0.0
    peak_memory_mb = 0.0
    for test_number in test_numbers:
        test_file_path = tests_dir / f"{test_number}.clue"
        input_file_path = tests_dir / str(test_number)
        correct, _ = load_test_file(str(test_file_path), return_encoding=True)
        test_data = load_test_file(str(input_file_path))
        input_data = (
            "\n".join(program + test_data) if is_function_only else "\n".join(test_data)
        )
        executor_file = executor if is_function_only else str(program_path)
        test_result = run_test(
            file=script_file, test_index=test_number, executor_file=executor_file,
            input_data=input_data, correct=correct, python_version=python_version,
            test_data=test_data, measure_child_memory=MEASURE_CHILD_MEMORY,
            poll_interval=CHILD_MEMORY_POLL_INTERVAL,
        )
        total_time += test_result.elapsed_time
        peak_memory_mb = max(peak_memory_mb, test_result.memory_mb)
        if test_result.passed:
            passed_tests += 1
        else:
            break
    total_tests = len(test_numbers)
    avg_time = total_time / total_tests if total_tests else 0.0
    status = "OK" if passed_tests == total_tests and total_tests else "FAILED"
    return FileBenchmarkResult(
        file=script_file, total_tests=total_tests, passed_tests=passed_tests,
        total_time=total_time, avg_time=avg_time, peak_memory_mb=peak_memory_mb, status=status,
    )


def print_single_result(result: "FileBenchmarkResult") -> None:
    print(
        f"{result.file}: "
        f"{result.passed_tests}/{result.total_tests} tests, "
        f"total={result.total_time:.4f}s, avg={result.avg_time:.4f}s, "
        f"peak_memory={result.peak_memory_mb:.2f} MB, status={result.status}"
    )


def print_comparison_table(task_folder: str, results: list) -> None:
    print(f"\n📂 {task_folder}")
    print("-" * 110)
    print(
        f"{'File':40}{'Passed':>12}{'Total time':>14}{'Avg time':>14}{'Peak memory':>16}{'Status':>12}"
    )
    print("-" * 110)
    sorted_results = sorted(
        results,
        key=lambda r: (r.status != "OK", -r.passed_tests, r.total_time, r.peak_memory_mb, r.file),
    )
    for result in sorted_results:
        print(
            f"{result.file:40}"
            f"{f'{result.passed_tests}/{result.total_tests}':>12}"
            f"{result.total_time:>14.4f}"
            f"{result.avg_time:>14.4f}"
            f"{result.peak_memory_mb:>16.2f}"
            f"{result.status:>12}"
        )


def run_single_mode(root_dir: pathlib.Path, executor: str) -> None:
    script_file = input("Enter py-file's path from the content root: ").strip()
    result = benchmark_file(script_file, root_dir, executor)
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
    grouped_files = defaultdict(list)
    for abs_path in all_files:
        rel_path = os.path.relpath(abs_path, root_dir)
        task_folder = os.path.dirname(rel_path)
        grouped_files[task_folder].append(rel_path)
    for task_folder, files in sorted(grouped_files.items()):
        results = [benchmark_file(file, root_dir, executor) for file in files]
        print_comparison_table(task_folder, results)


if __name__ == "__main__":
    root_dir = pathlib.Path(__file__).parent.resolve()
    executor = str(root_dir / "executor.py")
    print("Choose mode:")
    print("1 - test single file")
    print("2 - compare all solutions in top-level folder")
    print(
        "Memory mode: "
        + ("child process (more honest, slower)" if MEASURE_CHILD_MEMORY else "parent process (fast, rough)")
    )
    mode = input("Enter mode (1/2): ").strip()
    if mode == "1":
        run_single_mode(root_dir, executor)
    elif mode == "2":
        run_compare_mode(root_dir, executor)
    else:
        print("Unknown mode.")
