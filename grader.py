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

_SOLUTION_FILE_RE = re.compile(r"task(?:\d+)?(?:_\d+)?\.py")

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
        task4_1.py, task7_3.py          — стиль из README (номер задачи + номер решения)
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


def format_correctness_row(path: str, base_dir: str, result: dict[str, Any], *, col_file: int) -> str:
    """Сформатировать строку таблицы корректности для режимов 1 и 2."""
    total = result["total"]
    passed = result["passed"]
    status = "OK" if passed == total and result["failed"] == 0 and result["errors"] == 0 and total > 0 else "FAIL"
    rel = os.path.relpath(path, base_dir)
    total_t = result["total_time"]
    avg_t = result["avg_time"]
    mem = result["peak_memory_mb"]
    first_fail = result["first_fail"]
    return (
        f"{rel:<{col_file}} {passed:>3}/{total:<3}  "
        f"{total_t:>10.4f}  {avg_t:>9.4f}  "
        f"{mem:>9.2f} MB  {status:>6}  {str(first_fail):>9}"
    )


def print_correctness_header(*, col_file: int) -> None:
    """Напечатать заголовок таблицы корректности для режимов 1 и 2."""
    print(_SEP)
    print(
        f"{'File':<{col_file}} {'Passed':>6}  "
        f"{'Total time':>10}  {'Avg time':>9}  "
        f"{'Peak memory':>11}  {'Status':>6}  {'Fail test':>9}"
    )
    print(_SEP)


def format_benchmark_row(path: str, base_dir: str, data: dict[str, Any], *, col_file: int) -> str:
    """Сформатировать строку benchmark-таблицы для режимов 3 и 4."""
    rel_path = os.path.relpath(path, base_dir)
    return (
        f"{rel_path:<{col_file}} {data['runs']:>4}  "
        f"{data['min']:>7.4f}  {data['median']:>7.4f}  "
        f"{data['mean']:>7.4f}  {data['max']:>7.4f}  "
        f"{data['stdev']:>7.4f}  "
        f"{data['peak_memory_mb']:>7.2f} MB  "
        f"{data['relative']*100:>7.1f}%  {data['verdict']}"
    )


def print_benchmark_header(*, col_file: int) -> None:
    """Напечатать заголовок benchmark-таблицы для режимов 3 и 4."""
    print(_SEP)
    print(
        f"{'File':<{col_file}} {'Runs':>4}  "
        f"{'Min':>7}  {'Median':>7}  {'Mean':>7}  {'Max':>7}  "
        f"{'Std dev':>7}  {'Memory':>9}  {'Relative':>8}  {'Verdict'}"
    )
    print(_SEP)


def run_microbench_mode(
    solution_paths: list[str],
    test_dir: str,
    *,
    number: int = 1000,
) -> dict[str, Any]:
    """Запустить timeit-microbench для нескольких решений и вернуть сводную статистику."""
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    cases_to_bench = test_cases[:MICROBENCH_MAX_CASES]
    results: dict[str, dict[str, Any]] = {}

    for path in solution_paths:
        code = pathlib.Path(path).read_text(encoding=ENCODING)
        is_fn = is_function_only_solution(code)

        all_times: list[float] = []
        for case in cases_to_bench:
            stdin_data = build_input_data(code, case.input_lines, is_function_mode=is_fn)
            bench = run_microbench(code, stdin_data=stdin_data, number=number)
            if bench["error"]:
                results[path] = {"error": f"test {case.index}: {bench['error']}"}
                break
            all_times.extend(bench["times"])
        else:
            stats = _micro_stats(all_times)
            stats["runs"] = len(all_times)
            stats["peak_memory_mb"] = 0.0
            results[path] = stats

    ok_results = {k: v for k, v in results.items() if not v.get("error")}
    if ok_results:
        min_median = min(v["median"] for v in ok_results.values())
        for v in ok_results.values():
            v["relative"] = v["median"] / min_median if min_median > 0 else 1.0
            v["verdict"] = _verdict(v["relative"])

    return results


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
    _NUM_RE = re.compile(r"^\d+$")
    for inp_file in dir_path.iterdir():
        if _NUM_RE.match(inp_file.name):
            clue_file = dir_path / f"{inp_file.name}.clue"
            if not clue_file.exists():
                continue
            idx = int(inp_file.name)
            input_lines = load_text_lines(str(inp_file))
            expected_lines = load_text_lines(str(clue_file))
            cases.append(TestCase(index=idx, input_lines=input_lines, expected_lines=expected_lines))

    return sorted(cases, key=lambda c: c.index)


