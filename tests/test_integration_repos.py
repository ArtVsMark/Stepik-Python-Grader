"""Интеграционные тесты грейдера на реальных данных трёх репозиториев
python-generation: Professional, OOP, Samurai.

Каждый кейс воспроизводит реальную задачу: input.txt/output.txt с блоками
`# TEST_N:` (формат 3) кладётся рядом с минимальным решением, после чего
прогоняется полный пайплайн `run_tests`. Данные input/output скопированы
дословно из соответствующих задач репозиториев, чтобы покрыть типы блоков,
не встречавшиеся в unit-тестах:

    - OOP Module_4.3.10  — класс Vector, for-цикл по массиву кортежей
    - OOP Module_7.1.23  — иерархия классов + issubclass(...)
    - Samurai Module_2.10.15 — кастомное исключение InvalidDateError в try/except
    - Professional Module_10.2.20 — filterfalse из itertools + import внутри блока
    - Samurai Module_13.1.1 — функция group_ranges, печать списка строк
"""

from __future__ import annotations

import pathlib

import pytest

from grader import run_tests


def _run_case(
    tmp_path: pathlib.Path, solution: str, input_txt: str, output_txt: str
) -> dict:
    """Разложить solution.py + input.txt + output.txt и прогнать грейдер."""
    (tmp_path / "solution.py").write_text(solution, encoding="utf-8")
    (tmp_path / "input.txt").write_text(input_txt, encoding="utf-8")
    (tmp_path / "output.txt").write_text(output_txt, encoding="utf-8")
    return run_tests(str(tmp_path / "solution.py"), str(tmp_path))


# ---------------------------------------------------------------------------
# OOP Module_4.3.10 — класс Vector
# ---------------------------------------------------------------------------

_VECTOR_SOLUTION = """import math


class Vector:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def abs(self):
        return math.sqrt(self.x**2 + self.y**2)
"""

_VECTOR_INPUT = """# INPUT DATA:

# TEST_1:
vector = Vector()

print(vector.x, vector.y)
print(vector.abs())

# TEST_2:
vector = Vector(3, 4)

print(vector.x, vector.y)
print(vector.abs())

# TEST_3:
array = [(76, 164), (12, 20)]

for x, y in array:
    vector = Vector(x, y)
    print(vector.abs())
"""

_VECTOR_OUTPUT = """# OUTPUT DATA:

# TEST_1:
0 0
0.0

# TEST_2:
3 4
5.0

# TEST_3:
180.75397644312005
23.323807579381203
"""


def test_oop_vector_class(tmp_path: pathlib.Path) -> None:
    r = _run_case(tmp_path, _VECTOR_SOLUTION, _VECTOR_INPUT, _VECTOR_OUTPUT)
    assert r["total"] == 3
    assert r["passed"] == 3
    assert r["failed"] == 0
    assert r["errors"] == 0


# ---------------------------------------------------------------------------
# OOP Module_7.1.23 — иерархия классов + issubclass
# ---------------------------------------------------------------------------

_VEHICLE_SOLUTION = """class Vehicle:
    pass


class LandVehicle(Vehicle):
    pass


class WaterVehicle(Vehicle):
    pass


class AirVehicle(Vehicle):
    pass


class Car(LandVehicle):
    pass


class Motorcycle(LandVehicle):
    pass


class Bicycle(LandVehicle):
    pass


class Propeller(AirVehicle):
    pass


class Jet(AirVehicle):
    pass
"""

_VEHICLE_INPUT = """# INPUT DATA:

# TEST_1:
print(issubclass(LandVehicle, Vehicle))
print(issubclass(WaterVehicle, Vehicle))
print(issubclass(AirVehicle, Vehicle))

# TEST_2:
print(issubclass(Car, WaterVehicle))
print(issubclass(Jet, LandVehicle))
"""

_VEHICLE_OUTPUT = """# OUTPUT DATA:

# TEST_1:
True
True
True

# TEST_2:
False
False
"""


def test_oop_issubclass_hierarchy(tmp_path: pathlib.Path) -> None:
    r = _run_case(tmp_path, _VEHICLE_SOLUTION, _VEHICLE_INPUT, _VEHICLE_OUTPUT)
    assert r["total"] == 2
    assert r["passed"] == 2
    assert r["failed"] == 0
    assert r["errors"] == 0


# ---------------------------------------------------------------------------
# Samurai Module_2.10.15 — кастомное исключение InvalidDateError
# ---------------------------------------------------------------------------

_DATE_SOLUTION = """from datetime import date


class InvalidDateError(Exception):
    pass


def parse_date(date_str):
    parts = date_str.split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise InvalidDateError('Некорректная дата')
    try:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        return date(year, month, day)
    except ValueError:
        raise InvalidDateError('Некорректная дата')
"""

