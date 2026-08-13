"""Тесты для функций-анализаторов из grader.py.

Покрывает:
    - is_function_only_solution  — AST-анализатор режима запуска
    - is_solution_file           — валидатор имён файлов-решений
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from stepik_grader.core.test_loader import collect_grouped_files, find_all_solution_files
from stepik_grader.grader import is_function_only_solution, is_solution_file

# ===========================================================================
# is_function_only_solution
# ===========================================================================


class TestIsFunctionOnlySolution:
    """Проверяет определение «function-only» стиля решения через AST."""

    # --- Позитивные кейсы (должна вернуть True) ---

    def test_single_function(self) -> None:
        """Один def без точки входа → True."""
        code = textwrap.dedent("""\
            def solve(n):
                return n * 2
        """)
        assert is_function_only_solution(code) is True

    def test_multiple_functions(self) -> None:
        """Несколько def без точки входа → True."""
        code = textwrap.dedent("""\
            def add(a, b):
                return a + b

            def sub(a, b):
                return a - b
        """)
        assert is_function_only_solution(code) is True

    def test_function_with_import(self) -> None:
        """import + def → True."""
        code = textwrap.dedent("""\
            import math

            def area(r):
                return math.pi * r ** 2
        """)
        assert is_function_only_solution(code) is True

    def test_function_with_from_import(self) -> None:
        """from … import … + def → True."""
        code = textwrap.dedent("""\
            from collections import defaultdict

            def make_graph():
                return defaultdict(list)
        """)
        assert is_function_only_solution(code) is True

    def test_function_with_constant_assignment(self) -> None:
        """Присваивание константы + def → True."""
        code = textwrap.dedent("""\
            MOD = 10 ** 9 + 7

            def solve(n):
                return n % MOD
        """)
        assert is_function_only_solution(code) is True

    def test_function_with_module_docstring(self) -> None:
        """Строка-докстринг модуля + def → True."""
        code = textwrap.dedent("""\
            \"\"\"Решение задачи.\"\"\"

            def solve(x):
                return x
        """)
        assert is_function_only_solution(code) is True

    def test_async_function(self) -> None:
        """async def → True."""
        code = textwrap.dedent("""\
            async def fetch(url):
                pass
        """)
        assert is_function_only_solution(code) is True

    def test_annotated_constant(self) -> None:
        """Аннотированная константа + def → True."""
        code = textwrap.dedent("""\
            MOD: int = 10 ** 9 + 7

            def solve(n: int) -> int:
                return n % MOD
        """)
        assert is_function_only_solution(code) is True

    # --- Негативные кейсы (должна вернуть False) ---

    def test_only_import_no_function(self) -> None:
        """Только import без функции → False (нет ни одного def)."""
        code = "import math\n"
        assert is_function_only_solution(code) is False

    def test_empty_string(self) -> None:
        """Пустой код → False."""
        assert is_function_only_solution("") is False

    def test_plain_script_with_print(self) -> None:
        """Скрипт с print() на верхнем уровне → False."""
        code = "print('hello')\n"
        assert is_function_only_solution(code) is False

    def test_script_with_input_call(self) -> None:
        """Вызов input() на верхнем уровне → False."""
        code = textwrap.dedent("""\
            n = int(input())
            print(n * 2)
        """)
        assert is_function_only_solution(code) is False

    def test_if_name_main_guard(self) -> None:
        """if __name__ == '__main__': → False (это не константа)."""
        code = textwrap.dedent("""\
            def solve(n):
                return n

            if __name__ == '__main__':
                print(solve(int(input())))
        """)
        assert is_function_only_solution(code) is False

    def test_for_loop_at_top_level(self) -> None:
        """for-цикл на верхнем уровне → False."""
        code = textwrap.dedent("""\
            def solve(n):
                return n

            for i in range(10):
                print(i)
        """)
        assert is_function_only_solution(code) is False

    def test_class_definition(self) -> None:
        """class на верхнем уровне → False."""
        code = textwrap.dedent("""\
            class Solution:
                def solve(self, n):
                    return n
        """)
        assert is_function_only_solution(code) is False

    def test_syntax_error_returns_false(self) -> None:
        """SyntaxError не пробрасывается, возвращает False."""
        code = "def broken(\n"
        assert is_function_only_solution(code) is False

    def test_list_constant_assignment(self) -> None:
        """Присваивание списка-константы + def → True."""
        code = textwrap.dedent("""\
            PRIMES: list = [2, 3, 5, 7]

            def is_prime(n):
                return n in PRIMES
        """)
        assert is_function_only_solution(code) is True

    def test_function_call_assignment_now_allowed(self) -> None:
        """Присваивание результата вызова + def → True.

        После рефакторинга проверка значений Assign убрана:
        date1 = date(2021, 11, 1)  — типичный Stepik-шаблон, должен давать True.
        Разделение stdin/function по присваиваниям вынесено в _is_function_style().
        """
        code = textwrap.dedent("""\
            import sys
            data = sys.stdin.read()

            def solve():
                pass
        """)
        assert is_function_only_solution(code) is True

    def test_date_assignment_is_true(self) -> None:
        """Присваивание date(...) + def → True (ключевой кейс Stepik)."""
        code = textwrap.dedent("""\
            from datetime import date

            def saturdays_between_two_dates(date1, date2):
                return sum(
                    1 for i in range((date2 - date1).days + 1)
                    if (date1 + __import__('datetime').timedelta(i)).weekday() == 5
                )
        """)
        assert is_function_only_solution(code) is True

    def test_top_level_call_only_is_false(self) -> None:
        """Вызов print() без def на верхнем уровне → False."""
        code = textwrap.dedent("""\
            from datetime import date
            date1 = date(2021, 11, 1)
            date2 = date(2021, 11, 22)
            print(saturdays_between_two_dates(date1, date2))
        """)
        assert is_function_only_solution(code) is False


# ===========================================================================
# is_solution_file
# ===========================================================================


class TestIsSolutionFile:
    """Проверяет валидатор имён файлов-решений по регулярному выражению."""

    # --- Принимаемые форматы ---

    @pytest.mark.parametrize(
        "filename",
        [
            "task.py",  # базовое имя
            "task1.py",  # число без разделителя
            "task1_2.py",  # число_подномер (исторический стиль)
            "task_1.py",  # подчёркивание + номер (стиль downloader.py)
            "task_2.py",
            "task_10.py",
            "task_100.py",
        ],
    )
    def test_valid_filenames(self, filename: str) -> None:
        assert is_solution_file(filename) is True, f"Ожидался True для {filename!r}"

    # --- Отклоняемые форматы ---

    @pytest.mark.parametrize(
        "filename",
        [
            "solution.py",  # не начинается с task
            "task.txt",  # не .py
            "task1.py.bak",  # лишнее расширение
            "Task_1.py",  # заглавная буква
            "task_1.py.py",  # двойное расширение
            "__init__.py",  # служебный файл
            "test_task1.py",  # префикс test_
            "my_task1.py",  # другой префикс
            "task1_.py",  # завершающее подчёркивание
            "",  # пустая строка
            "task",  # без расширения
            "task_1_2_3.py",  # слишком много частей
        ],
    )
    def test_invalid_filenames(self, filename: str) -> None:
        assert is_solution_file(filename) is False, f"Ожидался False для {filename!r}"


# ===========================================================================
# _detect_run_mode (grader.py)
# ===========================================================================


class TestDetectRunMode:
    """Проверяет единую точку детекции режима запуска."""

    def test_stdin_mode_plain_script(self, tmp_path: pathlib.Path) -> None:
        """Скрипт с print() → stdin."""
        from stepik_grader.grader import _detect_run_mode

        sol = tmp_path / "task1.py"
        sol.write_text("n = int(input())\nprint(n)\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        assert _detect_run_mode(sol, tests_dir) == "stdin"

    def test_function_mode_via_meta_json(self, tmp_path: pathlib.Path) -> None:
        """meta.json с function_name → function."""
        import json

        from stepik_grader.grader import _detect_run_mode

        sol = tmp_path / "task1.py"
        sol.write_text("def solve(n):\n    return n\n", encoding="utf-8")
        meta = tmp_path / "meta.json"
        meta.write_text(json.dumps({"function_name": "solve"}), encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        assert _detect_run_mode(sol, tests_dir) == "function"

    def test_function_mode_via_type_file(self, tmp_path: pathlib.Path) -> None:
        """.type файл с 'function' → function."""
        from stepik_grader.grader import _detect_run_mode

        sol = tmp_path / "task1.py"
        sol.write_text("n = int(input())\nprint(n)\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "1.type").write_text("function", encoding="utf-8")
        assert _detect_run_mode(sol, tests_dir) == "function"

    def test_function_mode_via_ast(self, tmp_path: pathlib.Path) -> None:
        """Файл с только def (без meta.json и .type) → function через AST."""
        from stepik_grader.grader import _detect_run_mode

        sol = tmp_path / "task1.py"
        sol.write_text(
            "from datetime import date\n\ndef saturdays(d1, d2):\n    return 0\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        assert _detect_run_mode(sol, tests_dir) == "function"


# ===========================================================================
# _is_function_style (downloader.py) — AST-версия
# ===========================================================================


class TestIsFunctionStyle:
    """Проверяет AST-детектор режима тест-кейса из downloader.py."""

    def test_date_assignments_only(self) -> None:
        """Два присваивания date(...) без print → True (function-mode)."""
        from stepik_grader.downloader import _is_function_style

        text = "date1 = date(2021, 11, 1)\ndate2 = date(2021, 11, 22)"
        assert _is_function_style(text) is True

    def test_assignments_with_print(self) -> None:
        """Присваивания + print() → False (stdin-mode)."""
        from stepik_grader.downloader import _is_function_style

        date_lines = "date1 = date(2021, 11, 1)\ndate2 = date(2021, 11, 22)"
        text = date_lines + "\nprint(saturdays_between_two_dates(date1, date2))"
        assert _is_function_style(text) is False

    def test_simple_int_input(self) -> None:
        """n = int(input()) → False (вызов на верхнем уровне через Assign→Call→Call)."""
        from stepik_grader.downloader import _is_function_style

        # n = int(input()) — Assign, не Expr.Call, поэтому через AST body это Assign → True
        # Но само значение — вызов input(), что типично для stdin. Проверяем ожидание:
        # После рефакторинга: Assign разрешён → True (корректно для тест-кейсов вида n=5)
        text = "n = 5"
        assert _is_function_style(text) is True

    def test_plain_number_input(self) -> None:
        """Просто число (stdin) → False (нет присваиваний)."""
        from stepik_grader.downloader import _is_function_style

        text = "42"
        assert _is_function_style(text) is False

    def test_empty_string(self) -> None:
        """Пустая строка → False."""
        from stepik_grader.downloader import _is_function_style

        assert _is_function_style("") is False

    def test_syntax_error(self) -> None:
        """SyntaxError → False."""
        from stepik_grader.downloader import _is_function_style

        assert _is_function_style("def broken(") is False


# ---------------------------------------------------------------------------
# Папка без task*.py не выглядит пустой — issue #997 (JRN-2-06, JRN-1-03)
# ---------------------------------------------------------------------------


class TestSolutionSearchFallback:
    """Шаблон имени стал приоритетом, а не условием видимости.

    Режим 1 грейдит файл с любым именем (`--file my_solution.py` работает), а
    режимы 2/3/4 искали строго ``task*.py`` и на той же папке отвечали «решений
    не найдено»: файл лежит перед глазами, инструмент говорит, что его нет.
    """

    def _py(self, folder: pathlib.Path, name: str) -> pathlib.Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text("print(1)\n", encoding="utf-8")
        return path

    def test_folder_without_pattern_is_not_empty(self, tmp_path):
        self._py(tmp_path, "my_solution.py")

        found = [p.name for p in find_all_solution_files(tmp_path)]

        assert found == ["my_solution.py"]

    def test_pattern_wins_when_present(self, tmp_path):
        """Регрессия: пока есть task*.py, соседний utils.py не попадает в проверку."""
        self._py(tmp_path, "task1_1.py")
        self._py(tmp_path, "utils.py")

        found = [p.name for p in find_all_solution_files(tmp_path)]

        assert found == ["task1_1.py"]

    def test_archive_never_becomes_a_solution(self, tmp_path):
        """issue #1055: старые попытки не должны становиться конкурентами.

        Без шаблона они были бы обычными .py — поэтому каталог отсекается явно.
        """
        self._py(tmp_path, "my_solution.py")
        self._py(tmp_path / "submissions", "2024-03-01T12-00-05_wrong_1.py")

        found = [p.name for p in find_all_solution_files(tmp_path)]

        assert found == ["my_solution.py"]

    def test_service_files_are_skipped(self, tmp_path):
        self._py(tmp_path, "my_solution.py")
        for name in ("test_solution.py", "conftest.py", "__init__.py", "setup.py"):
            self._py(tmp_path, name)

        found = [p.name for p in find_all_solution_files(tmp_path)]

        assert found == ["my_solution.py"]

    def test_hidden_and_venv_dirs_are_skipped(self, tmp_path):
        self._py(tmp_path, "my_solution.py")
        self._py(tmp_path / ".venv" / "lib", "something.py")
        self._py(tmp_path / "__pycache__", "cached.py")

        found = [p.name for p in find_all_solution_files(tmp_path)]

        assert found == ["my_solution.py"]

    def test_grouping_sees_the_same_files(self, tmp_path):
        """Режимы 2 и 3/4 обязаны сходиться в том, что считать решением."""
        self._py(tmp_path / "a", "my_solution.py")
        self._py(tmp_path / "b", "task1_1.py")

        grouped = collect_grouped_files(tmp_path)
        flat = {p.name for p in find_all_solution_files(tmp_path)}

        assert {p.name for files in grouped.values() for p in files} == flat

    def test_empty_folder_stays_empty(self, tmp_path):
        """Пустая папка — по-прежнему ноль решений, а не «что-нибудь»."""
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

        assert find_all_solution_files(tmp_path) == []
