from __future__ import annotations

import ast
import contextlib
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import psutil

# executor.py — вспомогательный модуль для запуска кода из строки (не из файла).
# run_solution() используется в diagnostik_stepik.py и тестах.
# run_single_test() в grader.py использует subprocess.Popen напрямую,
# чтобы иметь доступ к замеру памяти (psutil) и точному времени.
# Импортируем RunResult для аннотаций и совместимости.
try:
    from executor import RunResult as _ExecutorRunResult  # noqa: F401  (реэкспорт для тестов)
except ImportError:
    _ExecutorRunResult = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_SOLUTION_FILE_RE = re.compile(r"task(?:\d+)?(?:_\d+)?\.py")

TIMEOUT_SECONDS: float = 10.0
ENCODING: str = "utf-8"
SIMILAR_THRESHOLD: float = 1.15
MUCH_SLOWER_THRESHOLD: float = 1.50
MEASURE_CHILD_MEMORY: bool = True
MICROBENCH_MAX_CASES: int = 5

# ---------------------------------------------------------------------------
# Вспомогательные типы
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    index: int
    input_lines: list[str]
    expected_lines: list[str]
    test_type: str = field(default="stdin")  # "stdin" | "function"


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

    Критерии function-only файла:
      - Нет исполняемых выражений на верхнем уровне (print/input/любой Call)
      - Нет управляющих конструкций (for/while/if/with/try) на верхнем уровне
      - Есть хотя бы одна функция (def или async def)
      - Присваивания РАЗРЕШЕНЫ независимо от значения (date(...), list(), и т.п.)
        т.к. это типичный паттерн Stepik-шаблонов

    При SyntaxError возвращает False — файл будет запущен как скрипт.
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
            # for/while/if/with/try и т.п. → это скрипт
            return False

        if isinstance(node, ast.Expr):
            # Разрешаем только строковые литералы (docstring модуля)
            # Любой вызов (print/input/my_func()) → это скрипт
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            return False

        # Присваивания разрешены всегда: date1 = date(...), MOD = 10**9+7, data = []
        # Это типичный паттерн Stepik-шаблонов — значение не проверяем

    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body)


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


