"""grader.py — интерактивный грейдер решений.

Архитектурный слой: Application.
Предоставляет 4 режима работы:
  1. Проверка одного файла (run_tests)
  2. Сравнение всех решений в папке (find_all_solution_files + run_tests)
  3. Subprocess-бенчмарк (run_benchmark)
  4. Timeit-микробенчмарк (run_microbench_mode / run_microbench)

Использует executor.py для запуска решений и microbench_runner.py для timeit.
"""

from __future__ import annotations

import ast
import contextlib
import difflib
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

__version__ = "1.0.0"

# rich — опциональная зависимость для цветного вывода таблиц и прогресс-баров.
# При её отсутствии грейдер откатывается на простой текстовый вывод.
try:
    from rich.console import Console
    from rich.progress import track as _rich_track
    from rich.table import Table
    from rich.text import Text

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False

    # Минимальные заглушки, чтобы имена Console/Table/Text были всегда определены
    # (используются в аннотациях и в коде, не зависящем от _RICH).
    class Console:  # type: ignore[no-redef]
        def print(self, *args: Any, **kwargs: Any) -> None:
            print(*args)

    class Table:  # type: ignore[no-redef]
        pass

    class Text:  # type: ignore[no-redef]
        pass

    def _rich_track(sequence: Any, description: str = "") -> Any:  # noqa: ARG001
        return sequence


# executor.py — вспомогательный модуль для запуска кода из строки (не из файла).
# run_solution() используется в тестах (tests/test_executor.py); grader сам его не вызывает.
# run_single_test() в grader.py использует subprocess.Popen напрямую,
# чтобы иметь доступ к замеру памяти (psutil) и точному времени.
# Импортируем RunResult для аннотаций и совместимости.
try:
    from executor import RunResult as _ExecutorRunResult  # noqa: F401  (реэкспорт для тестов)
except ImportError:
    _ExecutorRunResult = None  # type: ignore[assignment,misc]

