"""stdlib_inventory.py — офлайн-инвентаризация официального Python/stdlib (issue #196).

Архитектурный слой: Domain (leaf — только stdlib, не тянет ``core/*`` и не
импортируется из него — DAG ацикличен, см. CLAUDE.md § Архитектурные
инварианты).

Строит **source-driven** инвентарь того, что предлагает официальный Python:
встроенные функции/классы (``builtins``), иерархию встроенных исключений
(рекурсивный обход ``BaseException``) и публичные члены курируемого набора
stdlib-модулей. Используется коэффициент полноты глоссария относительно
официального Python — см.
[`docs/glossary.md § Источники истины`](../../../docs/glossary.md#источники-истины-роли).

Инвентарь строится **офлайн** через интроспекцию running-интерпретатора
(``inspect``/``importlib``) — никаких сетевых запросов и никакого разбора
внешних сайтов (в т.ч. Glossary-Python, который не является эталоном
полноты). Курируемые модули **импортируются** (это часть stdlib, исполняемой
средой доверия), но пользовательский код здесь не запускается.
"""

from __future__ import annotations

import builtins
import importlib
import inspect
import sys
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "InventoryKind",
    "StdlibItem",
    "NOTABLE_STDLIB_MODULES",
    "NOTABLE_BUILTIN_TYPES",
    "build_stdlib_inventory",
]

InventoryKind = Literal["function", "class", "exception", "method"]

# Курируемые встроенные типы, чьи публичные методы инвентаризируются (issue
# #327). Это самый частый у новичков пласт (``str.split``, ``list.append``,
# ``dict.get``), которого builtins-сканер не видит — он собирает только сами
# классы (``str``, ``list``), но не их методы. qualname — вида ``str.split``.
NOTABLE_BUILTIN_TYPES: tuple[type, ...] = (
    str,
    bytes,
    bytearray,
    list,
    tuple,
    dict,
    set,
    frozenset,
    int,
    float,
    complex,
)

# Курируемый набор stdlib-модулей, часто встречающихся в решениях студентов и
# в глоссарии (issue #196). Список сознательно конечен и стабилен — расширять
# явным PR, а не автоматически (иначе инвентарь перестанет быть детерминированным
# по составу между запусками разных версий Python).
NOTABLE_STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "functools",
        "itertools",
        "collections",
        "collections.abc",
        "math",
        "re",
        "os",
        "os.path",
        "pathlib",
        "json",
        "datetime",
        "string",
        "statistics",
        "dataclasses",
        "typing",
        "io",
        "textwrap",
        "random",
        "copy",
        "operator",
        "enum",
        "abc",
        "contextlib",
    }
)


@dataclass(frozen=True)
class StdlibItem:
    """Одна сущность официального Python/stdlib (функция/класс/исключение).

    ``qualname`` — полное имя для поиска/дедупа (напр. ``functools.reduce``,
    ``ValueError``); для builtins — без префикса модуля, как пишут в коде.
    """

    qualname: str
    module: str
    kind: InventoryKind
    python_version: str

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, StdlibItem):
            return NotImplemented
        return self.qualname < other.qualname


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _classify_non_exception(obj: object) -> InventoryKind | None:
    """Классифицировать builtins/module-член как function/class (не exception).

    Исключения сюда не попадают — их собирает отдельный рекурсивный обход
    ``BaseException`` (``_exception_items``), чтобы не задваивать записи.
    """
    if inspect.isclass(obj):
        if issubclass(obj, BaseException):
            return None
        return "class"
    if inspect.isroutine(obj):
        return "function"
    return None


def _public_names(module: object) -> list[str]:
    exported = getattr(module, "__all__", None)
    if isinstance(exported, list | tuple):
        return sorted(str(name) for name in exported)
    return sorted(name for name in dir(module) if not name.startswith("_"))