def format_correctness_row(
    path: str, base_dir: str, result: dict[str, Any], *, col_file: int
) -> str:
    """Сформатировать строку таблицы корректности для режимов 1 и 2."""
    total = result["total"]
    passed = result["passed"]
    ok = passed == total and result["failed"] == 0 and result["errors"] == 0 and total > 0
    status = "OK" if ok else "FAIL"
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
        f"{'File':<{col_file}} {'Passed':>7}  "
        f"{'Total time':>10}  {'Avg time':>9}  "
        f"{'Memory, MB':>12}  {'Status':>6}  {'Fail test':>9}"
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

        all_times: list[float] = []
        for case in cases_to_bench:
            stdin_data = "\n".join(case.input_lines) + "\n"
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
        tests/1.type   — "function" (опционально; отсутствие = "stdin")
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
        m = _INPUT_RE.match(inp_file.name)
        if m:
            idx = int(m.group(1))
            exp_file = dir_path / f"expected_{idx}.txt"
            if not exp_file.exists():
                continue
            input_lines = load_text_lines(str(inp_file))
            expected_lines = load_text_lines(str(exp_file))
            cases.append(
                TestCase(index=idx, input_lines=input_lines, expected_lines=expected_lines)
            )
            continue

    _NUM_RE = re.compile(r"^\d+$")
    for inp_file in dir_path.iterdir():
        if _NUM_RE.match(inp_file.name):
            clue_file = dir_path / f"{inp_file.name}.clue"
            if not clue_file.exists():
                continue
            idx = int(inp_file.name)
            input_lines = load_text_lines(str(inp_file))
            expected_lines = load_text_lines(str(clue_file))

            # Читаем .type-файл если он существует
            type_file = dir_path / f"{inp_file.name}.type"
            test_type = "stdin"
            if type_file.exists():
                raw_type = type_file.read_text(encoding=ENCODING).strip()
                if raw_type == "function":
                    test_type = "function"

            cases.append(
                TestCase(
                    index=idx,
                    input_lines=input_lines,
                    expected_lines=expected_lines,
                    test_type=test_type,
                )
            )

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

    Для stdin-режима (is_function_mode=False):
        Возвращает только input_lines, соединённые через \n.
        Пустой список → пустая строка.

    Для function-режима (is_function_mode=True):
        Предваряет source_code перед input_lines:
        Это позволяет передавать полный контекст executor'у
        (источник + данные) как единую stdin-строку.
    """
    if not input_lines:
        if is_function_mode:
            return source_code
        return ""

    joined = "\n".join(input_lines)
    if is_function_mode:
        return source_code + "\n" + joined
    return joined


def _measure_peak_memory(
    proc: subprocess.Popen, result: list[float], stop: threading.Event
) -> None:
    """Поток: просматривать RSS дочернего процесса до его завершения.

    Делает первый замер немедленно (до первого sleep), чтобы уловить
    даже очень короткие процессы (< 20 мс). Затем продолжает опрос
    каждые 20 мс до сигнала stop.

    Записывает пик памяти (МБ) в result[0].
    """
    peak = 0.0
    try:
        ps_proc = psutil.Process(proc.pid)
        try:
            rss = ps_proc.memory_info().rss / 1024 / 1024
            if rss > peak:
                peak = rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            result[0] = peak
            return
        while not stop.is_set():
            try:
                rss = ps_proc.memory_info().rss / 1024 / 1024
                if rss > peak:
                    peak = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                break
            stop.wait(0.02)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    result[0] = peak


# ---------------------------------------------------------------------------
# Function-mode runner
# ---------------------------------------------------------------------------


def _read_meta_function_name(solution_path: str) -> str | None:
    """Прочитать function_name из meta.json рядом с файлом решения.

    Ищет meta.json в той же директории, что и solution_path.
    Возвращает None если файл не найден или поле отсутствует.
    """
    meta_path = pathlib.Path(solution_path).parent / "meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, encoding=ENCODING) as f:
            meta = json.load(f)
        name = meta.get("function_name")
        return str(name) if name else None
    except (json.JSONDecodeError, OSError):
        return None


def _ast_function_name(solution_path: str) -> str | None:
    """Парсит файл решения через ast и возвращает имя первой функции (эвристика).

    Используется как fallback когда meta.json недоступен или function_name = None.
    """
    try:
        source = pathlib.Path(solution_path).read_text(encoding=ENCODING)
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None



def _detect_run_mode(solution_path: str, test_dir: str) -> str:
    """Единая точка детекции режима запуска: "stdin" или "function".

    Стратегия определения (первый сработавший выигрывает):
      1. meta.json рядом с файлом: если function_name != None → "function"
      2. .type-файлы в test_dir: если хоть один содержит "function" → "function"
      3. AST-анализ файла решения через is_function_only_solution → "function"
      4. Иначе → "stdin"

    Вызывается один раз в run_tests(), результат передаётся в run_single_test().
    Это устраняет рассинхронизацию трёх источников истины.
    """
    # 1. meta.json
    if _read_meta_function_name(solution_path) is not None:
        return "function"

    # 2. .type-файлы
    test_dir_path = pathlib.Path(test_dir)
    if test_dir_path.is_dir():
        for type_file in test_dir_path.glob("*.type"):
            raw = type_file.read_text(encoding=ENCODING).strip()
            if raw == "function":
                return "function"

    # 3. AST-анализ файла решения
    try:
        file_content = pathlib.Path(solution_path).read_text(encoding=ENCODING)
        if is_function_only_solution(file_content):
            return "function"
    except OSError:
        pass

    return "stdin"


def _build_function_wrapper(solution_path: str, input_data: str, function_name: str) -> str:
    """Генерирует исходный код скрипта-обёртки для function-mode запуска.

    Стратегия передачи аргументов — позиционная через inspect.signature:
      1. Импортирует функцию из файла решения.
      2. Выполняет input_data (объявления переменных из тест-кейса).
      3. Узнаёт количество и порядок параметров через inspect.signature.
      4. Собирает аргументы из locals() по имени параметра и вызывает функцию.

    Важно: имена параметров функции ДОЛЖНЫ совпадать с именами переменных в input_data.
    Если совпадения нет (date1/date2 vs start/end) — используй позиционный формат тестов:
      файл без расширения с аргументами по одному на строку (позиционный формат).

    Args:
        solution_path: абсолютный путь к файлу решения.
        input_data:    содержимое .type=function тест-кейса
                       (строки вида "d1 = date(2020, 1, 1)").
        function_name: имя функции для импорта.
    """
    abs_path = str(pathlib.Path(solution_path).resolve())
    safe_path = abs_path.replace("\\", "\\\\").replace("'", "\\'")
    safe_input = input_data.strip()
    safe_func = function_name
    module_stem = pathlib.Path(solution_path).stem

    return f"""import sys
