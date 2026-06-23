"""Тесты для функций загрузки данных из test.py.

Покрывает:
    - load_text_lines                  — загрузка файла построчно
    - load_text_lines_with_encoding    — загрузка + возврат кодировки
    - load_test_cases                  — чтение тест-кейсов из директории
    - resolve_input_path               — резолюция пути
    - find_all_solution_files          — поиск файлов-решений
    - collect_grouped_files            — группировка файлов по задачам
    - build_input_data                 — формирование stdin
"""

from __future__ import annotations

import pathlib

import pytest

from test import (
    TestCase,
    build_input_data,
    collect_grouped_files,
    find_all_solution_files,
    load_test_cases,
    load_text_lines,
    load_text_lines_with_encoding,
    resolve_input_path,
)


# ===========================================================================
# Вспомогательные фикстуры
# ===========================================================================


def _make_test_dir(base: pathlib.Path, cases: list[tuple[str, str]]) -> pathlib.Path:
    """Создаёт папку tests/ со структурой: {N} (stdin) + {N}.clue (stdout).

    cases: список кортежей (input_text, expected_text), нумерация с 1.
    """
    tests_dir = base / "tests"
    tests_dir.mkdir()
    for idx, (inp, exp) in enumerate(cases, start=1):
        (tests_dir / str(idx)).write_text(inp, encoding="utf-8")
        (tests_dir / f"{idx}.clue").write_text(exp, encoding="utf-8")
    return tests_dir


# ===========================================================================
# load_text_lines
# ===========================================================================


class TestLoadTextLines:
    """Загрузка файла построчно с авто-определением кодировки."""

    def test_simple_utf8(self, tmp_path: pathlib.Path) -> None:
        """Обычный UTF-8 файл читается и разбивается на строки."""
        f = tmp_path / "a.txt"
        f.write_text("hello\nworld", encoding="utf-8")
        assert load_text_lines(str(f)) == ["hello", "world"]

    def test_strips_trailing_newlines(self, tmp_path: pathlib.Path) -> None:
        """Хвостовые \\n обрезаются (.strip() перед splitlines)."""
        f = tmp_path / "b.txt"
        f.write_text("line1\nline2\n\n", encoding="utf-8")
        result = load_text_lines(str(f))
        assert result == ["line1", "line2"]

    def test_single_line(self, tmp_path: pathlib.Path) -> None:
        """Одна строка → список из одного элемента."""
        f = tmp_path / "c.txt"
        f.write_text("42", encoding="utf-8")
        assert load_text_lines(str(f)) == ["42"]

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        """Пустой файл → пустой список."""
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert load_text_lines(str(f)) == []

    def test_cyrillic_content(self, tmp_path: pathlib.Path) -> None:
        """Кириллица читается корректно."""
        f = tmp_path / "ru.txt"
        f.write_text("Привет\nМир", encoding="utf-8")
        assert load_text_lines(str(f)) == ["Привет", "Мир"]

    def test_multiline_numbers(self, tmp_path: pathlib.Path) -> None:
        """Типичный тестовый input: числа построчно."""
        f = tmp_path / "nums.txt"
        f.write_text("3\n1 2 3", encoding="utf-8")
        assert load_text_lines(str(f)) == ["3", "1 2 3"]

    def test_returns_list(self, tmp_path: pathlib.Path) -> None:
        """Возвращает именно list[str]."""
        f = tmp_path / "t.txt"
        f.write_text("x", encoding="utf-8")
        result = load_text_lines(str(f))
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)


# ===========================================================================
# load_text_lines_with_encoding
# ===========================================================================


class TestLoadTextLinesWithEncoding:
    """Загрузка + возврат кодировки."""

    def test_returns_tuple(self, tmp_path: pathlib.Path) -> None:
        """Fункция возвращает кортеж (list, str | None)."""
        f = tmp_path / "t.txt"
        f.write_text("hello", encoding="utf-8")
        result = load_text_lines_with_encoding(str(f))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_lines_match_load_text_lines(self, tmp_path: pathlib.Path) -> None:
        """Первый элемент совпадает с load_text_lines."""
        f = tmp_path / "t.txt"
        f.write_text("line1\nline2", encoding="utf-8")
        lines, _ = load_text_lines_with_encoding(str(f))
        assert lines == load_text_lines(str(f))

    def test_encoding_is_string_or_none(self, tmp_path: pathlib.Path) -> None:
        """Второй элемент — строка или None."""
        f = tmp_path / "t.txt"
        f.write_text("test", encoding="utf-8")
        _, enc = load_text_lines_with_encoding(str(f))
        assert enc is None or isinstance(enc, str)

    def test_utf8_encoding_detected(self, tmp_path: pathlib.Path) -> None:
        """Для UTF-8 файла кодировка содержит ascii или utf."""
        f = tmp_path / "t.txt"
        f.write_text("hello world", encoding="utf-8")
        _, enc = load_text_lines_with_encoding(str(f))
        assert enc is not None
        assert "utf" in enc.lower() or "ascii" in enc.lower()