def _resolve_test_dir(solution_path: str) -> str:
    """Вернуть путь к директории тест-кейсов для заданного файла решения.

    Стратегия поиска (первый найденный выигрывает):
      1. <parent>/tests/
      2. <parent>/<stem>/  (директория с именем = имени файла без расширения)
      3. <parent>/ (сам родительский каталог, если содержит .clue или input_*.txt)
    """
    p = pathlib.Path(solution_path).resolve()
    parent = p.parent
    stem = p.stem

    candidate_tests = parent / "tests"
    if candidate_tests.is_dir():
        return str(candidate_tests)

    candidate_stem = parent / stem
    if candidate_stem.is_dir():
        return str(candidate_stem)

    for f in parent.iterdir():
        if f.suffix == ".clue" or re.match(r"^input_\d+\.txt$", f.name):
            return str(parent)

    return str(candidate_tests)


def build_input_data(
    source_code: str,
    input_lines: list[str],
    *,
    is_function_mode: bool = False,
) -> str:
    """Собрать stdin-строку для передачи в subprocess.

    В режиме функции (is_function_mode=True) вызываем функцию явно,
    передавая аргументы через stdin-wrapper.
    В режиме скрипта — просто склеиваем input_lines через перевод строки.
    """
    if is_function_mode:
        return "\n".join(input_lines) + "\n"

    return "\n".join(input_lines) + "\n"


def run_single_test(
    solution_path: str,
    case: TestCase,
    *,
    timeout: float = TIMEOUT_SECONDS,
    measure_memory: bool = MEASURE_CHILD_MEMORY,
) -> dict[str, Any]:
    """Запустить одно решение на одном тест-кейсе и вернуть словарь с результатами.

    Возвращаемый словарь содержит:
        passed   (bool)   — прошёл ли тест
        output   (list)   — фактический вывод (строки)
        expected (list)   — ожидаемый вывод (строки)
        diff     (str)    — unified diff при несовпадении
        time     (float)  — время выполнения в секундах
        memory   (float)  — пик памяти в МБ (0 если measure_memory=False)
        error    (str)    — сообщение об ошибке (пустая строка = нет ошибки)
        timed_out (bool)  — истёк ли таймаут
    """
    code = pathlib.Path(solution_path).read_text(encoding=ENCODING)
    is_fn = is_function_only_solution(code)
    stdin_data = build_input_data(code, case.input_lines, is_function_mode=is_fn)

    if is_fn:
        wrapper_code = _build_function_wrapper(code, case.input_lines)
        run_code = wrapper_code
    else:
        run_code = code

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", run_code],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding=ENCODING,
        )
        elapsed = time.perf_counter() - start
        peak_mb = 0.0

        if proc.returncode != 0:
            return {
                "passed": False,
                "output": [],
                "expected": case.expected_lines,
                "diff": "",
                "time": elapsed,
                "memory": peak_mb,
                "error": proc.stderr.strip(),
                "timed_out": False,
            }

        actual_lines = [line.rstrip("\n") for line in proc.stdout.splitlines()]

        passed = actual_lines == case.expected_lines
        diff_str = ""
        if not passed:
            import difflib
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
        }

    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "output": [],
            "expected": case.expected_lines,
            "diff": "",
            "time": timeout,
            "memory": 0.0,
            "error": f"Timeout after {timeout}s",
            "timed_out": True,
        }


