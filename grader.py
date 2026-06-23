from __future__ import annotations

import ast
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import psutil
import requests

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_SOLUTION_FILE_RE = re.compile(r"task(?:\d+(?:_\d+)?|_\d+)?\.py")

TIMEOUT_SECONDS: float = 10.0
ENCODING: str = "utf-8"
SIMILAR_THRESHOLD: float = 1.15
MUCH_SLOWER_THRESHOLD: float = 1.50
MEASURE_CHILD_MEMORY: bool = False
MICROBENCH_MAX_CASES: int = 5

# ---------------------------------------------------------------------------
# Вспомогательные типы
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    index: int
    input_lines: list[str]
    expected_lines: list[str]


def _is_safe_constant(node: ast.expr) -> bool:
    """Вернуть True, если узел — безопасное константное выражение без вызовов.

    Рекурсивно проверяет AST-узел: принимает литералы (Constant), арифметику
    из констант (BinOp, UnaryOp) и вложенные контейнеры (List/Tuple/Set/Dict).
    Отклоняет любые вызовы (Call), обращения к атрибутам (Attribute) и Name.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(
        node.op, (ast.USub, ast.UAdd, ast.Invert)
    ):
        return _is_safe_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_constant(node.left) and _is_safe_constant(node.right)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_safe_constant(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_safe_constant(k) for k in node.keys if k is not None
        ) and all(_is_safe_constant(v) for v in node.values)
    return False


def is_function_only_solution(file_content: str) -> bool:
    """Вернуть True, если файл содержит только определения функций (без точки входа).

    При SyntaxError в исходнике возвращает False — файл будет запущен как скрипт
    напрямую, и ошибка будет поймана subprocess'ом с нормальным выводом в stderr.
    """
    try:
        tree = ast.parse(file_content)
    except SyntaxError:
        return False

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
            if not _is_safe_constant(node.value):
                return False

        if isinstance(node, ast.AnnAssign):
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


def collect_grouped_files(directory: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                rel_folder = os.path.relpath(root, directory)
                grouped[rel_folder].append(os.path.join(root, file_name))

    return dict(grouped)


def resolve_input_path(raw_path: str, base_dir: pathlib.Path) -> pathlib.Path:
    """Вернуть абсолютный путь к файлу входных данных.

    Если raw_path абсолютный — вернуть как есть.
    Если относительный — объединить с base_dir.
    """
    p = pathlib.Path(raw_path.strip())
    if p.is_absolute():
        return p
    return base_dir / p


def load_text_lines(file_path: str) -> list[str]:
    """Загрузить текстовый файл и вернуть список строк без завершающих переносов."""
    with open(file_path, encoding=ENCODING) as f:
        return [line.rstrip("\n") for line in f]


def load_text_lines_with_encoding(file_path: str) -> tuple[list[str], str | None]:
    """Загрузить текстовый файл и вернуть (строки, кодировка)."""
    import chardet

    with open(file_path, "rb") as f:
        raw = f.read()
    detected = chardet.detect(raw)
    encoding = detected.get("encoding")
    lines = raw.decode(encoding or ENCODING).splitlines()
    lines = [line.rstrip("\n") for line in lines]
    return lines, encoding


def load_test_cases(test_dir: str) -> list[TestCase]:
    """Загрузить тест-кейсы из директории.

    Поддерживаются два формата:

    Формат 1 — at_first.py (legacy):
        tests/1        — входные данные теста №1 (stdin)
        tests/1.clue   — ожидаемый вывод теста №1
        tests/2, tests/2.clue, ...

    Формат 2 — новый (используется в тестах):
        tests/input_1.txt    — входные данные теста №1
        tests/expected_1.txt — ожидаемый вывод теста №1
        tests/input_2.txt, tests/expected_2.txt, ...
    """
    cases: list[TestCase] = []
    dir_path = pathlib.Path(test_dir)

    _INPUT_RE = re.compile(r"^input_(\d+)\.txt$")

    for inp_file in dir_path.iterdir():
        # Формат 2: input_{N}.txt / expected_{N}.txt
        m = _INPUT_RE.match(inp_file.name)
        if m:
            idx = int(m.group(1))
            exp_file = dir_path / f"expected_{idx}.txt"
            if not exp_file.exists():
                continue
            input_lines = load_text_lines(str(inp_file))
            expected_lines = load_text_lines(str(exp_file))
            cases.append(TestCase(index=idx, input_lines=input_lines, expected_lines=expected_lines))
            continue

        # Формат 1: числовые файлы без расширения + .clue
        if inp_file.suffix or not inp_file.stem.isdigit():
            continue
        clue_file = dir_path / f"{inp_file.stem}.clue"
        if not clue_file.exists():
            continue
        idx = int(inp_file.stem)
        input_lines = load_text_lines(str(inp_file))
        expected_lines = load_text_lines(str(clue_file))
        cases.append(TestCase(index=idx, input_lines=input_lines, expected_lines=expected_lines))

    return sorted(cases, key=lambda c: c.index)


def build_input_data(
    source_code: str,
    input_lines: list[str],
    is_function_mode: bool,
) -> str:
    """Собрать строку stdin для запуска решения.

    В function-режиме: source_code + "\\n" + joined_input.
    В script-режиме: только joined_input.
    """
    joined = "\n".join(input_lines)
    if is_function_mode:
        return source_code + "\n" + joined
    return joined


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

_CYRILLIC_YO_MAP = str.maketrans("ЁёЙй", "ЕеИи")
_SLUG_MAX_LEN = 50


def slugify(text: str, max_len: int = _SLUG_MAX_LEN) -> str:
    """Преобразовать произвольный текст в slug для использования в именах файлов/URL.

    Шаги:
        1. Нормализовать ё→е, й→и.
        2. Транслитерировать кириллицу в латиницу через unidecode.
        3. Привести к нижнему регистру.
        4. Заменить не-алфавитно-цифровые символы на дефис.
        5. Убрать ведущие/завершающие дефисы.
        6. Обрезать до max_len.
    """
    from unidecode import unidecode

    text = text.translate(_CYRILLIC_YO_MAP)
    text = unidecode(text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len]


# ---------------------------------------------------------------------------
# Запуск решения с замером времени и памяти
# ---------------------------------------------------------------------------


def _get_peak_memory_mb(proc: psutil.Process) -> float:
    """Вернуть пиковый RSS процесса в мегабайтах."""
    try:
        return proc.memory_info().rss / 1024 / 1024
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def run_solution(
    file_path: str,
    stdin_data: str = "",
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить Python-файл в subprocess и вернуть результат с временем и памятью."""
    t_start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [sys.executable, file_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=ENCODING,
        )
        peak_mb = 0.0
        if MEASURE_CHILD_MEMORY:
            try:
                ps_proc = psutil.Process(proc.pid)
            except psutil.NoSuchProcess:
                ps_proc = None
        else:
            ps_proc = psutil.Process(os.getpid())

        try:
            stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            elapsed = time.perf_counter() - t_start
            return {
                "stdout": "",
                "stderr": f"Timeout after {timeout}s",
                "returncode": -1,
                "timed_out": True,
                "extra": "",
                "elapsed": elapsed,
                "peak_memory_mb": 0.0,
            }

        elapsed = time.perf_counter() - t_start
        if ps_proc is not None:
            peak_mb = _get_peak_memory_mb(ps_proc)

        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
            "timed_out": False,
            "extra": "",
            "elapsed": elapsed,
            "peak_memory_mb": peak_mb,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "stdout": "",
            "stderr": str(exc),
            "returncode": -1,
            "timed_out": False,
            "extra": "",
            "elapsed": 0.0,
            "peak_memory_mb": 0.0,
        }