# ===========================================================================
# load_test_cases
# ===========================================================================


class TestLoadTestCases:
    """Чтение тест-кейсов из директории структуры {N} / {N}.clue."""

    def test_single_case(self, tmp_path: pathlib.Path) -> None:
        """Один тест: возвращает список из одного TestCase."""
        tests_dir = _make_test_dir(tmp_path, [("3", "6")])
        cases = load_test_cases(tests_dir)
        assert len(cases) == 1
        assert cases[0].index == 1
        assert cases[0].input_lines == ["3"]
        assert cases[0].expected_lines == ["6"]

    def test_multiple_cases_sorted_by_index(self, tmp_path: pathlib.Path) -> None:
        """Несколько тестов: порядок по возрастанию индекса."""
        tests_dir = _make_test_dir(tmp_path, [("1", "2"), ("3", "6"), ("5", "10")])
        cases = load_test_cases(tests_dir)
        assert len(cases) == 3
        assert [c.index for c in cases] == [1, 2, 3]

    def test_input_multiline(self, tmp_path: pathlib.Path) -> None:
        """Многострочный input сохраняется корректно."""
        tests_dir = _make_test_dir(tmp_path, [("3\n1 2 3", "6")])
        cases = load_test_cases(tests_dir)
        assert cases[0].input_lines == ["3", "1 2 3"]

    def test_expected_multiline(self, tmp_path: pathlib.Path) -> None:
        """Многострочный expected сохраняется корректно."""
        tests_dir = _make_test_dir(tmp_path, [("in", "line1\nline2\nline3")])
        cases = load_test_cases(tests_dir)
        assert cases[0].expected_lines == ["line1", "line2", "line3"]

    def test_returns_test_case_instances(self, tmp_path: pathlib.Path) -> None:
        """Возвращает list[TestCase]."""
        tests_dir = _make_test_dir(tmp_path, [("x", "y")])
        cases = load_test_cases(tests_dir)
        assert all(isinstance(c, TestCase) for c in cases)

    def test_index_field_matches_file_number(self, tmp_path: pathlib.Path) -> None:
        """Поле index совпадает с номером файла (1, 2, 3 …)."""
        tests_dir = _make_test_dir(tmp_path, [("a", "b"), ("c", "d")])
        cases = load_test_cases(tests_dir)
        assert cases[0].index == 1
        assert cases[1].index == 2


# ===========================================================================
# resolve_input_path
# ===========================================================================


class TestResolveInputPath:
    """Резолюция пользовательского ввода в Path."""

    def test_relative_path_joined_to_base(self, tmp_path: pathlib.Path) -> None:
        """Относительный путь присоединяется к base_dir."""
        result = resolve_input_path(tmp_path, "subfolder/task.py")
        assert result == (tmp_path / "subfolder" / "task.py").resolve()

    def test_absolute_path_returned_as_is(self, tmp_path: pathlib.Path) -> None:
        """Абсолютный путь возвращается без изменений."""
        abs_path = str(tmp_path / "task.py")
        result = resolve_input_path(tmp_path, abs_path)
        assert result == pathlib.Path(abs_path)

    def test_strips_whitespace(self, tmp_path: pathlib.Path) -> None:
        """Пробелы в начале/конце удаляются (.strip())."""
        result = resolve_input_path(tmp_path, "  task.py  ")
        assert result == (tmp_path / "task.py").resolve()

    def test_returns_path_instance(self, tmp_path: pathlib.Path) -> None:
        """Возвращает экземпляр pathlib.Path."""
        result = resolve_input_path(tmp_path, "task.py")
        assert isinstance(result, pathlib.Path)

    def test_nested_relative_path(self, tmp_path: pathlib.Path) -> None:
        """Вложенный относительный путь разрешается полностью."""
        result = resolve_input_path(tmp_path, "a/b/c/task.py")
        assert result == (tmp_path / "a" / "b" / "c" / "task.py").resolve()


# ===========================================================================
# find_all_solution_files
# ===========================================================================


