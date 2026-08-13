"""Тесты для функций загрузки данных из grader.py.

Покрывает:
    - load_text_lines                  — загрузка файла построчно
    - load_test_cases                  — чтение тест-кейсов из директории
    - find_all_solution_files          — поиск файлов-решений
    - collect_grouped_files            — группировка файлов по задачам
"""

from __future__ import annotations

import pathlib

from stepik_grader.grader import (
    TestCase,
    collect_grouped_files,
    find_all_solution_files,
    load_test_cases,
    load_text_lines,
    resolve_test_dir,
)

# ===========================================================================
# Вспомогательные фикстуры
# ===========================================================================


def _make_test_dir(base: pathlib.Path, cases: list[tuple[str, str]]) -> pathlib.Path:
    """Создаёт папку tests/ со структурой: input_{N}.txt + expected_{N}.txt.

    cases: список кортежей (input_text, expected_text), нумерация с 1.
    """
    tests_dir = base / "tests"
    tests_dir.mkdir()
    for idx, (inp, exp) in enumerate(cases, start=1):
        (tests_dir / f"input_{idx}.txt").write_text(inp, encoding="utf-8")
        (tests_dir / f"expected_{idx}.txt").write_text(exp, encoding="utf-8")
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
        assert load_text_lines(f) == ["hello", "world"]

    def test_strips_trailing_newlines(self, tmp_path: pathlib.Path) -> None:
        """Хвостовые \\n обрезаются (.rstrip перед splitlines)."""
        f = tmp_path / "b.txt"
        f.write_text("line1\nline2\n\n", encoding="utf-8")
        result = load_text_lines(f)
        assert result == ["line1", "line2", ""]

    def test_single_line(self, tmp_path: pathlib.Path) -> None:
        """Одна строка → список из одного элемента."""
        f = tmp_path / "c.txt"
        f.write_text("42", encoding="utf-8")
        assert load_text_lines(f) == ["42"]

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        """Пустой файл → пустой список."""
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert load_text_lines(f) == []

    def test_cyrillic_content(self, tmp_path: pathlib.Path) -> None:
        """Кириллица читается корректно."""
        f = tmp_path / "ru.txt"
        f.write_text("Привет\nМир", encoding="utf-8")
        assert load_text_lines(f) == ["Привет", "Мир"]

    def test_multiline_numbers(self, tmp_path: pathlib.Path) -> None:
        """Типичный тестовый input: числа построчно."""
        f = tmp_path / "nums.txt"
        f.write_text("3\n1 2 3", encoding="utf-8")
        assert load_text_lines(f) == ["3", "1 2 3"]

    def test_returns_list(self, tmp_path: pathlib.Path) -> None:
        """Возвращает именно list[str]."""
        f = tmp_path / "t.txt"
        f.write_text("x", encoding="utf-8")
        result = load_text_lines(f)
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)


# ===========================================================================
# load_test_cases
# ===========================================================================


class TestLoadTestCases:
    """Чтение тест-кейсов из директории структуры input_{N}.txt / expected_{N}.txt."""

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
# resolve_test_dir — поиск директории тестов для разных структур
# ===========================================================================