# microbench_runner.py / normalizers.py — первоисточники timeit-бенчмарка и
# нормализации float-вывода. grader делегирует им вместо inline-дубликатов.
from microbench_runner import run_microbench
from normalizers import normalize_floats as _normalize_output_line
from storage import load_json_file

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
    __test__ = False  # prevent pytest from collecting this as a test class
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
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Invert)):
        return _is_safe_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_constant(node.left) and _is_safe_constant(node.right)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_safe_constant(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_safe_constant(k) for k in node.keys if k is not None) and all(
            _is_safe_constant(v) for v in node.values
        )
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
        task_1.py, task_2.py            — стиль, создаваемый downloader.py
    """
    return bool(_SOLUTION_FILE_RE.fullmatch(file_name))


def find_all_solution_files(directory: str) -> list[str]:
    scripts = []

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                scripts.append(str(pathlib.Path(root) / file_name))

    return sorted(scripts)


def collect_grouped_files(directory: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                try:
                    rel_folder = str(pathlib.Path(root).relative_to(directory))
                except ValueError:
                    rel_folder = os.path.relpath(root, directory)
                grouped[rel_folder].append(str(pathlib.Path(root) / file_name))

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
        f"{data['relative'] * 100:>7.1f}%  {data['verdict']}"
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


# ---------------------------------------------------------------------------
# Цветной вывод (rich) с откатом на plain-text
# ---------------------------------------------------------------------------

# Цвета статусов корректности (режимы 1/2) и вердиктов TLE/RE/WA.
_STATUS_COLORS: dict[str, str] = {
    "OK": "green",
    "AC": "green",
    "FAIL": "red",
    "WA": "red",
    "TLE": "red",
    "RE": "red",
    "ERROR": "red",
}

# Цвета вердиктов бенчмарка (режимы 3/4).
_VERDICT_COLORS: dict[str, str] = {
    "SIMILAR": "green",
    "SLOWER": "yellow",
    "MUCH_SLOWER": "red",
    "MUCH SLOWER": "red",
}


def _correctness_status(result: dict[str, Any]) -> str:
    """Вернуть "OK"/"FAIL" для строки таблицы корректности."""
    total = result["total"]
    ok = result["passed"] == total and result["failed"] == 0 and result["errors"] == 0 and total > 0
    return "OK" if ok else "FAIL"


def print_correctness_results(
    rows: list[tuple[str, dict[str, Any]]], base_dir: str, *, col_file: int
) -> None:
    """Напечатать таблицу корректности (rich при наличии, иначе plain-text)."""
    if _RICH and _console is not None:
        table = Table(show_lines=False)
        table.add_column("File", style="cyan", no_wrap=True)
        table.add_column("Passed", justify="right")
        table.add_column("Total time", justify="right")
        table.add_column("Avg time", justify="right")
        table.add_column("Memory, MB", justify="right")
        table.add_column("Status", justify="center")
        table.add_column("Fail test", justify="right")
        for path, result in rows:
            status = _correctness_status(result)
            color = _STATUS_COLORS.get(status, "white")
            table.add_row(
                os.path.relpath(path, base_dir),
                f"{result['passed']}/{result['total']}",
                f"{result['total_time']:.4f}",
                f"{result['avg_time']:.4f}",
                f"{result['peak_memory_mb']:.2f}",
                Text(status, style=color),
                str(result["first_fail"]),
            )
        _console.print(table)
        return

    print_correctness_header(col_file=col_file)
    for path, result in rows:
        print(format_correctness_row(path, base_dir, result, col_file=col_file))


def print_benchmark_results(
    rows: list[tuple[str, dict[str, Any]]],
    base_dir: str,
    *,
    col_file: int,
    title: str = "",
) -> None:
    """Напечатать benchmark-таблицу (rich при наличии, иначе plain-text)."""
    if _RICH and _console is not None:
        table = Table(title=title or None, show_lines=False)
        table.add_column("File", style="cyan", no_wrap=True)
        for name in ("Runs", "Min", "Median", "Mean", "Max", "Std dev", "Memory"):
            table.add_column(name, justify="right")
        table.add_column("Relative", justify="right")
        table.add_column("Verdict", justify="center")
        for path, data in rows:
            verdict = data["verdict"]
            color = _VERDICT_COLORS.get(verdict, "white")
            table.add_row(
                os.path.relpath(path, base_dir),
                str(data["runs"]),
                f"{data['min']:.4f}",
                f"{data['median']:.4f}",
                f"{data['mean']:.4f}",
                f"{data['max']:.4f}",
                f"{data['stdev']:.4f}",
                f"{data['peak_memory_mb']:.2f}",
                f"{data['relative'] * 100:.1f}%",
                Text(verdict, style=color),
            )
        _console.print(table)
        return

    print_benchmark_header(col_file=col_file)
    for path, data in rows:
        print(format_benchmark_row(path, base_dir, data, col_file=col_file))


def _cprint(text: str, *, style: str = "") -> None:
    """Печать строки со стилем (rich) или без (plain). markup отключён — безопасно
    для произвольного вывода решения (скобки не интерпретируются как разметка)."""
    if _RICH and _console is not None and style:
        _console.print(text, style=style, markup=False)
    else:
        print(text)


def _print_case_verbose(case: TestCase, r: dict[str, Any]) -> None:
    """Подробный вывод одного тест-кейса (режим 1, verbose): вердикт + diff при WA."""
    passed = r["passed"]
    verdict = r.get("verdict", "AC" if passed else "WA")
    icon = "✓" if passed else "✗"
    color = "green" if passed else "red"
    _cprint(f"  {icon} Test {case.index}: {verdict}", style=color)

    if r["error"]:
        _cprint(f"    [ERROR] {r['error']}", style="red")
        return
    if passed:
        return

    # WA: компактное сравнение expected vs actual + diff.
    expected = " | ".join(r.get("expected", case.expected_lines)) or "(empty)"
    actual = " | ".join(r.get("output", [])) or "(empty)"
    _cprint(f"    Expected: {expected}")
    _cprint(f"    Actual:   {actual}")
    diff = r.get("diff", "")
    if diff:
        _cprint("    Diff:")
        for line in diff.splitlines():
            if line.startswith("+"):
                _cprint(f"    {line}", style="green")
            elif line.startswith("-"):
                _cprint(f"    {line}", style="red")
            else:
                _cprint(f"    {line}", style="dim")


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
            input_data = "\n".join(case.input_lines)

            if case.test_type == "function" and _is_python_code_block(input_data):
                # Function-call блок — это Python-код, а не stdin.
                # timeit/exec тут не годится: используем subprocess-тайминг
                # через run_single_test (менее точно, зато корректно).
                sub_repeats = max(1, number // 50)
                case_times: list[float] = []
                for _ in range(sub_repeats):
                    r = run_single_test(path, case, timeout=60.0)
                    if r["error"] or r["timed_out"]:
                        results[path] = {"error": f"test {case.index}: {r['error'] or 'timeout'}"}
                        break
                    case_times.append(r["time"])
                else:
                    all_times.extend(case_times)
                    continue
                break

            stdin_data = input_data + "\n"
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


def load_text_lines(file_path: str) -> list[str]:
    """Загрузить текстовый файл и вернуть список строк без завершающих переносов."""
    return pathlib.Path(file_path).read_text(encoding=ENCODING).splitlines()


def _parse_testblock_file(text: str) -> list[str]:
    """Разобрать input.txt/output.txt с маркерами блоков `# TEST_N:`.

    Возвращает список содержимого блоков (каждый .strip()).
    Строки `# INPUT DATA:` игнорируются.

    Пустые блоки СОХРАНЯЮТСЯ как `''` (например, `# TEST_5:` без данных),
    чтобы индексы input- и output-блоков оставались синхронными.
    """
    blocks: list[str] = []
    current_lines: list[str] = []
    in_block = False

    for line in text.splitlines():
        if re.match(r"#\s*TEST_\d+:", line.strip()):
            if in_block:
                blocks.append("\n".join(current_lines).strip())
                current_lines = []
            in_block = True
        elif line.strip().startswith("# INPUT DATA:"):
            continue
        elif in_block:
            current_lines.append(line)

    if in_block:
        blocks.append("\n".join(current_lines).strip())

    return blocks


def _is_python_code_block(block: str) -> bool:
    """Вернуть True, если block похож на Python-код (а не на stdin-данные).

    Эвристика: блок парсится как валидный Python AST и содержит хотя бы один
    узел ``ast.Name`` (ссылку на переменную/функцию). Обычные stdin-данные
    (числа, даты-строки) либо не парсятся, либо не содержат Name-узлов.
    Python-код всегда ссылается на переменные/функции.

    Примеры:
        ``10\\n20\\n30``                  → False (голые константы, нет Name)
        ``04.11.2021``                   → False (SyntaxError)
        ``print(func(x))``               → True  (вызов функции)
        ``r = wins([...])\\nfor ...``     → True  (присваивание + for)
        ``chainmap = ChainMap({})``      → True  (присваивание)
        ``""``                           → False (пустой блок)
    """
    if not block.strip():
        return False
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.Name) for node in ast.walk(tree))


def load_test_cases(test_dir: str) -> list[TestCase]:
    """Загрузить тест-кейсы из директории.

    Поддерживаются три формата:

    Формат 3 — python-generation/Professional (высший приоритет):
        tests/input.txt   — ВСЕ входные блоки с маркерами `# TEST_N:`
        tests/output.txt  — ВСЕ ожидаемые блоки с маркерами `# TEST_N:`
        Тип блока определяется автоматически: если блок — валидный Python-код
        со ссылками на переменные/функции (`print(func(...))`, присваивания,
        for-циклы) → "function", иначе (голые числа/строки) → "stdin".

    Формат 1 — downloader.py (legacy):
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

    # Формат 3: python-generation (input.txt + output.txt с блоками # TEST_N:)
    input_file = dir_path / "input.txt"
    output_file = dir_path / "output.txt"
    if input_file.exists() and output_file.exists():
        input_text = input_file.read_text(encoding=ENCODING)
        output_text = output_file.read_text(encoding=ENCODING)
        input_blocks = _parse_testblock_file(input_text)
        output_blocks = _parse_testblock_file(output_text)
        if input_blocks and output_blocks:
            for i, (inp, out) in enumerate(zip(input_blocks, output_blocks, strict=False), 1):
                test_type = "function" if _is_python_code_block(inp) else "stdin"
                cases.append(
                    TestCase(
                        index=i,
                        input_lines=inp.splitlines(),
                        expected_lines=out.splitlines(),
                        test_type=test_type,
                    )
                )
            return cases

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

    # python-generation: input.txt + output.txt рядом с решением или в родителе
    for candidate in (parent, parent.parent):
        if (candidate / "input.txt").exists() and (candidate / "output.txt").exists():
            return str(candidate)

    for f in parent.iterdir():
        if f.suffix == ".clue" or re.match(r"^input_\d+\.txt$", f.name):
            return str(parent)

    return str(candidate_tests)


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
        meta = load_json_file(str(meta_path))
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