class TestFindAllSolutionFiles:
    """Рекурсивный поиск файлов-решений в директории."""

    def test_finds_task_files(self, tmp_path: pathlib.Path) -> None:
        """Находит файлы task_1.py, task_2.py."""
        (tmp_path / "task_1.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_2.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(str(tmp_path))
        names = [pathlib.Path(p).name for p in result]
        assert "task_1.py" in names
        assert "task_2.py" in names

    def test_skips_non_solution_files(self, tmp_path: pathlib.Path) -> None:
        """Игнорирует solution.py, README.md, __init__.py."""
        (tmp_path / "solution.py").write_text("pass", encoding="utf-8")
        (tmp_path / "README.md").write_text("", encoding="utf-8")
        (tmp_path / "__init__.py").write_text("", encoding="utf-8")
        result = find_all_solution_files(str(tmp_path))
        assert result == []

    def test_recursive_search(self, tmp_path: pathlib.Path) -> None:
        """Находит файлы в вложенных папках."""
        nested = tmp_path / "chapter1" / "task1"
        nested.mkdir(parents=True)
        (nested / "task_1.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(str(tmp_path))
        assert len(result) == 1
        assert "task_1.py" in result[0]

    def test_returns_sorted_list(self, tmp_path: pathlib.Path) -> None:
        """Результат отсортирован по алфавиту."""
        (tmp_path / "task_3.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_1.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_2.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(str(tmp_path))
        assert result == sorted(result)

    def test_empty_directory(self, tmp_path: pathlib.Path) -> None:
        """Пустая директория → пустой список."""
        assert find_all_solution_files(str(tmp_path)) == []


# ===========================================================================
# collect_grouped_files
# ===========================================================================


class TestCollectGroupedFiles:
    """Группировка файлов-решений по папке задачи."""

    def _make_structure(self, base: pathlib.Path) -> None:
        """task1/task_1.py, task1/task_2.py, task2/task_1.py."""
        (base / "task1").mkdir()
        (base / "task2").mkdir()
        (base / "task1" / "task_1.py").write_text("pass", encoding="utf-8")
        (base / "task1" / "task_2.py").write_text("pass", encoding="utf-8")
        (base / "task2" / "task_1.py").write_text("pass", encoding="utf-8")

    def test_groups_by_folder(self, tmp_path: pathlib.Path) -> None:
        """Файлы из разных папок попадают в отдельные группы."""
        self._make_structure(tmp_path)
        grouped = collect_grouped_files(tmp_path, tmp_path)
        assert len(grouped) == 2

    def test_correct_file_count_per_group(self, tmp_path: pathlib.Path) -> None:
        """Количество файлов в каждой группе соответствует ожидаемому."""
        self._make_structure(tmp_path)
        grouped = collect_grouped_files(tmp_path, tmp_path)
        counts = {k: len(v) for k, v in grouped.items()}
        assert any(c == 2 for c in counts.values())  # task1 — 2 файла
        assert any(c == 1 for c in counts.values())  # task2 — 1 файл

    def test_empty_directory_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """Пустая директория → пустой словарь."""
        assert collect_grouped_files(tmp_path, tmp_path) == {}

    def test_keys_are_relative_paths(self, tmp_path: pathlib.Path) -> None:
        """Ключи — относительные пути (не абсолютные)."""
        self._make_structure(tmp_path)
        grouped = collect_grouped_files(tmp_path, tmp_path)
        for key in grouped:
            assert not pathlib.Path(key).is_absolute()


# ===========================================================================
# build_input_data
# ===========================================================================


class TestBuildInputData:
    """Формирование stdin-строки для subprocess."""

    def _case(self, index: int, inp: list[str], exp: list[str]) -> TestCase:
        return TestCase(index=index, input_lines=inp, expected_lines=exp)

    def test_script_mode_returns_input_only(self) -> None:
        """Для скрипта stdin = только входные данные."""
        tc = self._case(1, ["5", "10"], ["15"])
        result = build_input_data(["print(1)"], is_function_only=False, test_case=tc)
        assert result == "5\n10"

    def test_function_mode_prepends_source(self) -> None:
        """Для function-only stdin = исходник + входные данные."""
        source = ["def solve(n):", "    return n * 2"]
        tc = self._case(1, ["5"], ["10"])
        result = build_input_data(source, is_function_only=True, test_case=tc)
        assert result.startswith("def solve(n):")
        assert result.endswith("5")

    def test_function_mode_source_and_input_separated_by_newline(self) -> None:
        """Исходник и input разделены \\n."""
        source = ["def f(x): return x"]
        tc = self._case(1, ["42"], ["42"])
        result = build_input_data(source, is_function_only=True, test_case=tc)
        assert "\n" in result
        lines = result.splitlines()
        assert lines[0] == "def f(x): return x"
        assert lines[-1] == "42"

    def test_script_empty_input(self) -> None:
        """Пустые input_lines → пустая строка."""
        tc = self._case(1, [], [])
        result = build_input_data(["print(1)"], is_function_only=False, test_case=tc)
        assert result == ""

    def test_function_multiline_input(self) -> None:
        """Многострочный input корректно добавляется после исходника."""
        source = ["def f(a, b): return a + b"]
        tc = self._case(1, ["3", "1 2 3"], ["6"])
        result = build_input_data(source, is_function_only=True, test_case=tc)
        lines = result.splitlines()
        assert lines[-2] == "3"
        assert lines[-1] == "1 2 3"