class TestResolveTestDir:
    """Резолюция директории тестов для разных раскладок папок."""

    def test_tests_subdir(self, tmp_path: pathlib.Path) -> None:
        """<parent>/tests/ имеет высший приоритет."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        assert resolve_test_dir(sol) == (tmp_path / "tests").resolve()

    def test_python_generation_input_output_alongside(self, tmp_path: pathlib.Path) -> None:
        """Format 3 (input.txt + output.txt) рядом с решением → родительская папка."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "input.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        (tmp_path / "output.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        assert resolve_test_dir(sol) == tmp_path.resolve()

    def test_python_generation_input_output_in_parent(self, tmp_path: pathlib.Path) -> None:
        """Format 3 на уровень выше решения (решение в подпапке)."""
        task_dir = tmp_path / "Module_3.1.20"
        task_dir.mkdir()
        sub = task_dir / "sub"
        sub.mkdir()
        sol = sub / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        (task_dir / "input.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        (task_dir / "output.txt").write_text("# TEST_1:\n1\n", encoding="utf-8")
        assert resolve_test_dir(sol) == task_dir.resolve()

    def test_clue_files_in_parent(self, tmp_path: pathlib.Path) -> None:
        """Legacy-формат: .clue-файлы в родительской папке решения."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "1").write_text("1", encoding="utf-8")
        (tmp_path / "1.clue").write_text("1", encoding="utf-8")
        assert resolve_test_dir(sol) == tmp_path.resolve()

    def test_returns_none_when_nothing_found(self, tmp_path: pathlib.Path) -> None:
        """No tests/ subfolder, no stem folder, no Format 3, no .clue/input_N.txt.

        Issue #47 R-04: previously fell through to a silent, non-existent
        <parent>/tests/ path instead of signalling failure explicitly.
        """
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        assert resolve_test_dir(sol) is None


# ===========================================================================
# find_all_solution_files
# ===========================================================================


class TestFindAllSolutionFiles:
    """Рекурсивный поиск файлов-решений в директории."""

    def test_finds_task_files(self, tmp_path: pathlib.Path) -> None:
        """Находит файлы task_1.py, task_2.py."""
        (tmp_path / "task_1.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_2.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(tmp_path)
        names = [p.name for p in result]
        assert "task_1.py" in names
        assert "task_2.py" in names

    def test_skips_non_solution_files(self, tmp_path: pathlib.Path) -> None:
        """Игнорирует не-Python и служебные файлы, даже когда решений в папке нет."""
        (tmp_path / "README.md").write_text("", encoding="utf-8")
        (tmp_path / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "conftest.py").write_text("", encoding="utf-8")
        (tmp_path / "test_something.py").write_text("", encoding="utf-8")
        (tmp_path / "setup.py").write_text("", encoding="utf-8")
        result = find_all_solution_files(tmp_path)
        assert result == []

    def test_last_submission_counts_when_no_task_files(self, tmp_path: pathlib.Path) -> None:
        """`solution.py` — решение, когда шаблонных файлов рядом нет.

        Его кладёт загрузчик: это последняя отправка ученика на Stepik, и
        таблица файлов задачи в `docs/use/configuration.md` обещает, что она
        участвует в сравнении решений. Прежний шаблон `task*.py` этому
        противоречил — на папке, где решение лежит перед глазами, грейдер
        отвечал «решений не найдено».
        """
        (tmp_path / "solution.py").write_text("pass", encoding="utf-8")

        result = find_all_solution_files(tmp_path)

        assert [p.name for p in result] == ["solution.py"]

    def test_last_submission_yields_to_task_pattern(self, tmp_path: pathlib.Path) -> None:
        """Рядом с `task*.py` последняя отправка в сравнение не попадает.

        Иначе скачанная задача сравнивала бы рабочий файл с его же копией:
        `solution.py` часто дословно повторяет отправленный `task1_1.py`.
        """
        (tmp_path / "task1_1.py").write_text("pass", encoding="utf-8")
        (tmp_path / "solution.py").write_text("pass", encoding="utf-8")

        result = find_all_solution_files(tmp_path)

        assert [p.name for p in result] == ["task1_1.py"]

    def test_recursive_search(self, tmp_path: pathlib.Path) -> None:
        """Находит файлы в вложенных папках."""
        nested = tmp_path / "chapter1" / "task1"
        nested.mkdir(parents=True)
        (nested / "task_1.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(tmp_path)
        assert len(result) == 1
        assert result[0].name == "task_1.py"

    def test_returns_sorted_list(self, tmp_path: pathlib.Path) -> None:
        """Результат отсортирован по алфавиту."""
        (tmp_path / "task_3.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_1.py").write_text("pass", encoding="utf-8")
        (tmp_path / "task_2.py").write_text("pass", encoding="utf-8")
        result = find_all_solution_files(tmp_path)
        assert result == sorted(result)

    def test_empty_directory(self, tmp_path: pathlib.Path) -> None:
        """Пустая директория → пустой список."""
        assert find_all_solution_files(tmp_path) == []


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
        grouped = collect_grouped_files(tmp_path)
        assert len(grouped) == 2

    def test_correct_file_count_per_group(self, tmp_path: pathlib.Path) -> None:
        """Количество файлов в каждой группе соответствует ожидаемому."""
        self._make_structure(tmp_path)
        grouped = collect_grouped_files(tmp_path)
        counts = {k: len(v) for k, v in grouped.items()}
        assert any(c == 2 for c in counts.values())
        assert any(c == 1 for c in counts.values())

    def test_empty_directory_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """Пустая директория → пустой словарь."""
        assert collect_grouped_files(tmp_path) == {}

    def test_keys_are_relative_paths(self, tmp_path: pathlib.Path) -> None:
        """Ключи — относительные пути (не абсолютные)."""
        self._make_structure(tmp_path)
        grouped = collect_grouped_files(tmp_path)
        for key in grouped:
            assert not pathlib.Path(key).is_absolute()
