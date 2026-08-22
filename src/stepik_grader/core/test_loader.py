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

from stepik_grader.config import CONFIG, get_config
from stepik_grader.core.mode_detector import (
    _ast_class_names,
    _ast_function_names,
    _block_invokes_solution,
    _detect_run_mode,
    _is_python_code_block,
    _read_meta_function_name,
)
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

# issue #996 (PY-1-07, LNCH-3-03): снимок на момент импорта — ради фасада
# grader.py и внешнего кода, который на это имя ссылается. Внутри модуля он НЕ
# используется: кодировка читается `get_config()` в момент ВЫЗОВА, иначе
# `override_config(encoding=...)` и `--config` не действуют на уже
# импортированный загрузчик (то же правило, что для таймаута в grader_core).
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


# Каталоги, которые не содержат решений по определению: архив прошлых отправок
# (issue #1055), тест-кейсы, служебные и виртуальные окружения. Нужны обходу
# ниже — он умеет брать файлы вне шаблона ``task*.py``, и без этого списка в
# «решения» попали бы старые попытки из ``submissions/``.
_NON_SOLUTION_DIRS = frozenset({"submissions", "tests", "__pycache__", "venv", "node_modules"})

# Имена, которые никогда не являются решением ученика, даже когда шаблон снят.
_SERVICE_FILE_RE = re.compile(r"(?:test_.*|.*_test|conftest|__init__|setup)\.py")


def _is_hidden_or_service_dir(name: str) -> bool:
    """Каталог, в который заходить не нужно: служебный, скрытый или venv."""
    return name in _NON_SOLUTION_DIRS or name.startswith(".")


