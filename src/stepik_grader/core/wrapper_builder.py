"""wrapper_builder.py — генерация wrapper-скриптов для function-mode запуска.

Архитектурный слой: Application / Business logic.
Отвечает ТОЛЬКО за генерацию исходного кода wrapper-скриптов как строк — не
исполняет их (это core/grader_core.py::run_single_test, через subprocess) и
не знает о тест-кейсах/режимах запуска (core/test_loader.py,
core/mode_detector.py).

Извлечён из grader_core.py (Issue #45 A-01).
"""

from __future__ import annotations

import pathlib

__all__: list[str] = []


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
