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
    "read_test_text",
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
    """Сгруппировать файлы-решения по ПАПКЕ, в которой они лежат.

    Ключ — путь папки относительно ``directory`` (``"."`` для файлов в самом
    ``directory``, ``"lesson1/step2"`` для вложенных); значение — список путей
    к файлам-решениям в ней. Имя файла на номер задачи не разбирается: ``task1.py``
    и ``task4_1.py`` из одной папки попадают в один ключ.

    issue #831 (PY-10): docstring обещал ключ ``taskN`` — группировки по номеру
    задачи здесь никогда не было, и потребитель, написанный по описанию, получал
    не те ключи.
    """
    grouped: dict[str, list[pathlib.Path]] = defaultdict(list)

    for root, _, files in os.walk(directory):
        for file_name in files:
            if is_solution_file(file_name):
                rel_folder = str(pathlib.Path(root).relative_to(directory, walk_up=True))
                grouped[rel_folder].append(pathlib.Path(root) / file_name)

    return dict(grouped)


def read_test_text(file_path: pathlib.Path) -> str:
    """Прочитать файл тест-кейсов терпимо к тому, чем его сохранил редактор.

    issue #939: файлы тестов приходят от аудитории курса, то есть из «Блокнота»
    и его родни. Два отклонения от «чистый UTF-8 без BOM» обрабатывались плохо:

    * кодировка не UTF-8 (cp1251) роняла прогон голым ``UnicodeDecodeError``, а
      в режиме 2 обрывала пачку и уносила результаты остальных решений;
    * BOM оставался первым символом строки, ``strip()`` его не срезает, поэтому
      маркер ``# TEST_1:`` переставал матчиться и набор молча становился пустым.

    Теперь непригодная кодировка не прерывает прогон: текст декодируется с
    заменой и сопровождается предупреждением с путём — вердикт по остальным
    задачам сохраняется, а причина названа. BOM срезается независимо от
    кодировки (``utf-8-sig`` помог бы только UTF-8, а ``encoding`` настраивается).
    """
    raw = file_path.read_bytes()
    try:
        text = raw.decode(ENCODING)
    except UnicodeDecodeError as exc:
        text = raw.decode(ENCODING, errors="replace")
        warnings.warn(
            f"{file_path}: файл тестов не в {ENCODING} ({exc.reason}, байт "
            f"{exc.object[exc.start : exc.start + 1]!r} в позиции {exc.start}) — "
            "нечитаемые символы заменены на «�», ожидаемый вывод может не "
            f"совпасть с фактическим. Пересохраните файл в {ENCODING}.",
            stacklevel=2,
        )
    return text.lstrip("﻿")