def _builtins_items(version: str) -> list[StdlibItem]:
    items: list[StdlibItem] = []
    for name in sorted(dir(builtins)):
        if name.startswith("_"):
            continue
        obj = getattr(builtins, name)
        kind = _classify_non_exception(obj)
        if kind is None:
            continue
        items.append(
            StdlibItem(qualname=name, module="builtins", kind=kind, python_version=version)
        )
    return items


def _module_items(module_names: frozenset[str], version: str) -> list[StdlibItem]:
    items: list[StdlibItem] = []
    for module_name in sorted(module_names):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for name in _public_names(module):
            obj = getattr(module, name, None)
            kind = _classify_non_exception(obj)
            if kind is None:
                continue
            items.append(
                StdlibItem(
                    qualname=f"{module_name}.{name}",
                    module=module_name,
                    kind=kind,
                    python_version=version,
                )
            )
    return items


def _type_method_items(types: tuple[type, ...], version: str) -> list[StdlibItem]:
    """Публичные методы курируемых встроенных типов (``str.split``, ``dict.get``).

    Берём только вызываемые публичные атрибуты (``dir`` без ``_``-префикса) —
    data-дескрипторы (``int.numerator``, ``int.real`` и т.п.) отбрасываются,
    т.к. это не методы. ``kind="method"``, ``module="builtins"``.
    """
    items: list[StdlibItem] = []
    for tp in types:
        for name in sorted(dir(tp)):
            if name.startswith("_"):
                continue
            if not callable(getattr(tp, name, None)):
                continue
            items.append(
                StdlibItem(
                    qualname=f"{tp.__name__}.{name}",
                    module="builtins",
                    kind="method",
                    python_version=version,
                )
            )
    return items


def _all_exception_classes() -> list[type[BaseException]]:
    """Рекурсивно обойти иерархию ``BaseException`` среди уже загруженных классов.

    Видны только исключения из модулей, реально импортированных в процессе
    (builtins всегда, курируемые модули — после ``_module_items``), поэтому
    вызывать после импорта нужных модулей, а не до.
    """
    seen: set[type[BaseException]] = {BaseException}
    stack: list[type[BaseException]] = [BaseException]
    while stack:
        current = stack.pop()
        for sub in current.__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return sorted(seen, key=lambda cls: (cls.__module__, cls.__qualname__))


def _exception_items(version: str) -> list[StdlibItem]:
    items: list[StdlibItem] = []
    for cls in _all_exception_classes():
        module = cls.__module__ or "builtins"
        qualname = cls.__qualname__ if module == "builtins" else f"{module}.{cls.__qualname__}"
        items.append(
            StdlibItem(qualname=qualname, module=module, kind="exception", python_version=version)
        )
    return items


def build_stdlib_inventory(modules: frozenset[str] | None = None) -> list[StdlibItem]:
    """Построить детерминированный офлайн-инвентарь официального Python/stdlib.

    Args:
        modules: курируемый набор stdlib-модулей для сканирования публичных
            членов (по умолчанию — ``NOTABLE_STDLIB_MODULES``). Модули,
            которых нет в текущем окружении, пропускаются без ошибки.

    Returns:
        Список ``StdlibItem``, отсортированный по ``qualname``, без дублей.
        Исключения (``kind="exception"``) собираются рекурсивным обходом
        ``BaseException``; функции/классы (``kind`` в ``function``/``class``) —
        из ``builtins`` и курируемых модулей; методы встроенных типов
        (``kind="method"``, напр. ``str.split``) — из ``NOTABLE_BUILTIN_TYPES``.
    """
    target_modules = NOTABLE_STDLIB_MODULES if modules is None else modules
    version = _python_version()

    by_qualname: dict[str, StdlibItem] = {}
    for item in (
        *_builtins_items(version),
        *_module_items(target_modules, version),
        *_type_method_items(NOTABLE_BUILTIN_TYPES, version),
    ):
        by_qualname.setdefault(item.qualname, item)
    for item in _exception_items(version):
        by_qualname.setdefault(item.qualname, item)

    return sorted(by_qualname.values())
