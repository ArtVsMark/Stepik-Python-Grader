"""mode_detector.py — определение режима запуска решения (stdin vs function).

Архитектурный слой: Application / Business logic.
Отвечает за:
  - AST-эвристики: is_function_only_solution, _is_python_code_block,
    _is_safe_constant;
  - чтение имени функции из meta.json / AST-фоллбэк;
  - единую точку детекции режима запуска (_detect_run_mode).

Не содержит логику загрузки тест-кейсов (core/test_loader.py) и не исполняет
решения (core/grader_core.py). Извлечён из grader_core.py (Issue #45 A-01).
"""

from __future__ import annotations

import ast
import json
import pathlib
from collections.abc import Iterable

from stepik_grader.config import CONFIG
from stepik_grader.core.storage import load_json_file

__all__ = [
    "is_function_only_solution",
]

ENCODING: str = CONFIG.encoding


def _is_safe_constant(node: ast.expr) -> bool:
    """Вернуть True, если узел — безопасное константное выражение без вызовов.

    Рекурсивно проверяет AST-узел: принимает литералы (Constant), арифметику
    из констант (BinOp, UnaryOp) и вложенные контейнеры (List/Tuple/Set/Dict).
    Отклоняет любые вызовы (Call), обращения к атрибутам (Attribute) и Name.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd | ast.Invert):
        return _is_safe_constant(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_constant(node.left) and _is_safe_constant(node.right)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_is_safe_constant(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_safe_constant(k) for k in node.keys if k is not None) and all(
            _is_safe_constant(v) for v in node.values
        )
    return False


def is_function_only_solution(file_content: str) -> bool:
    """Вернуть True, если файл содержит только определения функций (без точки входа).

    Критерии function-only файла:
      - Нет исполняемых выражений на верхнем уровне (print/input/любой Call)
      - Нет управляющих конструкций (for/while/if/with/try) на верхнем уровне
      - Есть хотя бы одна функция (def или async def)
      - Присваивания РАЗРЕШЕНЫ независимо от значения (date(...), list(), и т.п.)
        т.к. это типичный паттерн Stepik-шаблонов

    При SyntaxError возвращает False — файл будет запущен как скрипт.
    """
    try:
        tree = ast.parse(file_content)
    except SyntaxError:
        return False

    allowed_nodes = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,
        ast.Pass,
    )

    for node in tree.body:
        if not isinstance(node, allowed_nodes):
            # for/while/if/with/try и т.п. → это скрипт
            return False

        if isinstance(node, ast.Expr):
            # Разрешаем только строковые литералы (docstring модуля)
            # Любой вызов (print/input/my_func()) → это скрипт
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            return False

        # Присваивания разрешены всегда: date1 = date(...), MOD = 10**9+7, data = []
        # Это типичный паттерн Stepik-шаблонов — значение не проверяем

    return any(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) for node in tree.body)


def _is_python_code_block(block: str) -> bool:
    """Вернуть True, если block похож на Python-код (а не на stdin-данные).

    Эвристика: блок парсится как валидный Python AST и содержит вызов или
    присваивание. Это те конструкции, которых во входных данных не бывает:
    драйвер теста либо вызывает решение (``print(func(x))``), либо объявляет
    аргументы (``a = 5``) для legacy function-mode.

    Прежний критерий — «есть хотя бы один ``ast.Name``» — принимал за код
    любые слова-идентификаторы: ввод ``Anna\\nBob`` разбирался как два
    выражения-имени, кейс уезжал на function-маршрут и верное stdin-решение
    получало RE «function_name not found» (issue #784). Имена в данных
    встречаются сплошь и рядом (имена людей, города, названия), вызовы и
    присваивания — нет. Частный случай одного голого имени (``x``, ``print``),
    который прежде гасился отдельной веткой (issue #47 R-02), покрыт тем же
    критерием: имя само по себе — ни вызов, ни присваивание.

    Примеры:
        ``10\\n20\\n30``                  → False (голые константы)
        ``04.11.2021``                   → False (SyntaxError)
        ``Anna\\nBob``                    → False (слова-данные, не код)
        ``x``                            → False (голое имя, не вызов/присваивание)
        ``print(func(x))``               → True  (вызов функции)
        ``r = wins([...])\\nfor ...``     → True  (присваивание + вызов)
        ``chainmap = ChainMap({})``      → True  (присваивание)
        ``""``                           → False (пустой блок)
    """
    if not block.strip():
        return False
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.Call | ast.Assign | ast.AnnAssign | ast.AugAssign)
        for node in ast.walk(tree)
    )


def _block_invokes_solution(block: str, function_name: str | Iterable[str] | None) -> bool:
    """Вернуть True, если тест-блок сам вызывает решение и печатает результат.

    Это признак формата python-generation (format 3), где блок — полноценный
    драйвер теста (``print(func(...))``): его достаточно исполнить как есть
    (``_build_call_wrapper``). Legacy function-mode блоки, наоборот, только
    объявляют данные (``a = 5`` или голые ``5\\n10``) — вызов и печать собирает
    ``_build_function_wrapper``.

    Раньше маршрут выбирался по ``_is_python_code_block``, то есть по «похоже ли
    на Python-код». Присваивание ``a = 5`` содержит ``ast.Name`` в контексте
    Store и потому считалось кодом — legacy-блок уходил в call-wrapper, который
    исполнял присваивание и ничего не печатал (ложный WA). А голые значения
    уходили в function-wrapper, требующий именованных переменных (ложный RE).
    Признаком формата 3 является именно вывод, а не наличие имён (issue #622).

    Примеры:
        ``print(func(5))``          → True  (блок печатает сам)
        ``solve([1, 2])``           → True  (вызов функции решения)
        ``a = 5``                   → False (только данные)
        ``d1 = date(2020, 1, 1)``   → False (данные, хотя есть вызов date)
        ``5``                       → False (голый литерал)

    ``function_name`` принимает как одно имя, так и набор имён всех функций
    решения — вызов любой из них означает, что блок является драйвером.
    """
    if not block.strip():
        return False
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return False

    # issue #938 (RUN-2-02): сверяем со ВСЕМИ функциями решения, а не с одной.
    # Раньше сюда приходило имя первой функции по ``ast.walk``, поэтому решение
    # с ``def _helper`` перед ``def show`` при блоке ``show(5)`` не опознавалось
    # как драйвер: блок уходил в legacy-обёртку и падал ``NameError: name 'show'
    # is not defined``. Перестановка функций местами давала AC — то есть вердикт
    # зависел от порядка объявлений в файле пользователя.
    names = (
        set()
        if function_name is None
        else {function_name}
        if isinstance(function_name, str)
        else set(function_name)
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "print":
            return True
        if node.func.id in names:
            return True
    return False


def _read_meta_function_name(solution_path: pathlib.Path) -> str | None:
    """Прочитать function_name из meta.json рядом с файлом решения.

    Ищет meta.json в той же директории, что и solution_path.
    Возвращает None если файл не найден или поле отсутствует.
    """
    meta_path = solution_path.parent / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = load_json_file(meta_path)
        name = meta.get("function_name")
        return str(name) if name else None
    except (json.JSONDecodeError, OSError):
        return None


def _ast_function_names(solution_path: pathlib.Path) -> list[str]:
    """Вернуть имена всех функций верхнего уровня решения, в порядке объявления.

    issue #938 (RUN-2-02): нужно, чтобы «блок вызывает решение» проверялось по
    всем функциям, а не по первой попавшейся. Вложенные функции сюда не
    попадают намеренно — снаружи модуля они не вызываемы, поэтому драйвером
    теста быть не могут.
    """
    try:
        source = solution_path.read_bytes().decode(ENCODING, errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return []
    return [
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _ast_function_name(solution_path: pathlib.Path) -> str | None:
    """Парсит файл решения через ast и возвращает имя целевой функции (эвристика).

    Используется как fallback когда meta.json недоступен или function_name = None.

    issue #938: из нескольких функций верхнего уровня предпочитается первая
    **публичная** — имя с ведущим подчёркиванием по соглашению означает
    вспомогательную. Прежняя «первая по ``ast.walk``» выбирала ``_helper``,
    объявленный выше целевой функции, и legacy-обёртка вызывала не то.
    """
    try:
        # issue #792 (PY-03): байты + декодирование с заменой. Решение,
        # сохранённое в cp1251 (обычное дело на Windows-RU), роняло грейдинг
        # необработанным UnicodeDecodeError — он подкласс ValueError и мимо
        # `except (SyntaxError, OSError)` проходил насквозь. Нам здесь нужно
        # лишь имя функции: подставной символ в комментарии на AST не влияет,
        # а если исходник действительно не разбирается — вернём None, как и
        # для любого синтаксически неверного файла.
        source = solution_path.read_bytes().decode(ENCODING, errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return None
    top_level = [
        node.name for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    for name in top_level:
        if not name.startswith("_"):
            return name
    if top_level:
        return top_level[0]
    # Функций верхнего уровня нет — берём любую вложенную, как раньше: на
    # обёртку это всё равно не сработает, но поведение для странных решений
    # остаётся прежним, а не превращается в новый режим отказа.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return None


def _detect_run_mode(solution_path: pathlib.Path, test_dir: pathlib.Path) -> str:
    """Единая точка детекции режима запуска: "stdin" или "function".

    Стратегия определения (первый сработавший выигрывает):
      1. meta.json рядом с файлом: если function_name != None → "function"
      2. .type-файлы в test_dir: если хоть один содержит "function" → "function"
      3. AST-анализ файла решения через is_function_only_solution → "function"
      4. Иначе → "stdin"

    Вызывается один раз в run_tests(), результат передаётся в run_single_test().
    Это устраняет рассинхронизацию трёх источников истины.
    """
    # 1. meta.json
    if _read_meta_function_name(solution_path) is not None:
        return "function"

    # 2. .type-файлы
    if test_dir.is_dir():
        for type_file in test_dir.glob("*.type"):
            # issue #792 (PY-03): не-UTF8 в .type не должен ронять детекцию.
            raw = type_file.read_bytes().decode(ENCODING, errors="replace").strip()
            if raw == "function":
                return "function"

    # 3. AST-анализ файла решения
    try:
        # issue #792 (PY-03): то же декодирование с заменой — см. выше.
        file_content = solution_path.read_bytes().decode(ENCODING, errors="replace")
        if is_function_only_solution(file_content):
            return "function"
    except OSError:
        pass

    return "stdin"
