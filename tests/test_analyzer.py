"""Тесты для функций-анализаторов из test.py.

Покрывает:
    - is_function_only_solution  — AST-анализатор режима запуска
    - is_solution_file           — валидатор имён файлов-решений
"""

from __future__ import annotations

import textwrap

import pytest

from test import is_function_only_solution, is_solution_file

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

    def test_function_call_assignment_is_false(self) -> None:
        """Присваивание результата вызова функции на верхнем уровне → False."""
        code = textwrap.dedent("""\
            import sys
            data = sys.stdin.read()

            def solve():
                pass
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
            "task.py",        # базовое имя
            "task1.py",       # число без разделителя
            "task1_2.py",     # число_подномер (исторический стиль)
            "task_1.py",      # подчёркивание + номер (стиль at_first.py)
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
            "solution.py",       # не начинается с task
            "task.txt",          # не .py
            "task1.py.bak",      # лишнее расширение
            "Task_1.py",         # заглавная буква
            "task_1.py.py",      # двойное расширение
            "__init__.py",       # служебный файл
            "test_task1.py",     # префикс test_
            "my_task1.py",       # другой префикс
            "task1_.py",         # завершающее подчёркивание
            "",                  # пустая строка
            "task",              # без расширения
            "task_1_2_3.py",     # слишком много частей
        ],
    )
    def test_invalid_filenames(self, filename: str) -> None:
        assert is_solution_file(filename) is False, f"Ожидался False для {filename!r}"