# ---------------------------------------------------------------------------
# Точка входа executor
# ---------------------------------------------------------------------------


def main_executor(code: str, stdin_data: str = "") -> None:
    """Выполнить код и вывести результат в stdout."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding=ENCODING
    ) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = run_solution(tmp_path, stdin_data=stdin_data)
        if result["stdout"]:
            print(result["stdout"], end="")
        if result["returncode"] != 0:
            sys.exit(result["returncode"])
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Сравнение решений
# ---------------------------------------------------------------------------


def compare_outputs(
    actual: list[str],
    expected: list[str],
) -> bool:
    """Сравнить фактический и ожидаемый вывод построчно."""
    return actual == expected


# ---------------------------------------------------------------------------
# Форматирование результатов
# ---------------------------------------------------------------------------

PASS_MARK = "✓"
FAIL_MARK = "✗"
SKIP_MARK = "–"

_STATUS_COLORS = {
    "pass": "\033[32m",
    "fail": "\033[31m",
    "skip": "\033[33m",
    "reset": "\033[0m",
}


def colorize(text: str, status: str) -> str:
    """Обернуть текст в ANSI-цвет по статусу (pass/fail/skip)."""
    color = _STATUS_COLORS.get(status, "")
    reset = _STATUS_COLORS["reset"]
    return f"{color}{text}{reset}"


def format_diff(actual: list[str], expected: list[str]) -> str:
    """Вернуть строку с построчным diff actual vs expected."""
    lines = []
    max_len = max(len(actual), len(expected))
    for i in range(max_len):
        a = actual[i] if i < len(actual) else "<missing>"
        e = expected[i] if i < len(expected) else "<missing>"
        mark = "=" if a == e else "≠"
        lines.append(f"  [{i+1}] {mark}  got: {a!r}  exp: {e!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Microbench
# ---------------------------------------------------------------------------


def run_microbench(
    code: str,
    stdin_data: str = "",
    number: int = 100,
) -> dict[str, Any]:
    """Запустить микробенчмарк кода через timeit в subprocess."""
    import tempfile

    wrapper = f"""