_DATE_INPUT = """# INPUT DATA:

# TEST_1:
d = parse_date('11.01.1999')
print(d)
print(type(d))

# TEST_2:
try:
    print(parse_date('33.03.2007'))
except InvalidDateError as e:
    print(e)

# TEST_3:
try:
    print(parse_date('22/01/1985'))
except InvalidDateError as e:
    print(e)
"""

_DATE_OUTPUT = """# OUTPUT DATA:

# TEST_1:
1999-01-11
<class 'datetime.date'>

# TEST_2:
Некорректная дата

# TEST_3:
Некорректная дата
"""


def test_samurai_custom_exception(tmp_path: pathlib.Path) -> None:
    r = _run_case(tmp_path, _DATE_SOLUTION, _DATE_INPUT, _DATE_OUTPUT)
    assert r["total"] == 3
    assert r["passed"] == 3
    assert r["failed"] == 0
    assert r["errors"] == 0


# ---------------------------------------------------------------------------
# Professional Module_10.2.20 — filterfalse + import string внутри блока
# ---------------------------------------------------------------------------

_FILTERFALSE_SOLUTION = "from itertools import filterfalse  # noqa: F401\n"

_FILTERFALSE_INPUT = """# INPUT DATA:

# TEST_1:
objects = [0, 1, True, False, 17, []]

print(*filterfalse(None, objects))

# TEST_2:
numbers = (1, 2, 3, 4, 5)

print(*filterfalse(lambda x: x % 2 == 0, numbers))

# TEST_3:
import string
letters = string.ascii_letters
result = filterfalse(lambda char: ord(char) > 75, letters)
print(*result, sep=',')
"""

_FILTERFALSE_OUTPUT = """# OUTPUT DATA:

# TEST_1:
0 False []

# TEST_2:
1 3 5

# TEST_3:
A,B,C,D,E,F,G,H,I,J,K
"""


def test_professional_filterfalse(tmp_path: pathlib.Path) -> None:
    r = _run_case(
        tmp_path, _FILTERFALSE_SOLUTION, _FILTERFALSE_INPUT, _FILTERFALSE_OUTPUT
    )
    assert r["total"] == 3
    assert r["passed"] == 3
    assert r["failed"] == 0
    assert r["errors"] == 0


# ---------------------------------------------------------------------------
# Samurai Module_13.1.1 — group_ranges
# ---------------------------------------------------------------------------

_GROUP_SOLUTION = """def group_ranges(numbers):
    if not numbers:
        return []
    result = []
    start = end = numbers[0]
    for n in numbers[1:]:
        if n == end + 1:
            end = n
        else:
            result.append(f'{start}-{end}')
            start = end = n
    result.append(f'{start}-{end}')
    return result
"""

_GROUP_INPUT = """# INPUT DATA:

# TEST_1:
print(group_ranges([0, 1, 2, 3, 5, 6, 7, 9]))

# TEST_2:
print(group_ranges([]))

# TEST_3:
print(group_ranges([1, 5, 6, 7, 11]))
"""

_GROUP_OUTPUT = """# OUTPUT DATA:

# TEST_1:
['0-3', '5-7', '9-9']

# TEST_2:
[]

# TEST_3:
['1-1', '5-7', '11-11']
"""


def test_samurai_group_ranges(tmp_path: pathlib.Path) -> None:
    r = _run_case(tmp_path, _GROUP_SOLUTION, _GROUP_INPUT, _GROUP_OUTPUT)
    assert r["total"] == 3
    assert r["passed"] == 3
    assert r["failed"] == 0
    assert r["errors"] == 0


# ---------------------------------------------------------------------------
# Негативный контроль: грейдер обязан ловить расхождение вывода
# ---------------------------------------------------------------------------


def test_grader_detects_wrong_output(tmp_path: pathlib.Path) -> None:
    broken = """class Vector:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def abs(self):
        return 999
"""
    r = _run_case(tmp_path, broken, _VECTOR_INPUT, _VECTOR_OUTPUT)
    assert r["passed"] == 0
    assert r["failed"] > 0


@pytest.mark.parametrize(
    "block",
    [
        "vector = Vector(3, 4)\nprint(vector.x, vector.y)\nprint(vector.abs())",
        "print(issubclass(LandVehicle, Vehicle))",
        "try:\n    print(parse_date('33.03.2007'))\nexcept InvalidDateError as e:\n    print(e)",
        "import string\nletters = string.ascii_letters\nprint(*filterfalse(None, letters))",
        "print(group_ranges([0, 1, 2, 3, 5, 6, 7, 9]))",
    ],
)
def test_repo_blocks_detected_as_python_code(block: str) -> None:
    from grader import _is_python_code_block

    assert _is_python_code_block(block) is True