def _apply_run_mode_override(
    cases: list[TestCase], solution_path: str, test_dir: str
) -> list[TestCase]:
    """Переопределить test_type на "function" для всех stdin-кейсов, если режим
    запуска определён как function на уровне файла (AST/meta.json/.type).

    Устраняет рассинхронизацию между .type-файлами, meta.json и AST.
    Мутирует и возвращает переданный список cases.
    """
    if _detect_run_mode(solution_path, test_dir) == "function":
        for case in cases:
            if case.test_type == "stdin":
                case.test_type = "function"
    return cases


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
    safe_input = input_data.strip()
    safe_func = function_name
    module_stem = pathlib.Path(solution_path).stem

    # repr() безопасно интерполирует путь (включая Windows-бэкслеши и спецсимволы).
    return f"""import sys
import pathlib
import inspect
sys.path.insert(0, str(pathlib.Path({abs_path!r}).parent))

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


def _build_call_wrapper(solution_path: str, call_block: str) -> str:
    """Генерирует скрипт, импортирующий все публичные имена из решения и
    исполняющий call_block как есть.

    Используется для python-generation function-call формата (Module_3.1, 3.3),
    где блок теста уже содержит полный вызов вида `print(func(args))`.
    inspect.signature НЕ используется — аргументы заданы в самом блоке.
    """
    abs_path = str(pathlib.Path(solution_path).resolve())
    solution_dir = str(pathlib.Path(abs_path).parent)
    module_name = pathlib.Path(abs_path).stem

    return f"""import sys