import timeit
import sys

_code = {code!r}
_stdin = {stdin_data!r}

def _run():
    import subprocess, sys
    subprocess.run(
        [sys.executable, "-c", _code],
        input=_stdin,
        capture_output=True,
        text=True,
    )

# repeat=5 для получения статистики
times = timeit.repeat(_run, number={number}, repeat=5)
# Выводим все замеры через пробел (каждый — суммарное время за number итераций)
print(" ".join(str(t / {number}) for t in times))
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding=ENCODING
    ) as tmp:
        tmp.write(wrapper)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS * 10,
            encoding=ENCODING,
        )
        if result.returncode != 0:
            return {"error": result.stderr, "times": None}
        raw = [float(x) for x in result.stdout.strip().split()]
        return {"error": None, "times": raw}
    except subprocess.TimeoutExpired:
        return {"error": "Timeout", "times": None}
    finally:
        os.unlink(tmp_path)


def _micro_stats(times: list[float]) -> dict[str, float]:
    """Вычислить статистику по списку замеров (в секундах), вернуть в секундах."""
    return {
        "min": min(times),
        "median": statistics.median(times),
        "mean": statistics.mean(times),
        "max": max(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
    }


def apply_relative_micro(
    timings: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Вернуть словарь с абсолютными и относительными временами.

    relative = time / min_time
    """
    if not timings:
        return {}
    min_time = min(timings.values())
    return {
        name: {"time": t, "relative": t / min_time}
        for name, t in timings.items()
    }


def _verdict(relative: float) -> str:
    """Вернуть вердикт по относительному времени."""
    if relative <= SIMILAR_THRESHOLD:
        return "SIMILAR"
    if relative <= MUCH_SLOWER_THRESHOLD:
        return "SLOWER"
    return "MUCH SLOWER"


# ---------------------------------------------------------------------------
# Stepik client helpers
# ---------------------------------------------------------------------------


def _get_stepik_token(client_id: str, client_secret: str) -> str:
    """Получить OAuth2-токен Stepik."""
    resp = requests.post(
        "https://stepik.org/oauth2/token/",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _stepik_get(
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Выполнить GET-запрос к Stepik API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Режимы работы (MODE_*)
# ---------------------------------------------------------------------------

MODE_SCRIPT = "script"
MODE_FUNCTION = "function"
MODE_COMPARE = "compare"
MODE_BENCH = "bench"

VALID_MODES = {MODE_SCRIPT, MODE_FUNCTION, MODE_COMPARE, MODE_BENCH}


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"Неизвестный режим: {mode!r}. Допустимые: {VALID_MODES}")


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------


def run_tests(
    solution_path: str,
    test_dir: str,
    mode: str = MODE_SCRIPT,
    *,
    verbose: bool = False,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить тест-кейсы для одного файла решения.

    Args:
        solution_path: Путь к файлу решения.
        test_dir: Директория с тест-кейсами.
        mode: Режим запуска (script/function/compare/bench).
        verbose: Подробный вывод.
        timeout: Таймаут subprocess в секундах.

    Returns:
        Словарь с результатами: passed, failed, errors, total,
        total_time, avg_time, peak_memory_mb.
    """
    _validate_mode(mode)

    solution_code = pathlib.Path(solution_path).read_text(encoding=ENCODING)
    is_function_only = is_function_only_solution(solution_code)

    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "cases": [], "total_time": 0.0, "avg_time": 0.0,
            "peak_memory_mb": 0.0, "first_fail": "-",
        }

    results: list[dict[str, Any]] = []
    passed = failed = errors = 0
    total_time = 0.0
    peak_memory_mb = 0.0
    first_fail: str | int = "-"

    for case in test_cases:
        stdin_data = build_input_data(
            solution_code,
            case.input_lines,
            is_function_mode=is_function_only,
        )

        run_result = run_solution(
            solution_path,
            stdin_data=stdin_data,
            timeout=timeout,
        )

        total_time += run_result["elapsed"]
        if run_result["peak_memory_mb"] > peak_memory_mb:
            peak_memory_mb = run_result["peak_memory_mb"]

        actual_lines = run_result["stdout"].splitlines()
        ok = compare_outputs(actual_lines, case.expected_lines)

        if run_result["timed_out"] or run_result["returncode"] != 0:
            errors += 1
            status = "error"
        elif ok:
            passed += 1
            status = "pass"
        else:
            failed += 1
            status = "fail"

        if status != "pass" and first_fail == "-":
            first_fail = case.index

        case_result: dict[str, Any] = {
            "index": case.index,
            "status": status,
            "actual": actual_lines,
            "expected": case.expected_lines,
            "elapsed": run_result["elapsed"],
        }
        if verbose and not ok:
            case_result["diff"] = format_diff(actual_lines, case.expected_lines)

        results.append(case_result)

    avg_time = total_time / len(test_cases) if test_cases else 0.0

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": len(test_cases),
        "cases": results,
        "total_time": total_time,
        "avg_time": avg_time,
        "peak_memory_mb": peak_memory_mb,
        "first_fail": first_fail,
    }