def _walk_python_files(directory: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Обойти ``directory``, отдав пары (папка, имя .py-файла).

    Служебные каталоги отсекаются на входе, а не после сбора: иначе обход
    ходил бы по ``.venv`` и ``__pycache__`` целиком ради результата, который
    всё равно будет отброшен.
    """
    found: list[tuple[pathlib.Path, str]] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not _is_hidden_or_service_dir(d)]
        for file_name in files:
            if file_name.endswith(".py") and not _SERVICE_FILE_RE.fullmatch(file_name):
                found.append((pathlib.Path(root), file_name))
    return found


def _solution_files_in(directory: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Файлы-решения в ``directory``: сначала по шаблону, иначе — любые .py.

    issue #997 (``JRN-2-06``, ``JRN-1-03``): режим 1 грейдит файл с любым именем
    (``--file solution.py`` работает), а режимы 2/3/4 искали строго ``task*.py``
    и на папке с ``my_solution.py`` отвечали «решений не найдено». Файл лежит
    перед глазами, инструмент говорит, что его нет.

    Шаблон не снят, а стал приоритетом: пока в папке есть ``task*.py``, поведение
    ровно прежнее — соседний ``utils.py`` в проверку не попадёт. Расширенный
    поиск включается только там, где иначе не нашлось бы **ничего**, то есть
    вместо пустого ответа.
    """
    files = _walk_python_files(directory)
    by_pattern = [(root, name) for root, name in files if is_solution_file(name)]
    return by_pattern or files


def find_all_solution_files(directory: pathlib.Path) -> list[pathlib.Path]:
    """Рекурсивно собрать все файлы-решения в ``directory`` (отсортировано).

    Приоритет — имена по шаблону ``task*.py``; если таких нет, берутся любые
    ``.py`` кроме служебных (issue #997, см. :func:`_solution_files_in`).
    """
    return sorted(root / name for root, name in _solution_files_in(directory))


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

    # Тот же приоритет, что и в find_all_solution_files: группировка обязана
    # видеть ровно те же файлы, иначе режимы 2 и 3/4 разошлись бы в том, что
    # считать решением.
    for root, file_name in _solution_files_in(directory):
        rel_folder = str(root.relative_to(directory, walk_up=True))
        grouped[rel_folder].append(root / file_name)

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
    encoding = get_config().encoding
    raw = file_path.read_bytes()
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        text = raw.decode(encoding, errors="replace")
        warnings.warn(
            f"{file_path}: файл тестов не в {encoding} ({exc.reason}, байт "
            f"{exc.object[exc.start : exc.start + 1]!r} в позиции {exc.start}) — "
            "нечитаемые символы заменены на «�», ожидаемый вывод может не "
            f"совпасть с фактическим. Пересохраните файл в {encoding}.",
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

    Поддерживаются три формата. Они **складываются**, а не вытесняют друг
    друга: формат 3 идёт первым и получает номера 1..N, кейсы форматов 1/2
    встают следом с продолжением нумерации, а о смешении пользователь узнаёт
    предупреждением (issue #996, MTX-4-01 — прежде формат 3 выигрывал целиком,
    и решение получало «OK 1/1» на наборе, урезанном до одного кейса).

    Формат 3 — python-generation/Professional (идёт первым):
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
            # issue #48 R-03: файлы форматов 1/2 рядом с input.txt/output.txt
            # нельзя молча игнорировать — автор ручного формата 1, которому
            # downloader.py позже положил input.txt, гадал бы, почему его .clue
            # не действуют.
            #
            # issue #917 (RUN-2-05): текст по-русски — предупреждение обязано
            # доходить до пользователя, а не только существовать в коде.
            #
            # issue #996 (MTX-4-01): и не «предупредить и всё равно выбросить».
            # Формат 3 выигрывал целиком: кейсы форматов 1/2 исчезали, а
            # решение получало «OK 1/1» с кодом возврата 0 — то есть зелёный
            # вердикт на наборе, урезанном до одного кейса. Теперь формат 3
            # идёт ПЕРВЫМ (приоритет сохранён — за ним номера 1..N), а
            # остальные кейсы догружаются следом. Ровно так уже сосуществуют
            # форматы 1 и 2 между собой, см. предупреждение в конце функции.
            if any(
                f.suffix == ".clue" or re.match(r"^input_\d+\.txt$", f.name)
                for f in dir_path.iterdir()
                if f not in (input_file, output_file)
            ):
                warnings.warn(
                    f"{test_dir}: рядом с input.txt/output.txt лежат файлы форматов "
                    "1 (N/N.clue) и/или 2 (input_N.txt) — сначала идут кейсы "
                    "формата 3, остальные загружены следом, поэтому номера тестов "
                    "в отчёте могут не совпадать с именами файлов.",
                    stacklevel=2,
                )
            # issue #246 (F-07): zip(..., strict=False) ниже молча обрезает набор
            # по более короткому списку, когда input.txt и output.txt расходятся в
            # числе блоков — предупреждаем, вместо того чтобы тихо потерять кейсы и
            # выдать ложное «все тесты пройдены» на усечённом наборе.
            if len(input_blocks) != len(output_blocks):
                warnings.warn(
                    f"{test_dir}: в input.txt {len(input_blocks)} блок(ов), а в "
                    f"output.txt — {len(output_blocks)}: загружены только первые "
                    f"{min(len(input_blocks), len(output_blocks))}, остальные "
                    "отброшены. Сверьте маркеры `# TEST_N:` в обоих файлах.",
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
                        # issue #996 (MTX-4-01): индекс берётся у общего
                        # нумератора, а не ставится напрямую. Формат 3 идёт
                        # первым и получает 1..N; кейсы форматов 1/2 встанут
                        # следом, а не поверх — иначе в отчёте было бы два
                        # «Теста 1» с разными ожиданиями.
                        index=_claim(i, f"блок # TEST_{i}:"),
                        input_lines=split_output_lines(inp),
                        expected_lines=split_output_lines(out),
                        test_type=test_type,
                    )
                )

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
            # issue #987 (REV-1-02): читаем тем же терпимым путём, что и сами
            # тест-кейсы. Голый `read_text` ронял загрузку набора на первом же
            # не-UTF8 байте, хотя соседний `load_text_lines` то же отклонение
            # переживает, — и BOM от «Блокнота» оставлял `﻿function`,
            # которое `strip()` не срезает: кейс типа `function` молча
            # становился `stdin`, то есть верное решение получало WA.
            raw_type = read_test_text(type_file).strip()
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

    # issue #938 (RUN-2-01): синхронизация работала только в одну сторону, и
    # блок формата 3 вида `x = 5` / `y = 7` оставался «function» просто потому,
    # что похож на Python-код (`_is_python_code_block` считает присваивание
    # кодом). Вызывать при этом нечего: решение — обычный stdin-скрипт без
    # единого `def`, и верное решение получало `RE function_name not found`,
    # а при наличии вспомогательной функции — трейсбек внутрь неё.
    #
    # Понижаем осторожно, по двум условиям сразу, иначе лечение хуже болезни:
    #
    # * блок НЕ является драйвером — не печатает сам и не вызывает ничего из
    #   решения. Блок с `print(...)` исполним как есть, каким бы ни было
    #   решение, и трогать его нельзя;
    # * в решении нет ни одного вызываемого имени верхнего уровня. Классы тут
    #   так же важны, как функции: решение с одним лишь `class Vector` и
    #   блоком `vector = Vector()` / `print(vector.abs())` — обычная задача
    #   ООП-курса, и первый вариант этой проверки, смотревший только на `def`,
    #   ронял такие кейсы в stdin-маршрут (поймано интеграционными тестами на
    #   реальных задачах трёх репозиториев).
    if _read_meta_function_name(solution_path) is not None:
        return cases
    callables = {*_ast_function_names(solution_path), *_ast_class_names(solution_path)}
    if callables:
        return cases
    for case in cases:
        if case.test_type == "function" and not _block_invokes_solution(
            "\n".join(case.input_lines).strip(), callables
        ):
            case.test_type = "stdin"
    return cases