def load_text_lines(file_path: pathlib.Path) -> list[str]:
    """Загрузить текстовый файл и вернуть список строк без завершающих переносов.

    issue #843: разбор через ``split_output_lines`` — тем же правилом, что и
    фактический вывод решения. Прежний ``splitlines()`` резал ожидание ещё по
    восьми управляющим символам, и стороны сравнения расходились в трактовке
    одних и тех же байт.
    """
    return split_output_lines(read_test_text(file_path))


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
        input_text = read_test_text(input_file)
        output_text = read_test_text(output_file)
        input_blocks = _parse_testblock_file(input_text)
        output_blocks = _parse_testblock_file(output_text)
        if not input_blocks or not output_blocks:
            # issue #939: файлы формата 3 на месте, а блоков ноль — дальше
            # загрузчик молча уйдёт к форматам 1/2 и вернёт пустой набор,
            # то есть «NO TESTS» с кодом возврата 0 и без единой подсказки.
            # Единственная зацепка для пользователя — сказать, чего не хватило.
            empty = " и ".join(
                name
                for name, blocks in (("input.txt", input_blocks), ("output.txt", output_blocks))
                if not blocks
            )
            warnings.warn(
                f"{test_dir}: {empty} — маркеры вида `# TEST_1:` не найдены, "
                "ни одного блока не разобрано. Проверьте маркеры (и что файл "
                "сохранён без лишних символов в начале строки).",
                stacklevel=2,
            )
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
    _NUM_RE = re.compile(r"^\d+$")

    # issue #932/#959: порядок обхода задаётся явно, а не выдачей файловой
    # системы. Ключ (число, длина записи, имя) ставит каноничное `input_2.txt`
    # перед `input_02.txt`, поэтому при коллизии индекс достаётся ему, а не
    # тому, что первым вернул iterdir().
    def _order(name: str, digits: str) -> tuple[int, int, str]:
        return (int(digits), len(digits), name)

    fmt2_files = sorted(
        ((f, m.group(1)) for f in dir_path.iterdir() if (m := _INPUT_RE.match(f.name))),
        key=lambda pair: _order(pair[0].name, pair[1]),
    )
    fmt1_files = sorted(
        ((f, f.name) for f in dir_path.iterdir() if _NUM_RE.match(f.name)),
        key=lambda pair: _order(pair[0].name, pair[1]),
    )

    used: set[int] = set()
    unpaired: list[str] = []
    reindexed: list[str] = []

    def _claim(idx: int, source: str) -> int:
        """Занять индекс кейса; при коллизии — ближайший свободный.

        Кейс с занятым индексом раньше просто ложился рядом, и в отчёте
        оказывалось два «Теста 1» с разными ожиданиями (issue #932). Терять
        кейс нельзя — это ровно тот класс дефекта, от которого мы уходим,
        поэтому он получает свободный индекс, а пользователь — предупреждение.
        """
        if idx not in used:
            used.add(idx)
            return idx
        new_idx = idx
        while new_idx in used:
            new_idx += 1
        used.add(new_idx)
        reindexed.append(f"{source} → тест {new_idx} (индекс {idx} уже занят)")
        return new_idx

    for inp_file, digits in fmt2_files:
        # Пара ищется по той же буквальной записи номера: `input_02.txt` ↔
        # `expected_02.txt`. Откат на нормализованный номер оставлен ради
        # каталогов, собранных вручную вперемешку (issue #932, RUN-1-01).
        exp_file = dir_path / f"expected_{digits}.txt"
        if not exp_file.exists():
            exp_file = dir_path / f"expected_{int(digits)}.txt"
        if not exp_file.exists():
            unpaired.append(f"{inp_file.name} (нет expected_{digits}.txt)")
            continue
        cases.append(
            TestCase(
                index=_claim(int(digits), inp_file.name),
                input_lines=load_text_lines(inp_file),
                expected_lines=load_text_lines(exp_file),
            )
        )

    for inp_file, digits in fmt1_files:
        clue_file = dir_path / f"{inp_file.name}.clue"
        if not clue_file.exists():
            unpaired.append(f"{inp_file.name} (нет {inp_file.name}.clue)")
            continue

        # Читаем .type-файл если он существует
        type_file = dir_path / f"{inp_file.name}.type"
        test_type = "stdin"
        if type_file.exists():
            raw_type = type_file.read_text(encoding=ENCODING).strip()
            if raw_type == "function":
                test_type = "function"

        cases.append(
            TestCase(
                index=_claim(int(digits), inp_file.name),
                input_lines=load_text_lines(inp_file),
                expected_lines=load_text_lines(clue_file),
                test_type=test_type,
            )
        )

    # Молчаливый пропуск непарного файла — главный способ получить «всё
    # пройдено» на усечённом наборе (issue #932). Для формата 3 такое
    # предупреждение уже есть (#246), здесь оно доводится до форматов 1 и 2.
    if unpaired:
        warnings.warn(
            f"{test_dir}: файлы без пары пропущены — {', '.join(unpaired)}. "
            "Набор кейсов неполон: вердикт «все тесты пройдены» относится "
            "только к загруженным кейсам.",
            stacklevel=2,
        )
    if fmt2_files and fmt1_files:
        warnings.warn(
            f"{test_dir}: в одной папке лежат форматы 1 (N/N.clue) и "
            "2 (input_N.txt/expected_N.txt) — загружены оба, номера тестов в "
            "отчёте могут не совпадать с именами файлов.",
            stacklevel=2,
        )
    if reindexed:
        warnings.warn(
            f"{test_dir}: индексы кейсов пересеклись, номера сдвинуты — {', '.join(reindexed)}.",
            stacklevel=2,
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
