"""wrapper_builder.py — генерация wrapper-скриптов для function-mode запуска.

Архитектурный слой: Application / Business logic.
Отвечает ТОЛЬКО за генерацию исходного кода wrapper-скриптов как строк — не
исполняет их (это core/grader_core.py::run_single_test, через subprocess) и
не знает о тест-кейсах/режимах запуска (core/test_loader.py,
core/mode_detector.py).

Извлечён из grader_core.py (Issue #45 A-01).
"""

from __future__ import annotations

import ast
import pathlib

from stepik_grader.core.mode_detector import _is_safe_constant

__all__: list[str] = []

# Связывание аргументов для именованного стиля теста (`a = 5`): сперва по имени
# параметра, иначе — позиционно в порядке присваиваний блока (issue #622).
# Имена параметров и имена переменных теста часто расходятся (date1/date2 vs
# start/end), поэтому fallback обязателен, иначе получаем KeyError → ложный RE.
#
# issue #938 (DEV-2-01): совпадение по имени проверяется по `_assigned` — тому,
# что реально присвоил тест-блок, — а не по `_local_vars`. На уровне модуля
# обёртки `locals()` это её globals, куда уже импортированы `date`, `time`,
# `datetime`, `timedelta`, `Decimal`, `Fraction`. Решение `days_between(date,
# delta)` при блоке `d = "2020-01-01"` / `delta = 5` находило параметр `date`
# среди импортов, связывало его с классом stdlib вместо значения теста, и
# позиционный fallback не срабатывал вовсе — верное решение получало WA.
# `_local_vars` остаётся только источником ЗНАЧЕНИЙ, но не критерием выбора.
_NAMED_BINDING_TEMPLATE = """_sig = inspect.signature({func})
_local_vars = locals()
_param_names = list(_sig.parameters)
_assigned = {assigned}
if all(_n in _assigned for _n in _param_names):
    _args = [_local_vars[_n] for _n in _param_names]
else:
    _args = [_local_vars[_n] for _n in _assigned if _n in _local_vars][: len(_param_names)]"""


def _top_level_literal_args(block: str) -> list[str] | None:
    """Вернуть позиционные аргументы, если блок — только безопасные литералы.

    Возвращает список исходных фрагментов (``["5", "10"]``) для блока вида
    ``5\\n10``, где каждая строка — самостоятельное литеральное выражение.
    Возвращает ``None``, если блок не подходит под этот стиль (есть
    присваивания, вызовы, имена или синтаксическая ошибка).
    """
    if not block.strip():
        return None
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return None
    if not tree.body:
        return None

    args: list[str] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Expr) or not _is_safe_constant(stmt.value):
            return None
        args.append(ast.unparse(stmt.value))
    return args


