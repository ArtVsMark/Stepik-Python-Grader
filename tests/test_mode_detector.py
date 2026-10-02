"""issue #646 (T1): ``_is_safe_constant`` покрыт по всем рекурсивным веткам.

Предикат (``core/mode_detector.py``) отличает безопасное константное выражение
— литералы, арифметику констант, вложенные контейнеры — от всего, что содержит
вызовы/имена/атрибуты. До аудита модуль-тест не касался его ни одной строкой,
хотя ``wrapper_builder`` полагается на него, решая, можно ли инъектировать
top-level присваивание решения без исполнения побочных эффектов.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from stepik_grader.core import mode_detector
from stepik_grader.core.mode_detector import _is_safe_constant


def _expr(src: str) -> ast.expr:
    """Единственное выражение исходника как AST-узел."""
    return ast.parse(src, mode="eval").body


@pytest.mark.parametrize(
    "src",
    [
        "5",
        "'text'",
        "3.14",
        "True",
        "None",
        "-5",  # UnaryOp USub над константой
        "+5",  # UnaryOp UAdd
        "~5",  # UnaryOp Invert
        "2 + 3",  # BinOp двух констант
        "10 ** 9 + 7",  # вложенный BinOp
        "-(2 + 3)",  # UnaryOp над BinOp
        "[1, 2, 3]",  # List
        "(1, 2)",  # Tuple
        "{1, 2}",  # Set
        "{'a': 1, 'b': 2}",  # Dict
        "[-1, [2, 3], {4}]",  # вложенные контейнеры
    ],
)
def test_accepts_constant_expressions(src: str) -> None:
    assert _is_safe_constant(_expr(src)) is True


@pytest.mark.parametrize(
    "src",
    [
        "x",  # Name
        "foo()",  # Call
        "date(2020, 1, 1)",  # Call с константными аргументами — всё равно вызов
        "a + 1",  # BinOp с именем в операнде
        "-x",  # UnaryOp над именем
        "[1, f()]",  # List с вызовом
        "(1, x)",  # Tuple с именем
        "{g()}",  # Set с вызовом
        "{'k': v()}",  # Dict: значение — вызов
        "{k: 1}",  # Dict: ключ — имя
        "{**d}",  # Dict-unpack: key is None, значение — имя
        "obj.attr",  # Attribute
        "1 if x else 2",  # IfExp — узел без обработки → return False
    ],
)
def test_rejects_calls_names_and_attributes(src: str) -> None:
    assert _is_safe_constant(_expr(src)) is False


class TestBrokenMetaJsonFallsBackQuietly:
    """issue #996 (PY-3-04): повреждённый meta.json — не повод не проверять решение.

    `meta.json` рядом с задачей — **подсказка** про имя функции, а не источник
    истины: при её отсутствии режим определяется эвристикой по AST. Значит и
    испорченная подсказка должна вести к эвристике, а не ронять прогон.
    """

    def _task(self, tmp_path: pathlib.Path, meta: bytes) -> pathlib.Path:
        (tmp_path / "meta.json").write_bytes(meta)
        solution = tmp_path / "task.py"
        solution.write_text("def solve(x):\n    return x\n", encoding="utf-8")
        return solution

    def test_non_utf8_byte_does_not_raise(self, tmp_path: pathlib.Path) -> None:
        """Не-UTF8 байт даёт UnicodeDecodeError — подкласс ValueError, не OSError.

        Прежний перехват ловил только JSONDecodeError и OSError, поэтому такой
        файл ронял ВЕСЬ прогон; в режиме 2 — вместе с остальными решениями.
        """
        solution = self._task(tmp_path, b'{"function_name": "\xff\xfe solve"}')

        assert mode_detector._read_meta_function_name(solution) is None

    def test_json_array_root_does_not_raise(self, tmp_path: pathlib.Path) -> None:
        """Корень-массив: load_json_file поднимает голый ValueError."""
        solution = self._task(tmp_path, b'["solve"]')

        assert mode_detector._read_meta_function_name(solution) is None

    def test_malformed_json_still_falls_back(self, tmp_path: pathlib.Path) -> None:
        """Битый JSON работал и раньше — проверяем, что не сломали."""
        solution = self._task(tmp_path, b"{not json")

        assert mode_detector._read_meta_function_name(solution) is None

    def test_valid_meta_is_still_read(self, tmp_path: pathlib.Path) -> None:
        """Главный риск расширенного перехвата — проглотить рабочий случай."""
        solution = self._task(tmp_path, b'{"function_name": "solve"}')

        assert mode_detector._read_meta_function_name(solution) == "solve"


class TestTypeFilesAreCaseLevel:
    """issue #996 (``MTX-5-02``): ``N.type`` — сигнал кейса N, а не всего набора.

    Детектор принимал любой ``.type`` с маркером за файловый режим: одного
    ``1.type`` хватало, чтобы весь набор уехал в function-режим, — и верное
    stdin-решение получало ``RE`` на каждом кейсе. Файл при этом мог просто
    пережить прошлую задачу в той же папке.
    """

    @staticmethod
    def _stdin_solution(tmp_path: pathlib.Path) -> pathlib.Path:
        """Обычное stdin-решение: по AST в function-режим не попадёт."""
        solution = tmp_path / "task.py"
        solution.write_text("print(int(input()) * 2)\n", encoding="utf-8")
        return solution

    @staticmethod
    def _case(test_dir: pathlib.Path, index: int, *, typed: bool = False) -> None:
        """Кейс формата 1: ``N`` + ``N.clue``, при ``typed`` — ещё и ``N.type``."""
        (test_dir / str(index)).write_text(f"{index}\n", encoding="utf-8")
        (test_dir / f"{index}.clue").write_text(f"{index * 2}\n", encoding="utf-8")
        if typed:
            (test_dir / f"{index}.type").write_text("function\n", encoding="utf-8")

    def test_partial_marker_does_not_switch_the_whole_set(self, tmp_path: pathlib.Path) -> None:
        """Помечен один кейс из двух — набор остаётся stdin."""
        solution = self._stdin_solution(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        self._case(test_dir, 1, typed=True)
        self._case(test_dir, 2)

        assert mode_detector._detect_run_mode(solution, test_dir) == "stdin"

    def test_every_case_marked_still_switches(self, tmp_path: pathlib.Path) -> None:
        """Помечены все кейсы — прежний вывод сохраняется."""
        solution = self._stdin_solution(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        self._case(test_dir, 1, typed=True)
        self._case(test_dir, 2, typed=True)

        assert mode_detector._detect_run_mode(solution, test_dir) == "function"

    def test_marker_without_format1_cases_still_switches(self, tmp_path: pathlib.Path) -> None:
        """Форматы 2 и 3 ``.type`` не читают — там файл относится к набору целиком."""
        solution = self._stdin_solution(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("5\n", encoding="utf-8")
        (test_dir / "expected_1.txt").write_text("10\n", encoding="utf-8")
        (test_dir / "1.type").write_text("function\n", encoding="utf-8")

        assert mode_detector._detect_run_mode(solution, test_dir) == "function"

    def test_stray_marker_without_its_case_is_not_a_signal(self, tmp_path: pathlib.Path) -> None:
        """``3.type`` без кейса 3 не переводит в function чужие кейсы."""
        solution = self._stdin_solution(tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        self._case(test_dir, 1)
        (test_dir / "3.type").write_text("function\n", encoding="utf-8")

        assert mode_detector._detect_run_mode(solution, test_dir) == "stdin"