import pathlib
import inspect
sys.path.insert(0, str(pathlib.Path('{safe_path}').parent))

# Стандартные импорты, которые могут быть нужны в input_data
from datetime import date, time, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

# Импортируем функцию из файла решения
from {module_stem} import {safe_func}

# Выполняем объявления переменных из тест-кейса
{safe_input}

# Определяем аргументы через inspect.signature (позиционно, по имени параметра)
_sig = inspect.signature({safe_func})
_args = [locals()[_p] for _p in _sig.parameters]
print({safe_func}(*_args))
"""


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
    stdin_bytes: bytes | None = None

    if case.test_type == "function":
        # Определяем имя функции: meta.json → ast fallback
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
            }
        input_data = "\n".join(case.input_lines)
        wrapper_src = _build_function_wrapper(solution_path, input_data, func_name)
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

    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [sys.executable, run_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            }
        finally:
            stop_event.set()
            # Удаляем временный wrapper-файл (contextlib.suppress — безопасно при краше)
            if tmp_wrapper is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_wrapper.name)

        elapsed = time.perf_counter() - start
        if measure_memory:
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
            }

        actual_lines = [line.rstrip("\n") for line in stdout.splitlines()]
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
        }


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
    # Определяем режим запуска один раз для всех тест-кейсов
    # (устраняет рассинхронизацию между .type-файлами, meta.json и AST)
    run_mode = _detect_run_mode(solution_path, test_dir)
    # Переопределяем test_type для всех кейсов если режим определён на уровне файла
    # (например, AST показал function-only, но .type-файлов нет)
    if run_mode == "function":
        for case in test_cases:
            if case.test_type == "stdin":
                case.test_type = "function"

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
            icon = "\u2713" if r["passed"] else "\u2717"
            print(f"  {icon} Test {case.index}", end="")
            if r["error"]:
                print(f" [ERROR: {r['error']}]")
            elif not r["passed"]:
                print(" [FAIL]")
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

    Код запускается как строка через python -c.
    stdin сбрасывается перед каждой итерацией через _reset_stdin() в начале stmt.

    Возвращает словарь с ключами:
        times  (list[float]) — список замеров (в секундах на итерацию)
        error  (str)         — сообщение об ошибке (пустая = успех)
    """
    # Экранируем тройные кавычки в исходнике, чтобы неломать heredoc-строку
    safe_code = source_code.replace("'''", '"""')
    stmt_with_reset = "_reset_stdin()\n" + safe_code

    # Весь вспомогательный код помещаем в setup через exec,
    # чтобы _reset_stdin была доступна в глобальном пространстве stmt.
    # stmt — строка, выполняемая timeit; globals не пробрасываются через repeat()
    # в Python 3.14+, поэтому инжектируем функцию через builtins.
    bench_script = (
        "import timeit as _timeit, sys as _sys, io as _io, builtins as _builtins\n"
        "_stdin = " + repr(stdin_data) + "\n"
        "def _reset_stdin():\n"
        "    _sys.stdin = _io.StringIO(_stdin)\n"
        "_builtins._reset_stdin = _reset_stdin\n"
        "_reset_stdin()\n"
        "_code = '''" + stmt_with_reset + "'''\n"
        f"_number = {number}\n"
        "_times = _timeit.repeat(\n"
        "    stmt=_code,\n"
        "    setup='pass',\n"
        "    repeat=5,\n"
        f"    number=_number,\n"
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


_SEP = "-" * 92


# ---------------------------------------------------------------------------
# Stepik API  (не используется в меню — зарезервировано для будущей интеграции)
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

    Примечание: функция не вызывается из интерактивного меню.
    Для полноценной загрузки задач используй at_first.py.
    """
    import json

    import requests

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
# Профили нагрузки
# ---------------------------------------------------------------------------

_BENCH_PROFILES: dict[str, int] = {
    "1": 5,
    "2": 15,
    "3": 50,
    "4": 0,
}

_MICRO_PROFILES: dict[str, int] = {
    "1": 500,
    "2": 1_000,
    "3": 5_000,
    "4": 50_000,
    "5": 100_000,
    "6": 0,
}


def _ask_bench_profile() -> int:
    """Запросить профиль нагрузки для subprocess-бенчмарка (режим 3)."""
    print("  Load profiles (repeats per solution):")
    print("    1  low       \u2014   5 runs")
    print("    2  medium    \u2014  15 runs")
    print("    3  high      \u2014  50 runs")
    print("    4  custom    \u2014  5\u2013100 runs")
    choice = input("  Select profile [2]: ").strip() or "2"
    repeats = _BENCH_PROFILES.get(choice)
    if repeats is None:
        repeats = _BENCH_PROFILES["2"]
    if repeats == 0:
        repeats = _ask_number("  Enter repeats (5\u2013100): ", default=15)
        repeats = max(5, min(100, repeats))
    return repeats


def _ask_micro_profile() -> int:
    """Запросить профиль нагрузки для timeit micro-bench (режим 4)."""
    print("  Load profiles (calls per run):")
    print("    1  fast      \u2014     500")
    print("    2  normal    \u2014   1 000")
    print("    3  thorough  \u2014   5 000")
    print("    4  deep      \u2014  50 000")
    print("    5  hard      \u2014 100 000  (short deterministic functions only)")
    print("    6  custom    \u2014 100\u2013500 000")
    choice = input("  Select profile [2]: ").strip() or "2"
    number = _MICRO_PROFILES.get(choice)
    if number is None:
        number = _MICRO_PROFILES["2"]
    if number == 0:
        number = _ask_number("  Enter calls (100\u2013500 000): ", default=1000)
        number = max(100, min(500_000, number))
    return number


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
    """Показать меню один раз, выполнить выбранный режим и завершить работу."""
    _print_menu()
    choice = input("Select mode [0-4]: ").strip()

    if choice == "0":
        print("Goodbye!")
        return

    if choice == "1":
        solution = input("Enter path to solution file: ").strip()
        if not os.path.isfile(solution):
            print(f"File not found: {solution}")
            return

        test_dir = _resolve_test_dir(solution)
        if not os.path.isdir(test_dir):
            print(f"Test directory not found: {test_dir}")
            return

        result = run_tests(solution, test_dir, verbose=False)

        col_file = 28
        print()
        print_correctness_header(col_file=col_file)
        base = pathlib.Path(solution).resolve().parent.as_posix()
        print(format_correctness_row(solution, base, result, col_file=col_file))

    elif choice == "2":
        directory = input("Enter path to folder: ").strip()
        if not os.path.isdir(directory):
            print(f"Directory not found: {directory}")
            return

        test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        col_file = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2

        print_correctness_header(col_file=col_file)
        for path in scripts:
            result = run_tests(path, test_dir, verbose=False)
            print(format_correctness_row(path, directory, result, col_file=col_file))

    elif choice == "3":
        directory = input("Enter path to folder: ").strip()
        if not os.path.isdir(directory):
            print(f"Directory not found: {directory}")
            return

        test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        repeats = _ask_bench_profile()

        results: dict[str, dict[str, Any]] = {}
        for path in scripts:
            results[path] = run_benchmark(path, test_dir, repeats=repeats)

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

    elif choice == "4":
        directory = input("Enter path to folder with solutions: ").strip()
        if not os.path.isdir(directory):
            print(f"Directory not found: {directory}")
            return

        number = _ask_micro_profile()

        grouped = collect_grouped_files(directory)
        if not grouped:
            print("No solution files found.")
            return

        for folder, paths in sorted(grouped.items()):
            if folder != ".":
                folder_abs = pathlib.Path(directory) / folder
            else:
                folder_abs = pathlib.Path(directory)
            test_dir = _resolve_test_dir_from_input(str(folder_abs), is_dir=True)

            label = folder if folder != "." else os.path.basename(directory)
            print(f"\n\u26a1 Micro-bench (timeit): {label}")

            if not os.path.isdir(test_dir):
                print(f"  \u26a0 Tests not found: {test_dir}")
                print("  Expected: tests/ subfolder next to solution files.")
                continue

            bench = run_microbench_mode(sorted(paths), test_dir, number=number)

            if not bench:
                print("  \u26a0 No test cases found in:", test_dir)
                continue

            ok_rows = {k: v for k, v in bench.items() if not v.get("error")}

            col = max((len(os.path.relpath(p, directory)) for p in paths), default=20) + 2

            if ok_rows:
                print_benchmark_header(col_file=col)
                for path, data in sorted(ok_rows.items(), key=lambda x: x[1]["median"]):
                    print(format_benchmark_row(path, directory, data, col_file=col))

            for path, data in sorted(bench.items()):
                if data.get("error"):
                    rel = os.path.relpath(path, directory)
                    print(f"  \u2717 {rel}: {data['error']}")

            if not ok_rows and not any(v.get("error") for v in bench.values()):
                print("  No results.")

    else:
        print("Unknown choice. Please enter 0\u20134.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    _interactive_menu()
