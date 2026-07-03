"""grader_core.py — загрузка тест-кейсов и исполнение решений.

Архитектурный слой: Application / Business logic.
Отвечает за:
  - обнаружение и классификацию файлов-решений,
  - загрузку тест-кейсов (форматы 1/2/3) и определение режима запуска
    (stdin vs function),
  - генерацию wrapper-скриптов для function-mode и их исполнение в subprocess,
  - агрегацию статистики (run_tests, run_benchmark, run_microbench_mode).

Не содержит вывода (rich-таблицы) — это core/reporter.py; не содержит CLI/меню —
это cli.py.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 2).
Перенесён в core/ (Issue #26).
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
import warnings
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
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

# microbench_runner.py / normalizers.py — первоисточники timeit-бенчмарка и
# нормализации float-вывода. grader_core делегирует им вместо inline-дубликатов.
from stepik_grader.core.microbench_runner import apply_relative_ranking, run_microbench
from stepik_grader.core.normalizers import normalize_floats as _normalize_output_line
from stepik_grader.core.parsers import parse_testblock_file as _parse_testblock_file
from stepik_grader.core.storage import load_json_file

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Паттерн имён файлов-решений.  Матчит (fullmatch):
#   task.py        — базовый файл без номера
#   task1.py       — исторический стиль (номер задачи слитно)
#   task1_2.py     — номер задачи + номер решения
#   task_1.py      — стиль downloader.py (нет цифры перед _)
#   task_12.py     — то же, двузначный суффикс
# НЕ матчит: solution.py, main.py, task_v2.py (буквы после _)
_SOLUTION_FILE_RE = re.compile(r"task(?:\d+)?(?:_\d+)?\.py")

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
class TestCase:
    __test__ = False  # prevent pytest from collecting this as a test class
    index: int
    input_lines: list[str]
    expected_lines: list[str]
    test_type: str = field(default="stdin")  # "stdin" | "function"


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


def _is_safe_constant(node: ast.expr) -> bool:
    """Вернуть True, если узел — безопасное константное выражение без вызовов.

    Рекурсивно проверяет AST-узел: принимает литералы (Constant), арифметику
    из констант (BinOp, UnaryOp) и вложенные контейнеры (List/Tuple/Set/Dict).
    Отклоняет любые вызовы (Call), обращения к атрибутам (Attribute) и Name.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd | ast.Invert):
        return _is_safe_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_constant(node.left) and _is_safe_constant(node.right)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
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

    return any(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) for node in tree.body)


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


def load_text_lines(file_path: str) -> list[str]:
    """Загрузить текстовый файл и вернуть список строк без завершающих переносов."""
    return pathlib.Path(file_path).read_text(encoding=ENCODING).splitlines()


def _is_python_code_block(block: str) -> bool:
    """Вернуть True, если block похож на Python-код (а не на stdin-данные).

    Эвристика: блок парсится как валидный Python AST и содержит хотя бы один
    узел ``ast.Name`` (ссылку на переменную/функцию). Обычные stdin-данные
    (числа, даты-строки) либо не парсятся, либо не содержат Name-узлов.
    Python-код всегда ссылается на переменные/функции.

    Исключение (issue #47 R-02): единственное top-level выражение, которое
    целиком состоит из голого имени (``x``, ``print``) — это не вызов и не
    присваивание, а вырожденный случай, который на практике встречается
    только как stdin-данные, совпадающие по виду с identifier'ом. Такой блок
    классифицируется как False, не затрагивая остальную эвристику.

    Примеры:
        ``10\\n20\\n30``                  → False (голые константы, нет Name)
        ``04.11.2021``                   → False (SyntaxError)
        ``x``                            → False (голое имя, не вызов/присваивание)
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
    if (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Name)
    ):
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
            # issue #48 R-03: Format 1/2 files sitting next to input.txt/output.txt
            # are silently ignored below (Format 3 wins and returns early) -- warn
            # so a user who hand-authored Format 1 and later got input.txt added by
            # downloader.py isn't left wondering why their .clue files don't matter.
            if any(
                f.suffix == ".clue" or re.match(r"^input_\d+\.txt$", f.name)
                for f in dir_path.iterdir()
                if f not in (input_file, output_file)
            ):
                warnings.warn(
                    f"{test_dir}: Format 1/2 test files (N/N.clue or input_N.txt) found "
                    "alongside input.txt/output.txt -- Format 3 takes priority and the "
                    "others are ignored.",
                    stacklevel=2,
                )
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


def resolve_test_dir(solution_path: str) -> str | None:
    """Вернуть путь к директории тест-кейсов для заданного файла решения, или
    None, если ни одна стратегия поиска не нашла подходящую директорию
    (issue #47 R-04 — раньше молча возвращался несуществующий <parent>/tests/,
    что приводило к неинформативному FileNotFoundError глубже в стеке).

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

    return None


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
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))

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
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
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

    # safe_func/module_stem идут в generated-код БЕЗ repr() (это identifiers,
    # не строковые литералы) — валидируем их явно, иначе newline/`;` в
    # function_name (например, из meta.json) — code injection в wrapper-скрипт.
    if not safe_func.isidentifier():
        raise ValueError(f"Invalid function_name for code generation: {function_name!r}")
    if not module_stem.isidentifier():
        raise ValueError(f"Invalid module filename stem for code generation: {module_stem!r}")

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

# Явные импорты стандартных имён, которые могут встречаться в тест-блоке
# (issue #44 — заменяет прежние wildcard-импорты вида `from module import`
# + звёздочка). Список повторяет
# документированное публичное API каждого модуля (docs.python.org), а не
# производный dir() — это исключает служебные реэкспорты вроде
# functools.RLock/GenericAlias, не относящиеся к типичным тест-блокам.
# Делаются ПЕРЕД импортом из решения, чтобы имена решения имели приоритет
# (см. цикл dir(_mod) ниже — он перекрывает одноимённые stdlib-импорты).
from collections import (  # noqa: F401
    ChainMap,
    Counter,
    OrderedDict,
    UserDict,
    UserList,
    UserString,
    defaultdict,
    deque,
    namedtuple,
)
from datetime import (  # noqa: F401
    MAXYEAR,
    MINYEAR,
    UTC,
    date,
    datetime,
    time,
    timedelta,
    timezone,
    tzinfo,
)
from itertools import (  # noqa: F401
    accumulate,
    batched,
    chain,
    combinations,
    combinations_with_replacement,
    compress,
    count,
    cycle,
    dropwhile,
    filterfalse,
    groupby,
    islice,
    pairwise,
    permutations,
    product,
    repeat,
    starmap,
    takewhile,
    tee,
    zip_longest,
)
from functools import (  # noqa: F401
    cache,
    cached_property,
    cmp_to_key,
    lru_cache,
    partial,
    partialmethod,
    reduce,
    singledispatch,
    singledispatchmethod,
    total_ordering,
    update_wrapper,
    wraps,
)
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