def _assigned_names(block: str) -> list[str]:
    """Имена, которым блок присваивает значения на верхнем уровне, по порядку."""
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return []

    names: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            names.extend(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
    return names


def _build_function_wrapper(
    solution_path: pathlib.Path, input_data: str, function_name: str
) -> str:
    """Генерирует исходный код скрипта-обёртки для function-mode запуска.

    Печатает возвращённое значение, если оно не ``None``: функция, которая
    печатает результат сама, ничего не возвращает, и безусловный ``print``
    добавлял к её выводу строку ``'None'`` (issue #785).

    Сравнивается именно **напечатанное**, поэтому тип возврата в вердикте не
    участвует: ``return 5`` и ``return "5"`` дают один текст и оба получают
    ``AC`` (issue #996, ``MTX-4-03``). Это ограничение формата, а не упущение —
    ожидание задаётся текстовым файлом, и строка ``5`` о типе не говорит.
    Печатать ``repr()`` бессмысленно: у чисел, списков и словарей он совпадает
    со ``str()``, а строкам добавил бы кавычки и завернул бы все задачи со
    строковым ответом. Канон — `docs/use/configuration.md § Что именно
    сравнивается в function-режиме`.

    Поддерживает два стиля legacy-теста (issue #622):

    1. **Именованный** — блок объявляет переменные (``a = 5``). Блок
       исполняется, аргументы берутся из ``locals()`` по имени параметра;
       если имена не совпали (``date1/date2`` vs ``start/end``) — позиционно,
       в порядке присваиваний блока.
    2. **Позиционный** — блок состоит из голых литералов (``5\\n10``).
       Значения извлекаются статически из AST, сам блок не исполняется.

    Args:
        solution_path: абсолютный путь к файлу решения.
        input_data:    содержимое .type=function тест-кейса.
        function_name: имя функции для импорта.
    """
    abs_path = str(solution_path.resolve())
    solution_file = solution_path.name
    safe_input = input_data.strip()
    safe_func = function_name
    module_stem = solution_path.stem

    # safe_func/module_stem идут в generated-код БЕЗ repr() (это identifiers,
    # не строковые литералы) — валидируем их явно, иначе newline/`;` в
    # function_name (например, из meta.json) — code injection в wrapper-скрипт.
    if not safe_func.isidentifier():
        raise ValueError(f"Invalid function_name for code generation: {function_name!r}")
    if not module_stem.isidentifier():
        raise ValueError(f"Invalid module filename stem for code generation: {module_stem!r}")

    # Выбор стратегии связывания аргументов (issue #622).
    literal_args = _top_level_literal_args(safe_input)
    if literal_args is not None:
        # Позиционный стиль: блок — голые литералы. Значения извлечены из AST,
        # сам блок НЕ исполняем (иначе значения просто отбрасываются как
        # выражения-statement'ы, и связывать было бы нечего).
        setup_block = "# позиционный стиль: аргументы извлечены из теста статически"
        binding = f"_args = [{', '.join(literal_args)}]"
    else:
        # Именованный стиль: блок объявляет переменные — исполняем его.
        setup_block = safe_input
        binding = _NAMED_BINDING_TEMPLATE.format(
            func=safe_func, assigned=repr(_assigned_names(safe_input))
        )

    # repr() безопасно интерполирует путь (включая Windows-бэкслеши и спецсимволы).
    # Стандартные импорты — ДО sys.path.insert: иначе одноимённый файл рядом с
    # решением (например, datetime.py) окажется первым в sys.path и перекроет
    # настоящий stdlib-модуль (issue #244, F-05).
    return f"""import sys
import pathlib
import inspect

# Стандартные импорты, которые могут быть нужны в input_data
from datetime import date, time, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

# issue #992: каталог решения ищется рядом с обёрткой и только потом по
# исходному пути. Под --sandbox путь хоста внутри изоляции не существует, а
# решение кладётся рядом со скриптом; вне изоляции рядом ничего нет и работает
# исходный путь. Одна и та же обёртка обязана исполняться в обоих режимах —
# иначе изоляция становится вторым исполнителем со своим поведением.
_here = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_here if (_here / {solution_file!r}).exists()
                       else pathlib.Path({abs_path!r}).parent))

# Импортируем функцию из файла решения
from {module_stem} import {safe_func}

# Данные тест-кейса
{setup_block}

# Связывание аргументов. locals() снимается в переменную ДО списочного
# выражения: список сам по себе создаёт вложенную область видимости, и locals()
# внутри неё видит только эту область (PEP 709 / PEP 667) — на Python 3.11/3.13+
# это KeyError на модульных переменных; на 3.12 "случайно" работало.
{binding}

# Возврат печатается, только если он есть: функция, которая печатает сама и
# ничего не возвращает («напишите функцию, которая выводит …»), иначе получала
# бы к своему выводу лишнюю строку 'None' и WA на верном решении (issue #785).
_result = {safe_func}(*_args)
if _result is not None:
    print(_result)
"""


def _is_bare_expression(block: str) -> bool:
    """Блок — ровно одно выражение верхнего уровня, без присваиваний (issue #996).

    Такой блок вычисляет значение и **отбрасывает** его: `Vector(-5).length()`
    исполняется и не печатает ничего. Признак того, что результат надо напечатать
    самим — ровно как это делает function-обёртка (issue #785).

    Блок из нескольких инструкций сюда не попадает намеренно: там последняя
    строка чаще служебная, и печатать её значение — догадка, а не правило.
    """
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return False
    return len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)


def _build_call_wrapper(solution_path: pathlib.Path, call_block: str) -> str:
    """Генерирует скрипт, импортирующий все публичные имена из решения и
    исполняющий call_block как есть.

    Используется для python-generation function-call формата (Module_3.1, 3.3),
    где блок теста уже содержит полный вызов вида `print(func(args))`.
    inspect.signature НЕ используется — аргументы заданы в самом блоке.

    issue #996 (RUN-1-06): если блок — одно голое выражение (`Vector(-5).length()`
    без `print`), его значение печатается, когда оно не ``None``. Прежде такой
    блок исполнялся, результат отбрасывался, и верное решение получало ``WA`` с
    пустым `Actual` — при том, что function-обёртка в ровно том же положении
    результат печатает (issue #785). Вердикт зависел от того, каким из двух
    маршрутов режима пошёл кейс.

    Блок подставляется **дословно**, без ``ast.unparse``: обёртка не переписывает
    пользовательский тест, а только оборачивает его в присваивание.
    """
    abs_path = str(solution_path.resolve())
    solution_file = solution_path.name
    module_name = pathlib.Path(abs_path).stem

    if _is_bare_expression(call_block):
        # Скобки — чтобы многострочное выражение осталось одним выражением;
        # `print(...)` сюда тоже попадает и безвреден: он возвращает None.
        body = f"_result = (\n{call_block}\n)\nif _result is not None:\n    print(_result)"
    else:
        body = call_block

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

# issue #992: решение ищется рядом с обёрткой и только потом по исходному пути —
# под --sandbox путь хоста внутри изоляции не существует (см. legacy-обёртку).
import pathlib as _pathlib

_here = _pathlib.Path(__file__).resolve().parent
_beside = _here / {solution_file!r}
_solution_path = str(_beside) if _beside.exists() else {abs_path!r}
sys.path.insert(0, str(_pathlib.Path(_solution_path).parent))
_spec = importlib.util.spec_from_file_location({module_name!r}, _solution_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
# Импорт из решения идёт ПОСЛЕДНИМ — публичные имена решения
# перекрывают одноимённые из stdlib wildcard-импортов выше.
for _name in dir(_mod):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_mod, _name)

{body}
"""