def _build_function_wrapper(source_code: str, input_lines: list[str]) -> str:
    """Построить wrapper-код для запуска файла, содержащего только функции.

    Извлекает имя первой публичной функции из исходника и генерирует
    обёртку, которая читает аргументы из stdin и вызывает функцию.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code

    func_name: str | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                func_name = node.name
                break

    if func_name is None:
        return source_code

    args_repr = ", ".join(repr(line) for line in input_lines)
    wrapper = (
        f"{source_code}\n\n"
        f"_args = [{args_repr}]\n"
        f"_result = {func_name}(*_args)\n"
        f"if _result is not None:\n"
        f"    print(_result)\n"
    )
    return wrapper


def run_tests(
    solution_path: str,
    test_dir: str,
    *,
    verbose: bool = False,
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Запустить все тест-кейсы для решения и собрать статистику.

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

        if verbose:
            icon = "✓" if r["passed"] else "✗"
            print(f"  {icon} Test {case.index}", end="")
            if r["error"]:
                print(f" [ERROR: {r['error']}]")
            elif not r["passed"]:
                print(f" [FAIL]")
                if r["diff"]:
                    print(r["diff"])
            else:
                print()

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
) -> dict[str, Any]:
    """Запустить все тест-кейсы в режиме benchmark и собрать статистику времени.

    Возвращаемый словарь:
        runs       (int)   — число запусков
        min/max/mean/median/stdev (float) — статистика времени (секунды)
        peak_memory_mb (float)
        relative   (float) — задаётся снаружи при сравнении
        verdict    (str)   — задаётся снаружи
        error      (str)   — пустая строка если нет ошибок
    """
    test_cases = load_test_cases(test_dir)
    times: list[float] = []
    peak_mb = 0.0

    for case in test_cases:
        r = run_single_test(solution_path, case, timeout=timeout)
        if r["error"] or r["timed_out"]:
            return {"error": r["error"] or "timeout", "runs": 0}
        times.append(r["time"])
        peak_mb = max(peak_mb, r["memory"])

    if not times:
        return {"error": "no test cases", "runs": 0}

    stats = {
        "runs": len(times),
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
        "peak_memory_mb": peak_mb,
        "relative": 1.0,
        "verdict": "SIMILAR",
        "error": "",
    }
    return stats


def run_microbench(
    source_code: str,
    *,
    stdin_data: str = "",
    number: int = 1000,
) -> dict[str, Any]:
    """Запустить timeit-microbenchmark для исходного кода.

    Возвращает словарь с ключами:
        times  (list[float]) — список замеров (в секундах)
        error  (str)         — сообщение об ошибке (пустая строка = успех)
    """
    bench_script = (
        "import timeit as _timeit, sys as _sys\n"
        "_code = '''" + source_code.replace("'''", "\"\"\"") + "'''\n"
        f"_number = {number}\n"
        "_stdin = " + repr(stdin_data) + "\n"
        "import io as _io\n"
        "_sys.stdin = _io.StringIO(_stdin)\n"
        "def _reset_stdin():\n"
        "    _sys.stdin = _io.StringIO(_stdin)\n"
        "_times = _timeit.repeat(\n"
        "    stmt=_code,\n"
        "    setup='_reset_stdin()',\n"
        "    repeat=5,\n"
        f"    number={number},\n"
        "    globals={'_reset_stdin': _reset_stdin}\n"
        ")\n"
        "_per = [t / _number for t in _times]\n"
        "print('\\n'.join(str(t) for t in _per))\n"
    )

    try:
        result = subprocess.run(
            [sys.executable, "-c", bench_script],
            capture_output=True,
            text=True,
            timeout=60,
            encoding=ENCODING,
        )
        if result.returncode != 0:
            return {"times": [], "error": result.stderr.strip()}
        times = [float(line) for line in result.stdout.strip().splitlines() if line.strip()]
        return {"times": times, "error": ""}
    except subprocess.TimeoutExpired:
        return {"times": [], "error": "microbench timeout"}
    except Exception as exc:
        return {"times": [], "error": str(exc)}


def _micro_stats(times: list[float]) -> dict[str, float]:
    """Вычислить статистику по списку замеров времени."""
    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0.0,
    }


def _verdict(relative: float) -> str:
    """Вернуть текстовый вердикт по относительному времени."""
    if relative <= SIMILAR_THRESHOLD:
        return "SIMILAR"
    if relative <= MUCH_SLOWER_THRESHOLD:
        return "SLOWER"
    return "MUCH_SLOWER"


_SEP = "-" * 78


# ---------------------------------------------------------------------------
# Stepik API
# ---------------------------------------------------------------------------


def fetch_stepik_tests(
    lesson_id: int,
    step_position: int,
    *,
    secrets_file: str = "secrets.json",
    config_file: str = "stepik_config.json",
    output_dir: str | None = None,
) -> str:
    """Загрузить тест-кейсы из Stepik API и сохранить в директорию.

    Возвращает путь к созданной директории с тестами.
    Raises RuntimeError при ошибках авторизации или отсутствии тестов.
    """
    import json

    secrets_path = pathlib.Path(secrets_file)
    if not secrets_path.exists():
        raise RuntimeError(f"Secrets file not found: {secrets_file}")

    with open(secrets_path, encoding=ENCODING) as f:
        secrets = json.load(f)

    client_id = secrets.get("client_id")
    client_secret = secrets.get("client_secret")

    if not client_id or not client_secret:
        raise RuntimeError("client_id / client_secret not found in secrets file")

    token_resp = requests.post(
        "https://stepik.org/oauth2/token/",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    lessons_resp = requests.get(
        f"https://stepik.org/api/lessons/{lesson_id}",
        headers=headers,
        timeout=15,
    )
    lessons_resp.raise_for_status()
    steps = lessons_resp.json()["lessons"][0]["steps"]
    if step_position < 1 or step_position > len(steps):
        raise RuntimeError(f"Step position {step_position} out of range (1..{len(steps)})")
    step_id = steps[step_position - 1]

    step_resp = requests.get(
        f"https://stepik.org/api/steps/{step_id}",
        headers=headers,
        timeout=15,
    )
    step_resp.raise_for_status()
    step_data = step_resp.json()["steps"][0]
    block = step_data.get("block", {})
    test_cases_raw = block.get("options", {}).get("code_templates", [])

    if not test_cases_raw:
        samples = block.get("options", {}).get("samples", [])
        test_cases_raw = [{"input": s[0], "output": s[1]} for s in samples]

    if not test_cases_raw:
        raise RuntimeError("No test cases found in step")

    if output_dir is None:
        output_dir = f"tests_lesson{lesson_id}_step{step_position}"

    out_path = pathlib.Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    for idx, tc in enumerate(test_cases_raw, start=1):
        inp = tc.get("input", "")
        out = tc.get("output", "") or tc.get("stdout", "")
        (out_path / f"input_{idx}.txt").write_text(inp, encoding=ENCODING)
        (out_path / f"expected_{idx}.txt").write_text(out, encoding=ENCODING)

    return str(out_path)


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------


def _print_menu() -> None:
    print("\n" + "=" * 50)
    print("  Stepik Python Grader")
    print("=" * 50)
    print("  1. Check one solution")
    print("  2. Check all solutions in folder")
    print("  3. Benchmark solutions in folder")
    print("  4. Micro-benchmark (timeit) for folder")
    print("  5. Fetch tests from Stepik API")
    print("  0. Exit")
    print("=" * 50)


def _ask_number(prompt: str, *, default: int) -> int:
    raw = input(prompt).strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _resolve_test_dir_from_input(solution_or_dir: str, *, is_dir: bool = False) -> str:
    if is_dir:
        candidate = pathlib.Path(solution_or_dir) / "tests"
        if candidate.is_dir():
            return str(candidate)
        return solution_or_dir
    return _resolve_test_dir(solution_or_dir)


def _interactive_menu() -> None:
    """Основной интерактивный цикл грейдера."""
    while True:
        _print_menu()
        choice = input("Select mode [0-5]: ").strip()

        if choice == "0":
            print("Goodbye!")
            break

        # ------------------------------------------------------------------
        # Режим 1 — проверить одно решение
        # ------------------------------------------------------------------
        elif choice == "1":
            solution = input("Enter path to solution file: ").strip()
            if not os.path.isfile(solution):
                print(f"File not found: {solution}")
                continue

            test_dir = _resolve_test_dir(solution)
            if not os.path.isdir(test_dir):
                print(f"Test directory not found: {test_dir}")
                continue

            result = run_tests(solution, test_dir, verbose=False)

            col_file = 28
            print()
            print_correctness_header(col_file=col_file)
            print(format_correctness_row(solution, pathlib.Path(solution).resolve().parent.as_posix(), result, col_file=col_file))

        # ------------------------------------------------------------------
        # Режим 2 — проверить все решения в папке
        # ------------------------------------------------------------------
        elif choice == "2":
            directory = input("Enter path to folder: ").strip()
            if not os.path.isdir(directory):
                print(f"Directory not found: {directory}")
                continue

            test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            scripts = find_all_solution_files(directory)
            if not scripts:
                print("No solution files found.")
                continue

            col_file = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2

            print_correctness_header(col_file=col_file)
            for path in scripts:
                result = run_tests(path, test_dir, verbose=False)
                print(format_correctness_row(path, directory, result, col_file=col_file))

        # ------------------------------------------------------------------
        # Режим 3 — benchmark нескольких решений в папке
        # ------------------------------------------------------------------
        elif choice == "3":
            directory = input("Enter path to folder: ").strip()
            if not os.path.isdir(directory):
                print(f"Directory not found: {directory}")
                continue

            test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            scripts = find_all_solution_files(directory)
            if not scripts:
                print("No solution files found.")
                continue

            results: dict[str, dict[str, Any]] = {}
            for path in scripts:
                results[path] = run_benchmark(path, test_dir)

            ok = {k: v for k, v in results.items() if not v.get("error")}
            if ok:
                min_median = min(v["median"] for v in ok.values())
                for v in ok.values():
                    v["relative"] = v["median"] / min_median if min_median > 0 else 1.0
                    v["verdict"] = _verdict(v["relative"])

            col = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2
            print_benchmark_header(col_file=col)
            for path, data in sorted(ok.items(), key=lambda x: x[1]["median"]):
                print(format_benchmark_row(path, directory, data, col_file=col))

            for path, data in sorted(results.items()):
                if data.get("error"):
                    rel = os.path.relpath(path, directory)
                    print(f"  {rel}: {data['error']}")

        # ------------------------------------------------------------------
        # Режим 4 — timeit micro-benchmark для папки с решениями
        # ------------------------------------------------------------------
        elif choice == "4":
            directory = input("Enter path to folder with solutions: ").strip()
            if not os.path.isdir(directory):
                print(f"Directory not found: {directory}")
                continue

            number = _ask_number("Number of timeit repeats [1000]: ", default=1000)

            grouped = collect_grouped_files(directory)
            if not grouped:
                print("No solution files found.")
                continue

            for folder, paths in sorted(grouped.items()):
                test_dir = os.path.join(directory, folder, "tests")
                if not os.path.isdir(test_dir):
                    continue

                print(f"\n⚡ Micro-bench (timeit): {folder}")
                bench = run_microbench_mode(sorted(paths), test_dir, number=number)
                ok_rows = {k: v for k, v in bench.items() if not v.get("error")}
                if not ok_rows:
                    print("  No results.")
                    continue

                col = 28
                print_benchmark_header(col_file=col)
                for path, data in sorted(ok_rows.items(), key=lambda x: x[1]["median"]):
                    print(format_benchmark_row(path, directory, data, col_file=col))

                for path, data in sorted(bench.items()):
                    if data.get("error"):
                        rel = os.path.relpath(path, directory)
                        print(f"  {rel}: {data['error']}")

        # ------------------------------------------------------------------
        # Режим 5 — загрузить тесты из Stepik API
        # ------------------------------------------------------------------
        elif choice == "5":
            try:
                lesson_id = int(input("Lesson ID: ").strip())
                step_position = int(input("Step position: ").strip())
            except ValueError:
                print("Invalid input. Please enter integers.")
                continue

            try:
                out_dir = fetch_stepik_tests(lesson_id, step_position)
                print(f"Tests saved to: {out_dir}")
            except RuntimeError as exc:
                print(f"Error: {exc}")
            except Exception as exc:
                print(f"Unexpected error: {exc}")

        else:
            print("Unknown choice. Please enter 0–5.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    _interactive_menu()