import importlib.util

# Стандартные wildcard-импорты, которые могут встречаться в тест-блоке
# (ChainMap, OrderedDict, defaultdict, Counter, date, datetime, и т.п.).
# Делаются ПЕРЕД импортом из решения, чтобы имена решения имели приоритет.
from collections import *  # noqa: F401,F403
from datetime import *  # noqa: F401,F403
from itertools import *  # noqa: F401,F403
from functools import *  # noqa: F401,F403
from decimal import Decimal  # noqa: F401
from fractions import Fraction  # noqa: F401

sys.path.insert(0, {solution_dir!r})
_spec = importlib.util.spec_from_file_location({module_name!r}, {abs_path!r})
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
# Импорт из решения идёт ПОСЛЕДНИМ — публичные имена решения
# перекрывают одноимённые из stdlib wildcard-импортов выше.
for _name in dir(_mod):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_mod, _name)

{call_block}
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
    mem_thread: threading.Thread | None = None

    # Гарантируем UTF-8 в stdout/stderr дочернего процесса на всех платформах
    # (на Windows по умолчанию используется cp1251, что ломает кириллицу в выводе).
    _child_env = os.environ.copy()
    _child_env["PYTHONIOENCODING"] = "utf-8"
    _child_env["PYTHONUTF8"] = "1"

    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [sys.executable, run_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env,
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

        if verbose:
            _print_case_verbose(case, r)

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
        p = pathlib.Path(solution_or_dir)
        # tests/ subdir takes priority
        candidate = p / "tests"
        if candidate.is_dir():
            return str(candidate)
        # Format 3: input.txt + output.txt directly in the given dir
        if (p / "input.txt").exists() and (p / "output.txt").exists():
            return str(p)
        # fallback: return as-is, load_test_cases will handle it
        return str(p)
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
        if not pathlib.Path(solution).is_file():
            print(f"File not found: {solution}")
            return

        test_dir = _resolve_test_dir(solution)
        if not pathlib.Path(test_dir).is_dir():
            print(f"Test directory not found: {test_dir}")
            return

        result = run_tests(solution, test_dir, verbose=True)

        col_file = 28
        print()
        base = pathlib.Path(solution).resolve().parent.as_posix()
        print_correctness_results([(solution, result)], base, col_file=col_file)

    elif choice == "2":
        directory = input("Enter path to folder: ").strip()
        if not pathlib.Path(directory).is_dir():
            print(f"Directory not found: {directory}")
            return

        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        col_file = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2

        rows: list[tuple[str, dict[str, Any]]] = []
        for path in _rich_track(scripts, description="Проверка решений..."):
            individual_test_dir = _resolve_test_dir(path)
            if not pathlib.Path(individual_test_dir).is_dir():
                individual_test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            result = run_tests(path, individual_test_dir, verbose=False)
            rows.append((path, result))
        print_correctness_results(rows, directory, col_file=col_file)

    elif choice == "3":
        directory = input("Enter path to folder: ").strip()
        if not pathlib.Path(directory).is_dir():
            print(f"Directory not found: {directory}")
            return

        scripts = find_all_solution_files(directory)
        if not scripts:
            print("No solution files found.")
            return

        repeats = _ask_bench_profile()

        results: dict[str, dict[str, Any]] = {}
        for path in _rich_track(scripts, description="Бенчмарк решений..."):
            individual_test_dir = _resolve_test_dir(path)
            if not pathlib.Path(individual_test_dir).is_dir():
                individual_test_dir = _resolve_test_dir_from_input(directory, is_dir=True)
            results[path] = run_benchmark(path, individual_test_dir, repeats=repeats)

        ok = {k: v for k, v in results.items() if not v.get("error")}
        if ok:
            min_median = min(v["median"] for v in ok.values())
            for v in ok.values():
                v["relative"] = v["median"] / min_median if min_median > 0 else 1.0
                v["verdict"] = _verdict(v["relative"])

        col = max((len(os.path.relpath(p, directory)) for p in scripts), default=20) + 2
        ranked = sorted(ok.items(), key=lambda x: x[1]["median"])
        print_benchmark_results(ranked, directory, col_file=col)

        for path, data in sorted(results.items()):
            if data.get("error"):
                rel = os.path.relpath(path, directory)
                print(f"  {rel}: {data['error']}")

    elif choice == "4":
        directory = input("Enter path to folder with solutions: ").strip()
        if not pathlib.Path(directory).is_dir():
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

            label = folder if folder != "." else pathlib.Path(directory).name
            print(f"\n\u26a1 Micro-bench (timeit): {label}")

            if not pathlib.Path(test_dir).is_dir():
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
                ranked = sorted(ok_rows.items(), key=lambda x: x[1]["median"])
                print_benchmark_results(ranked, directory, col_file=col)

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
