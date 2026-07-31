"""test_loader.py — обнаружение файлов-решений и загрузка тест-кейсов.

Архитектурный слой: Application / Business logic.
Отвечает за:
  - обнаружение и классификацию файлов-решений (is_solution_file,
    find_all_solution_files, collect_grouped_files);
  - резолюцию директории тест-кейсов для файла решения (resolve_test_dir);
  - загрузку тест-кейсов трёх форматов (load_test_cases) и синхронизацию
    их test_type с детекцией режима на уровне файла (_apply_run_mode_override).

Не исполняет решения (core/grader_core.py) и не строит wrapper-скрипты
(core/wrapper_builder.py). Извлечён из grader_core.py (Issue #45 A-01).
"""

from __future__ import annotations

import os
import pathlib
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass, field

from stepik_grader.config import CONFIG
from stepik_grader.core.mode_detector import _detect_run_mode, _is_python_code_block
from stepik_grader.core.normalizers import split_output_lines
from stepik_grader.core.parsers import parse_testblock_file as _parse_testblock_file

__all__ = [
    "TestCase",
    "collect_grouped_files",
    "find_all_solution_files",
    "is_solution_file",
    "load_test_cases",
    "load_text_lines",
    "resolve_test_dir",
]

ENCODING: str = CONFIG.encoding

# Паттерн имён файлов-решений.  Матчит (fullmatch):
#   task.py        — базовый файл без номера
#   task1.py       — исторический стиль (номер задачи слитно)
#   task1_2.py     — номер задачи + номер решения
#   task_1.py      — стиль downloader.py (нет цифры перед _)
#   task_12.py     — то же, двузначный суффикс
# НЕ матчит: solution.py, main.py, task_v2.py (буквы после _)
_SOLUTION_FILE_RE = re.compile(r"task(?:\d+)?(?:_\d+)?\.py")


@dataclass
class TestCase:
    """Один тест-кейс: индекс, входные/ожидаемые строки и тип запуска (stdin/function)."""

    __test__ = False  # prevent pytest from collecting this as a test class
    index: int
    input_lines: list[str]
    expected_lines: list[str]
    test_type: str = field(default="stdin")  # "stdin" | "function"


def is_solution_file(file_name: str) -> bool:
    """Вернуть True, если имя файла соответствует шаблону решения.

    Принимаемые форматы:
        task.py, task1.py, task1_2.py   — исторический стиль
        task4_1.py, task7_3.py          — стиль из README (номер задачи + номер решения)
        task_1.py, task_2.py            — стиль, создаваемый downloader.py
    """
    return bool(_SOLUTION_FILE_RE.fullmatch(file_name))


def find_all_solution_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Рекурсивно собрать все файлы-решения в ``directory`` (отсортировано)."""
    scripts = []

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                scripts.append(pathlib.Path(root) / file_name)

    return sorted(scripts)


def collect_grouped_files(directory: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Сгруппировать файлы-решения по номеру задачи (ключ — ``taskN``)."""
    grouped: dict[str, list[pathlib.Path]] = defaultdict(list)

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                rel_folder = str(pathlib.Path(root).relative_to(directory, walk_up=True))
                grouped[rel_folder].append(pathlib.Path(root) / file_name)

    return dict(grouped)


def load_text_lines(file_path: pathlib.Path) -> list[str]:
    """Загрузить текстовый файл и вернуть список строк без завершающих переносов.

    issue #843: разбор через ``split_output_lines`` — тем же правилом, что и
    фактический вывод решения. Прежний ``splitlines()`` резал ожидание ещё по
    восьми управляющим символам, и стороны сравнения расходились в трактовке
    одних и тех же байт.
    """
    return split_output_lines(file_path.read_text(encoding=ENCODING))


def load_test_cases(test_dir: pathlib.Path) -> list[TestCase]:
    """Загрузить тест-кейсы из директории.

    Поддерживаются три формата:

    Формат 3 — python-generation/Professional (высший приоритет):
        tests/input.txt   — ВСЕ входные блоки с маркерами `# TEST_N:`
        tests/output.txt  — ВСЕ ожидаемые блоки с маркерами `# TEST_N:`
        Тип блока определяется автоматически: если блок — валидный Python-код
        с вызовом или присваиванием (`print(func(...))`, `a = 5`) → "function",
        иначе (числа, строки, слова-имена) → "stdin".

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
    dir_path = test_dir

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
            # issue #246 (F-07): zip(..., strict=False) below silently truncates to
            # the shorter list when input.txt/output.txt disagree on block count --
            # warn instead of quietly dropping test cases and risking a false-positive
            # "all tests pass" from a truncated set.
            if len(input_blocks) != len(output_blocks):
                warnings.warn(
                    f"{test_dir}: input.txt has {len(input_blocks)} test block(s) but "
                    f"output.txt has {len(output_blocks)} -- only the first "
                    f"{min(len(input_blocks), len(output_blocks))} block(s) are used, "
                    "the rest are silently dropped. Check the # TEST_N: markers in "
                    "both files match.",
                    stacklevel=2,
                )
            for i, (inp, out) in enumerate(zip(input_blocks, output_blocks, strict=False), 1):
                # Классификация — по выпрямленному блоку: с issue #783 разбор
                # сохраняет пробелы по краям строк, а для ast.parse ведущий
                # отступ — синтаксическая ошибка, и код-блок молча уехал бы в
                # stdin. Сами данные кейса остаются нетронутыми.
                test_type = "function" if _is_python_code_block(inp.strip()) else "stdin"
                cases.append(
                    TestCase(
                        index=i,
                        input_lines=split_output_lines(inp),
                        expected_lines=split_output_lines(out),
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
            input_lines = load_text_lines(inp_file)
            expected_lines = load_text_lines(exp_file)
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
            input_lines = load_text_lines(inp_file)
            expected_lines = load_text_lines(clue_file)

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


def resolve_test_dir(solution_path: pathlib.Path) -> pathlib.Path | None:
    """Вернуть путь к директории тест-кейсов для заданного файла решения, или
    None, если ни одна стратегия поиска не нашла подходящую директорию
    (issue #47 R-04 — раньше молча возвращался несуществующий <parent>/tests/,
    что приводило к неинформативному FileNotFoundError глубже в стеке).

    Стратегия поиска (первый найденный выигрывает):
      1. <parent>/tests/
      2. <parent>/<stem>/  (директория с именем = имени файла без расширения)
      3. <parent>/ (сам родительский каталог, если содержит .clue или input_*.txt)
    """
    p = solution_path.resolve()
    parent = p.parent
    stem = p.stem

    candidate_tests = parent / "tests"
    if candidate_tests.is_dir():
        return candidate_tests

    candidate_stem = parent / stem
    if candidate_stem.is_dir():
        return candidate_stem

    # python-generation: input.txt + output.txt рядом с решением или в родителе
    for candidate in (parent, parent.parent):
        if (candidate / "input.txt").exists() and (candidate / "output.txt").exists():
            return candidate

    for f in parent.iterdir():
        if f.suffix == ".clue" or re.match(r"^input_\d+\.txt$", f.name):
            return parent

    return None


def _apply_run_mode_override(
    cases: list[TestCase], solution_path: pathlib.Path, test_dir: pathlib.Path
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