def run_compare(
    solution_paths: list[str],
    test_dir: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Сравнить несколько решений на одних тест-кейсах."""
    return {
        path: run_tests(path, test_dir, mode=MODE_SCRIPT, timeout=timeout)
        for path in solution_paths
    }


def run_bench_mode(
    solution_paths: list[str],
    test_dir: str,
    *,
    number: int = 15,
) -> dict[str, Any]:
    """Запустить subprocess-бенчмарк (несколько прогонов) для нескольких решений."""
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    results: dict[str, dict[str, Any]] = {}
    for path in solution_paths:
        code = pathlib.Path(path).read_text(encoding=ENCODING)
        is_fn = is_function_only_solution(code)
        stdin_data = build_input_data(code, test_cases[0].input_lines, is_function_mode=is_fn)

        times: list[float] = []
        peak_mb = 0.0
        for _ in range(number):
            r = run_solution(path, stdin_data=stdin_data)
            times.append(r["elapsed"])
            if r["peak_memory_mb"] > peak_mb:
                peak_mb = r["peak_memory_mb"]

        stats = _micro_stats(times)
        stats["peak_memory_mb"] = peak_mb
        stats["runs"] = number
        results[path] = stats

    # Добавляем relative по median
    if results:
        min_median = min(v["median"] for v in results.values())
        for v in results.values():
            v["relative"] = v["median"] / min_median if min_median > 0 else 1.0
            v["verdict"] = _verdict(v["relative"])

    return results


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------

_SEP = "-" * 68


def _interactive_menu() -> None:
    """Интерактивный режим при запуске без аргументов."""
    mem_mode = "child process (honest, slower)" if MEASURE_CHILD_MEMORY else "parent process (fast, rough)"
    print("Choose mode:")
    print("  1 - test single file")
    print("  2 - compare all solutions in folder")
    print("  3 - benchmark passed solutions")
    print("  4 - microbench (timeit, any solution type)")
    print(f"Memory mode: {mem_mode}")
    print(f"Subprocess timeout: {TIMEOUT_SECONDS}s per test")

    mode_input = input("Enter mode (1/2/3/4): ").strip()

    # ------------------------------------------------------------------
    # Режим 1 — проверка одного файла
    # ------------------------------------------------------------------
    if mode_input == "1":
        solution = input("Enter path to solution file (relative or absolute): ").strip()
        test_dir = input("Enter path to tests directory: ").strip()
        result = run_tests(solution, test_dir, verbose=True)

        total = result["total"]
        passed = result["passed"]
        failed = result["failed"]
        errors = result["errors"]
        status = "OK" if failed == 0 and errors == 0 else "FAIL"
        total_t = result["total_time"]
        avg_t = result["avg_time"]
        mem = result["peak_memory_mb"]

        print(
            f"\n{solution}: {passed}/{total} tests, "
            f"total={total_t:.4f}s, avg={avg_t:.4f}s, "
            f"peak_memory={mem:.2f} MB, status={status}"
        )
        for case in result.get("cases", []):
            mark = PASS_MARK if case["status"] == "pass" else FAIL_MARK
            color = "pass" if case["status"] == "pass" else "fail"
            print(colorize(f"  {mark} Test {case['index']}", color))
            if "diff" in case:
                print(case["diff"])

    # ------------------------------------------------------------------
    # Режим 2 — сравнение всех решений в папке
    # ------------------------------------------------------------------
    elif mode_input == "2":
        directory = input("Enter path to folder with solutions: ").strip()
        grouped = collect_grouped_files(directory)
        if not grouped:
            print("No solution files found.")
            return

        col_file = 28
        for folder, paths in sorted(grouped.items()):
            test_dir = os.path.join(directory, folder, "tests")
            if not os.path.isdir(test_dir):
                print(f"\n📂 {folder}  — tests/ not found, skipping")
                continue

            print(f"\n📂 {folder}")
            print(_SEP)
            print(
                f"{'File':<{col_file}} {'Passed':>6}  "
                f"{'Total time':>10}  {'Avg time':>9}  "
                f"{'Peak memory':>11}  {'Status':>6}  {'Fail test':>9}"
            )
            print(_SEP)

            for path in sorted(paths):
                result = run_tests(path, test_dir)
                total = result["total"]
                passed = result["passed"]
                status = "OK" if passed == total and total > 0 else "FAIL"
                rel = os.path.relpath(path, directory)
                total_t = result["total_time"]
                avg_t = result["avg_time"]
                mem = result["peak_memory_mb"]
                first_fail = result["first_fail"]

                print(
                    f"{rel:<{col_file}} {passed:>3}/{total:<3}  "
                    f"{total_t:>10.4f}  {avg_t:>9.4f}  "
                    f"{mem:>9.2f} MB  {status:>6}  {str(first_fail):>9}"
                )

    # ------------------------------------------------------------------
    # Режим 3 — subprocess-бенчмарк прошедших решений
    # ------------------------------------------------------------------
    elif mode_input == "3":
        directory = input("Enter path to folder with solutions: ").strip()
        repeat_map = {"1": 5, "2": 15, "3": 50}
        print("Repeats: 1=low(5)  2=medium(15)  3=high(50)  4=custom")
        repeat_choice = input("Choose (1/2/3/4): ").strip()
        if repeat_choice == "4":
            number = int(input("Enter number of repeats (5-100): ").strip())
        else:
            number = repeat_map.get(repeat_choice, 15)

        grouped = collect_grouped_files(directory)
        for folder, paths in sorted(grouped.items()):
            test_dir = os.path.join(directory, folder, "tests")
            if not os.path.isdir(test_dir):
                continue

            # Проходит только то, что прошло все тесты (кешируем результат)
            passed_paths = []
            for p in sorted(paths):
                r = run_tests(p, test_dir)
                if r["failed"] == 0 and r["errors"] == 0 and r["total"] > 0:
                    passed_paths.append(p)

            if not passed_paths:
                continue

            print(f"\n🚀 Benchmark: {folder}")
            bench = run_bench_mode(passed_paths, test_dir, number=number)
            if not bench:
                print("  No results.")
                continue

            col = 28
            print(_SEP)
            print(
                f"{'File':<{col}} {'Runs':>4}  "
                f"{'Min':>7}  {'Median':>7}  {'Mean':>7}  {'Max':>7}  "
                f"{'Std dev':>7}  {'Memory':>9}  {'Relative':>8}  {'Verdict'}"
            )
            print(_SEP)
            for path, data in sorted(bench.items(), key=lambda x: x[1]["median"]):
                rel_path = os.path.relpath(path, directory)
                print(
                    f"{rel_path:<{col}} {data['runs']:>4}  "
                    f"{data['min']:>7.4f}  {data['median']:>7.4f}  "
                    f"{data['mean']:>7.4f}  {data['max']:>7.4f}  "
                    f"{data['stdev']:>7.4f}  "
                    f"{data['peak_memory_mb']:>7.2f} MB  "
                    f"{data['relative']*100:>7.1f}%  {data['verdict']}"
                )

    # ------------------------------------------------------------------
    # Режим 4 — microbench (timeit)
    # ------------------------------------------------------------------
    elif mode_input == "4":
        solution = input("Enter path to solution file: ").strip()
        test_dir = input("Enter path to tests directory: ").strip()
        calls_map = {"1": 500, "2": 1000, "3": 5000, "4": 50000, "5": 100000}
        print("Calls: 1=fast(500)  2=normal(1000)  3=thorough(5000)  4=deep(50000)  5=hard(100000)  6=custom")
        calls_choice = input("Choose (1-6): ").strip()
        if calls_choice == "6":
            number = int(input("Enter number of calls (100-500000): ").strip())
        else:
            number = calls_map.get(calls_choice, 1000)

        code = pathlib.Path(solution).read_text(encoding=ENCODING)
        test_cases = load_test_cases(test_dir)
        if not test_cases:
            print("No test cases found.")
            return

        # Ограничиваем число кейсов для стабильного std-dev
        cases_to_bench = test_cases[:MICROBENCH_MAX_CASES]

        all_times: list[float] = []
        for case in cases_to_bench:
            stdin_data = build_input_data(
                code, case.input_lines,
                is_function_mode=is_function_only_solution(code),
            )
            bench = run_microbench(code, stdin_data=stdin_data, number=number)
            if bench["error"]:
                print(f"Error on test {case.index}: {bench['error']}")
                return
            all_times.extend(bench["times"])

        stats = _micro_stats(all_times)
        to_us = 1_000_000

        print(f"\n⚡ Micro-bench (timeit): {solution}")
        print(_SEP)
        print(
            f"{'File':<28} {'Repeats':>7}  "
            f"{'Min, us':>8}  {'Median, us':>10}  {'Mean, us':>9}  "
            f"{'Max, us':>8}  {'Std dev, us':>11}  {'Relative':>8}  {'Verdict'}"
        )
        print(_SEP)
        # Для одного файла relative = 100 %
        rel_name = os.path.relpath(solution)
        print(
            f"{rel_name:<28} {number:>7}  "
            f"{stats['min']*to_us:>8.2f}  "
            f"{stats['median']*to_us:>10.2f}  "
            f"{stats['mean']*to_us:>9.2f}  "
            f"{stats['max']*to_us:>8.2f}  "
            f"{stats['stdev']*to_us:>11.2f}  "
            f"{'100.0%':>8}  SIMILAR"
        )

    else:
        print(f"Unknown mode: {mode_input!r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    _interactive_menu()
