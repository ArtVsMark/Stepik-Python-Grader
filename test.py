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
from dataclasses import dataclass, field
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_SOLUTION_FILE_RE = re.compile(r"task(?:\d+(?:_\d+)?|_\d+)?\.py")

TIMEOUT_SECONDS: int = 10
ENCODING: str = "utf-8"
SIMILAR_THRESHOLD: float = 1.15

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

    Использует isinstance вместо match/case для совместимости с Python 3.14,
    где структурный паттерн-матчинг AST-узлов ведёт себя непредсказуемо.
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

    Ожидаемая структура:
        input_1.txt, input_2.txt, ...
        expected_1.txt, expected_2.txt, ...
    """
    cases: list[TestCase] = []
    dir_path = pathlib.Path(test_dir)

    input_files = sorted(dir_path.glob("input_*.txt"))
    for inp_file in input_files:
        idx_str = inp_file.stem.split("_", 1)[1]
        idx = int(idx_str)
        exp_file = dir_path / f"expected_{idx}.txt"
        if not exp_file.exists():
            continue
        input_lines = load_text_lines(str(inp_file))
        expected_lines = load_text_lines(str(exp_file))
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
# Запуск решения
# ---------------------------------------------------------------------------


def run_solution(
    file_path: str,
    stdin_data: str = "",
    timeout: int = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить Python-файл в subprocess и вернуть результат."""
    try:
        result = subprocess.run(
            [sys.executable, file_path],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding=ENCODING,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "timed_out": False,
            "extra": "",
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Timeout after {timeout}s",
            "returncode": -1,
            "timed_out": True,
            "extra": "",
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

times = timeit.repeat(_run, number={number}, repeat=3)
print(min(times) / {number})
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
            return {"error": result.stderr, "time": None}
        return {"error": None, "time": float(result.stdout.strip())}
    except subprocess.TimeoutExpired:
        return {"error": "Timeout", "time": None}
    finally:
        os.unlink(tmp_path)


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


# ---------------------------------------------------------------------------
# Storage helpers (импортируем из storage.py)
# ---------------------------------------------------------------------------

from storage import load_json_file, save_json_file, save_secrets  # noqa: E402


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
    timeout: int = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить тест-кейсы для одного файла решения.

    Args:
        solution_path: Путь к файлу решения.
        test_dir: Директория с тест-кейсами.
        mode: Режим запуска (script/function/compare/bench).
        verbose: Подробный вывод.
        timeout: Таймаут subprocess в секундах.

    Returns:
        Словарь с результатами: passed, failed, errors, total.
    """
    _validate_mode(mode)

    solution_code = pathlib.Path(solution_path).read_text(encoding=ENCODING)
    is_function_mode = mode == MODE_FUNCTION or (
        mode == MODE_SCRIPT and is_function_only_solution(solution_code)
    )
    is_function_only = is_function_only_solution(solution_code)

    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {"passed": 0, "failed": 0, "errors": 0, "total": 0, "cases": []}

    results: list[dict[str, Any]] = []
    passed = failed = errors = 0

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

        case_result: dict[str, Any] = {
            "index": case.index,
            "status": status,
            "actual": actual_lines,
            "expected": case.expected_lines,
        }
        if verbose and not ok:
            case_result["diff"] = format_diff(actual_lines, case.expected_lines)

        results.append(case_result)

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total": len(test_cases),
        "cases": results,
    }


def run_compare(
    solution_paths: list[str],
    test_dir: str,
    *,
    timeout: int = TIMEOUT_SECONDS,
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
    number: int = 100,
) -> dict[str, Any]:
    """Запустить микробенчмарк для нескольких решений."""
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    timings: dict[str, float] = {}
    for path in solution_paths:
        code = pathlib.Path(path).read_text(encoding=ENCODING)
        stdin_data = build_input_data(code, test_cases[0].input_lines, is_function_mode=False)
        bench_result = run_microbench(code, stdin_data=stdin_data, number=number)
        if bench_result["time"] is not None:
            timings[path] = bench_result["time"]

    return apply_relative_micro(timings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="test",
        description="Тестирование Python-решений со Stepik.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = subparsers.add_parser("run", help="Запустить тесты для одного решения.")
    run_p.add_argument("solution", help="Путь к файлу решения.")
    run_p.add_argument("test_dir", help="Директория с тест-кейсами.")
    run_p.add_argument("--mode", choices=list(VALID_MODES), default=MODE_SCRIPT)
    run_p.add_argument("--verbose", "-v", action="store_true")
    run_p.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)

    # compare
    cmp_p = subparsers.add_parser("compare", help="Сравнить несколько решений.")
    cmp_p.add_argument("solutions", nargs="+", help="Пути к файлам решений.")
    cmp_p.add_argument("test_dir", help="Директория с тест-кейсами.")
    cmp_p.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)

    # bench
    bench_p = subparsers.add_parser("bench", help="Микробенчмарк решений.")
    bench_p.add_argument("solutions", nargs="+", help="Пути к файлам решений.")
    bench_p.add_argument("test_dir", help="Директория с тест-кейсами.")
    bench_p.add_argument("--number", type=int, default=100)

    return parser.parse_args(argv)


def cli_main(argv: list[str] | None = None) -> None:
    """Точка входа CLI."""
    args = _parse_args(argv)

    if args.command == "run":
        result = run_tests(
            args.solution,
            args.test_dir,
            mode=args.mode,
            verbose=args.verbose,
            timeout=args.timeout,
        )
        total = result["total"]
        passed = result["passed"]
        failed = result["failed"]
        errors = result["errors"]
        print(f"Результат: {passed}/{total} пройдено, {failed} провалено, {errors} ошибок.")
        for case in result.get("cases", []):
            mark = PASS_MARK if case["status"] == "pass" else FAIL_MARK
            color = "pass" if case["status"] == "pass" else "fail"
            print(colorize(f"  {mark} Тест {case['index']}", color))
            if "diff" in case:
                print(case["diff"])

    elif args.command == "compare":
        results = run_compare(args.solutions, args.test_dir, timeout=args.timeout)
        for path, result in results.items():
            total = result["total"]
            passed = result["passed"]
            print(f"{path}: {passed}/{total}")

    elif args.command == "bench":
        results = run_bench_mode(args.solutions, args.test_dir, number=args.number)
        for path, data in sorted(results.items(), key=lambda x: x[1]["time"]):
            print(f"{path}: {data['time']:.6f}s (x{data['relative']:.2f})")


if __name__ == "__main__":
    cli_main()
