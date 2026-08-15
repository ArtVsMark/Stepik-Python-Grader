"""tests/test_import_dag.py — граф импортов пакета остаётся DAG без циклов, а
канонические leaf-модули не тянут ничего из проекта (issue #410, инвариант A6).

CLAUDE.md § «Архитектурные инварианты» фиксирует два правила, которые до сих пор
держались только на код-ревью и docs/dev/architecture.md, но ничем не проверялись:

1. **DAG без циклов** — новые импорты не создают циклических зависимостей между
   модулями ``stepik_grader``. Цикл на уровне *загрузки модуля* — это реальный
   ``ImportError`` в проде, а не косметика.
2. **Leaf-модули** — ``core/storage.py``, ``core/normalizers.py``,
   ``core/glossary.py``, ``atomic_io.py`` не импортируют ничего из проекта
   (только stdlib).

Модель рёбер — консервативная и совпадает с семантикой загрузки CPython:

* учитываются только импорты, исполняемые **при загрузке модуля** — импорты в
  теле функций/методов отложены (ими как раз легитимно разрывают циклы, напр.
  ``core/sandbox/__init__.py`` и ``pytest_plugin.py``), а блок
  ``if TYPE_CHECKING:`` при рантайме не исполняется вовсе;
* ``from pkg import submodule`` даёт ребро к самому ``pkg.submodule`` (реальная
  зависимость), а не к пакету ``pkg`` — иначе фасадный ``__init__`` (реэкспорт
  подмодулей) порождал бы ложные самоциклы ``submodule → pkg → submodule``.

Для leaf-проверки, наоборот, учитываются **все** импорты (в т.ч. ленивые и
TYPE_CHECKING): инвариант «не импортирует ничего из проекта» — абсолютный.
"""

from __future__ import annotations

import ast
import functools
import pathlib
import re

import pytest

_SRC_ROOT = pathlib.Path(__file__).parent.parent / "src"
_PKG = "stepik_grader"
_PKG_ROOT = _SRC_ROOT / _PKG

# CLAUDE.md § «Архитектурные инварианты», п.2 — эти модули leaf по контракту.
# ``atomic_io`` (issue #551) и ``db`` (issue #552) — общие top-level инфра-leaf'ы
# (атомарный JSON-писатель и SQLite-коннектор): живут вне ``core/``, чтобы
# независимые от core подпакеты (``glossary/``) могли ими пользоваться, не создавая
# ребра ``glossary → core`` (ADR-0011); сами — stdlib-only.
_LEAF_MODULES = (
    "stepik_grader.core.storage",
    "stepik_grader.core.normalizers",
    "stepik_grader.core.glossary",
    "stepik_grader.atomic_io",
    "stepik_grader.db",
    # ``mtime_cache`` (issue #996, ARCH-3-01) — по той же причине top-level: кеш
    # нужен ``rules/json_provider``, а подпакеты ``core/`` не импортируют. Пока
    # модуль лежал в ``core/``, единственным способом им воспользоваться было
    # ребро ``rules → core`` — ровно то, которое инвариант ниже запрещает.
    "stepik_grader.mtime_cache",
    # ``stdio_encoding`` (issue #1108) — по той же причине top-level: переключатель
    # вывода на UTF-8 нужен всем точкам входа и скриптам стенда, а раньше жил в
    # ``cli/options`` и тянул за собой половину CLI.
    "stepik_grader.stdio_encoding",
)


def _module_name(path: pathlib.Path) -> str:
    """FQN модуля по пути файла (``.../core/storage.py`` → ``stepik_grader.core.storage``)."""
    rel = path.relative_to(_SRC_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _current_package(path: pathlib.Path) -> str:
    """Пакет модуля для разрешения относительных импортов.

    Для ``__init__.py`` пакет — это сам модуль (полный FQN); для обычного
    модуля — его родитель.
    """
    name = _module_name(path)
    if path.name == "__init__.py":
        return name
    return name.rsplit(".", 1)[0] if "." in name else name


def _is_type_checking(test: ast.expr) -> bool:
    """``TYPE_CHECKING`` / ``typing.TYPE_CHECKING`` в условии ``if``."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _runtime_import_nodes(body: list[ast.stmt]) -> list[ast.Import | ast.ImportFrom]:
    """Импорт-узлы, исполняемые при загрузке модуля (top-level, вне функций/классов).

    Спускается в ``if``/``try``/``with``/``for``/``while`` (их тела исполняются при
    импорте), но НЕ в тела функций/классов (отложенные импорты) и НЕ в
    ``if TYPE_CHECKING:`` (при рантайме не исполняется).
    """
    found: list[ast.Import | ast.ImportFrom] = []
    for node in body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.append(node)
        elif isinstance(node, ast.If):
            if _is_type_checking(node.test):
                found.extend(_runtime_import_nodes(node.orelse))  # else-ветка исполняется
            else:
                found.extend(_runtime_import_nodes(node.body))
                found.extend(_runtime_import_nodes(node.orelse))
        elif isinstance(node, ast.Try):
            found.extend(_runtime_import_nodes(node.body))
            for handler in node.handlers:
                found.extend(_runtime_import_nodes(handler.body))
            found.extend(_runtime_import_nodes(node.orelse))
            found.extend(_runtime_import_nodes(node.finalbody))
        elif isinstance(node, ast.With | ast.AsyncWith):
            found.extend(_runtime_import_nodes(node.body))
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            found.extend(_runtime_import_nodes(node.body))
            found.extend(_runtime_import_nodes(node.orelse))
        # FunctionDef / AsyncFunctionDef / ClassDef — не спускаемся (отложено).
    return found


def _resolve_from_target(path: pathlib.Path, node: ast.ImportFrom) -> str:
    """FQN модуля в ``from ... import`` (разрешает относительные ``.``/``..``)."""
    if node.level == 0:
        return node.module or ""
    base_parts = _current_package(path).split(".")
    if node.level > 1:  # level=1 — текущий пакет; каждый лишний уровень — на родителя выше
        base_parts = base_parts[: -(node.level - 1)]
    base = ".".join(base_parts)
    return f"{base}.{node.module}" if node.module else base


def _project_targets(
    path: pathlib.Path, node: ast.Import | ast.ImportFrom, modules: set[str]
) -> set[str]:
    """Проектные модули-цели одного импорт-узла (пустое множество для stdlib/сторонних)."""
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in modules:  # import stepik_grader.core.storage [as x]
                targets.add(alias.name)
        return targets
    resolved = _resolve_from_target(path, node)
    if not resolved.startswith(_PKG):
        return targets
    for alias in node.names:
        submodule = f"{resolved}.{alias.name}"
        if submodule in modules:  # from pkg import submodule → ребро к submodule
            targets.add(submodule)
        elif resolved in modules:  # from module import name → ребро к module
            targets.add(resolved)
    return targets


def _iter_module_files() -> list[pathlib.Path]:
    return sorted(_PKG_ROOT.rglob("*.py"))


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _build_import_graph() -> dict[str, set[str]]:
    """Граф загрузочных зависимостей: module → {проектные модули, импортируемые при загрузке}."""
    files = _iter_module_files()
    modules = {_module_name(p) for p in files}
    graph: dict[str, set[str]] = {}
    for path in files:
        name = _module_name(path)
        edges: set[str] = set()
        for node in _runtime_import_nodes(_parse(path).body):
            edges |= _project_targets(path, node, modules)
        edges.discard(name)  # само-петля от `import pkg` в pkg/__init__ — не цикл
        graph[name] = edges
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Первый найденный цикл как список узлов (``a → b → a``) либо ``None``."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for neighbor in sorted(graph.get(node, ())):
            if color[neighbor] == WHITE:
                cycle = visit(neighbor)
                if cycle is not None:
                    return cycle
            elif color[neighbor] == GRAY:
                return stack[stack.index(neighbor) :] + [neighbor]
        stack.pop()
        color[node] = BLACK
        return None

    for start in sorted(graph):
        if color[start] == WHITE:
            cycle = visit(start)
            if cycle is not None:
                return cycle
    return None


def test_import_graph_has_no_cycles() -> None:
    """DAG-инвариант (CLAUDE.md): загрузочные импорты не образуют циклов."""
    graph = _build_import_graph()
    cycle = _find_cycle(graph)
    assert cycle is None, (
        "Обнаружен цикл импортов (нарушение DAG-инварианта, CLAUDE.md § "
        "«Архитектурные инварианты», п.1):\n  "
        + " → ".join(cycle or ())
        + "\nРазорвите цикл: перенесите импорт в тело функции (ленивый) или "
        "вынесите общий код в отдельный leaf-модуль."
    )


@pytest.mark.parametrize("leaf", _LEAF_MODULES)
def test_leaf_module_has_no_project_imports(leaf: str) -> None:
    """Leaf-инвариант (CLAUDE.md п.2): каждый leaf из ``_LEAF_MODULES`` — только stdlib.

    Учитываются ВСЕ импорты (в т.ч. ленивые и TYPE_CHECKING): «не импортирует
    ничего из проекта» — абсолютное правило, а не только про загрузку.
    """
    files = _iter_module_files()
    modules = {_module_name(p) for p in files}
    by_name = {_module_name(p): p for p in files}
    path = by_name[leaf]

    offenders: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            offenders |= _project_targets(path, node, modules)
    offenders.discard(leaf)

    assert not offenders, (
        f"{leaf} — leaf-модуль (CLAUDE.md § «Архитектурные инварианты», п.2) и не "
        "должен импортировать ничего из проекта, но импортирует: " + ", ".join(sorted(offenders))
    )


def test_glossary_package_does_not_import_core() -> None:
    """ADR-0011: подпакет ``glossary/`` не зависит от ``core/``.

    Ровно это правило и позволило перенести доменные правила базы из
    ``web/glossary_adapter`` в ``glossary/taxonomy``/``glossary/lookup``
    (issue #830-соседний #831, ARCH-06): домен обязан оставаться пригодным для
    CLI и для внешнего экспорта, а не тянуть за собой рантайм грейдера. Общая
    инфраструктура для обоих подпакетов живёт в top-level leaf'ах
    (``atomic_io``, ``db``) — именно чтобы это ребро не появилось.

    Учитываются ВСЕ импорты, включая ленивые: обход через локальный импорт
    внутри функции — тот самый способ, которым такое ребро обычно и заводят.
    """
    files = _iter_module_files()
    modules = {_module_name(p) for p in files}
    violations: dict[str, list[str]] = {}
    for path in files:
        name = _module_name(path)
        if not name.startswith("stepik_grader.glossary"):
            continue
        offenders: set[str] = set()
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                offenders |= {
                    t for t in _project_targets(path, node, modules) if ".core" in f".{t}"
                }
        if offenders:
            violations[name] = sorted(offenders)
    assert not violations, (
        "glossary/ импортирует core/ — ребро, которого ADR-0011 не допускает "
        "(общее выносится в top-level leaf: atomic_io, db):\n"
        + "\n".join(f"  {mod}: {', '.join(off)}" for mod, off in violations.items())
    )


def test_cycle_detector_catches_synthetic_cycle() -> None:
    """Guard-the-guard: детектор реально ловит цикл (иначе тест выше — пустой)."""
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}}) is not None
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": set()}) is None


def test_import_graph_is_non_trivial() -> None:
    """Санити: граф действительно построен (модули найдены и рёбра извлечены)."""
    graph = _build_import_graph()
    assert len(graph) > 50, "Ожидались десятки модулей — путь к пакету сломан?"
    assert sum(len(v) for v in graph.values()) > 50, "Рёбра не извлеклись — сломан парсер импортов?"


# ---------------------------------------------------------------------------
# Boundary-guard web↔core (issue #549, ADR-0010): web-слой касается ядра только
# через ПУБЛИЧНУЮ поверхность, а исполнительное grade-ядро — только через фасад
# web/grading. Три структурные защиты + guard-the-guard.
# ---------------------------------------------------------------------------

# Пакеты ядра, к которым web ходит только по публичной поверхности.
_CORE_PKG_PREFIXES = ("stepik_grader.core", "stepik_grader.glossary", "stepik_grader.rules")

# Модули исполнительного ядра, реэкспортируемые фасадом ``web/grading.py``. Их
# ПРЯМОЙ импорт разрешён только в самом фасаде и в адаптерах (сервисный слой,
# ADR-0010); прочие web-модули берут эти примитивы из ``web.grading``.
_GRADE_CORE_MODULES = frozenset(
    {
        "stepik_grader.core.cache",
        "stepik_grader.core.grader_core",
        "stepik_grader.core.microbench_runner",
        "stepik_grader.core.reporter",
        "stepik_grader.core.runner",
        "stepik_grader.core.test_loader",
        "stepik_grader.core.tracer",
    }
)


def _web_files() -> list[pathlib.Path]:
    return sorted((_PKG_ROOT / "web").rglob("*.py"))


def _is_core_pkg(module: str) -> bool:
    return any(module == p or module.startswith(p + ".") for p in _CORE_PKG_PREFIXES)


@functools.cache
def _module_public_names(module: str) -> frozenset[str] | None:
    """Имена из ``__all__`` core-модуля; ``None`` — если ``__all__`` не объявлен.

    Читается статически (AST), без импорта модуля: guard обязан работать на файлах,
    а не на загруженном пакете.
    """
    path = _PKG_ROOT.parent / (module.replace(".", "/") + ".py")
    if not path.is_file():
        path = _PKG_ROOT.parent / module.replace(".", "/") / "__init__.py"
    if not path.is_file():
        return None
    for node in ast.walk(_parse(path)):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List | ast.Tuple)
        ):
            return frozenset(
                e.value for e in node.value.elts if isinstance(e, ast.Constant) and e.value
            )
    return None


def _non_public_core_imports(path: pathlib.Path, modules: set[str]) -> list[str]:
    """Импортируемые из core/glossary/rules НЕПУБЛИЧНЫЕ имена.

    Непубличное — это либо ``_``-префикс, либо имя, которого нет в ``__all__``
    целевого модуля, когда тот его объявил. Второе — суть issue #830 (ARCH-05):
    ``grader_core`` намеренно вывел ``SIMILAR_THRESHOLD``/``TIMEOUT_SECONDS`` из
    ``__all__`` (issue #52 Q-03), но прежний guard считал их публичными, потому
    что смотрел только на подчёркивание.

    Импорт самого ПОДМОДУЛЯ (``from stepik_grader.core import history``) —
    не импорт имени: пакеты ядра ``__all__`` не объявляют и не обязаны.
    """
    offenders: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_target(path, node)
            if not _is_core_pkg(resolved):
                continue
            public = _module_public_names(resolved)
            for alias in node.names:
                if alias.name.startswith("_"):
                    offenders.append(f"{resolved}.{alias.name}")
                elif f"{resolved}.{alias.name}" in modules:
                    continue  # подмодуль, а не имя из поверхности
                elif public is not None and alias.name not in public:
                    offenders.append(f"{resolved}.{alias.name}")
        elif isinstance(node, ast.Import):
            offenders += [
                a.name
                for a in node.names
                if _is_core_pkg(a.name) and a.name.rsplit(".", 1)[-1].startswith("_")
            ]
    return offenders


def _core_module_locals(path: pathlib.Path, modules: set[str]) -> dict[str, str]:
    """Локальные имена, связанные с core-МОДУЛЕМ (для детекта ``._private``-доступа).

    ``from stepik_grader.core import grader_core`` → {"grader_core": "...grader_core"};
    ``import ...grader_core as gc`` → {"gc": "...grader_core"}. Имена-НЕ-модули
    (``from ...grader_core import run_tests``) не связываются — ``run_tests`` не в
    ``modules``.
    """
    binds: dict[str, str] = {}
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_target(path, node)
            if not _is_core_pkg(resolved):
                continue
            for alias in node.names:
                submodule = f"{resolved}.{alias.name}"
                if submodule in modules:  # alias — реальный core-подмодуль
                    binds[alias.asname or alias.name] = submodule
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules and _is_core_pkg(alias.name):
                    binds[alias.asname or alias.name.split(".")[0]] = alias.name
    return binds


def _private_core_attr_access(path: pathlib.Path, modules: set[str]) -> list[str]:
    """Доступ к приватным атрибутам импортированных core-модулей (``grader_core._RUNNER``)."""
    locals_map = _core_module_locals(path, modules)
    offenders: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            val = node.value
            if isinstance(val, ast.Name) and val.id in locals_map:
                offenders.append(f"{val.id}.{node.attr}")
    return offenders


def _direct_grade_core_imports(path: pathlib.Path) -> list[str]:
    """Прямые импорты grade-core модулей (в обход ``web/grading``), включая ленивые."""
    offenders: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom):
            if _resolve_from_target(path, node) in _GRADE_CORE_MODULES:
                offenders.append(_resolve_from_target(path, node))
        elif isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name in _GRADE_CORE_MODULES]
    return offenders


def test_web_does_not_import_private_core_names() -> None:
    """ADR-0010: web импортирует из ядра только то, что ядро объявило в ``__all__``."""
    modules = {_module_name(p) for p in _iter_module_files()}
    violations = {
        _module_name(p): off for p in _web_files() if (off := _non_public_core_imports(p, modules))
    }
    assert not violations, (
        "web импортирует непубличные core-имена — `_`-приватные или выведенные из "
        "`__all__` целевого модуля (нарушение публичной границы, ADR-0010 / "
        "issue #549, #830):\n"
        + "\n".join(f"  {mod}: {', '.join(off)}" for mod, off in violations.items())
    )


def test_web_does_not_touch_private_core_attributes() -> None:
    """ADR-0010: web не разыменовывает приватные атрибуты core-модулей (``mod._X``)."""
    modules = {_module_name(p) for p in _iter_module_files()}
    violations = {
        _module_name(p): off for p in _web_files() if (off := _private_core_attr_access(p, modules))
    }
    assert not violations, (
        "web обращается к приватным атрибутам core-модулей (напр. `grader_core._RUNNER` — "
        "используйте публичный `run_spec`, ADR-0010 / issue #549):\n"
        + "\n".join(f"  {mod}: {', '.join(off)}" for mod, off in violations.items())
    )


def test_web_grade_core_only_via_grading_facade() -> None:
    """ADR-0010: grade-ядро импортируется в web только через ``web/grading`` (и адаптеры)."""
    violations = {}
    for path in _web_files():
        if path.name == "grading.py" or path.name.endswith("_adapter.py"):
            continue  # фасад и сервисный слой (адаптеры) — легитимно
        if off := _direct_grade_core_imports(path):
            violations[_module_name(path)] = off
    assert not violations, (
        "web-модуль импортирует grade-ядро напрямую, минуя фасад `web.grading` "
        "(ADR-0010 / issue #549):\n"
        + "\n".join(f"  {mod}: {', '.join(off)}" for mod, off in violations.items())
    )


def test_router_reaches_core_only_through_adapters() -> None:
    """issue #830 (ARCH-07): слой маршрутов не импортирует ядро напрямую.

    ``api_routes.py`` — тонкая обёртка над адаптерами и viewmodels
    (docs/dev/architecture.md), собственной логики не добавляет. Прямой импорт
    ``core.*`` означает, что часть работы (валидация лимита, чтение настроек)
    осела в HTTP-слое и разъехалась с CLI, который делает то же в другом месте.
    """
    router = _PKG_ROOT / "web" / "api_routes.py"
    direct = sorted(
        {
            resolved
            for node in ast.walk(_parse(router))
            if isinstance(node, ast.ImportFrom)
            and _is_core_pkg(resolved := _resolve_from_target(router, node))
        }
        | {
            a.name
            for node in ast.walk(_parse(router))
            if isinstance(node, ast.Import)
            for a in node.names
            if _is_core_pkg(a.name)
        }
    )
    assert not direct, (
        "web/api_routes.py импортирует ядро напрямую вместо адаптера "
        "(issue #830, ARCH-07): " + ", ".join(direct)
    )


def test_package_facades_do_not_reexport_privates() -> None:
    """issue #830 (ARCH-08): ``__init__`` пакета не реэкспортирует приватные имена.

    Реэкспорт ``_Handler``/``_case_view`` ради удобства тестов закрепляет
    приватное как де-факто публичный API: символ уже не переименовать, не сверив
    фасад, — то есть архитектурную поверхность начинают диктовать тесты. Приватное
    берётся из своего модуля (``web.server``, ``web.viewmodels``) напрямую.

    Приватный ПОДМОДУЛЬ (``core/sandbox/_linux.py``) — не реэкспорт имени:
    пакет собирает свои реализации, наружу они не торчат.

    ``stepik_grader.cli`` — известное исключение (issue #903): та же болезнь, но
    ~90 обращений в тестах, чинится отдельной задачей. Список сокращается, а не
    растёт: новый пакет в нём появляться не должен.
    """
    known_debt = {"stepik_grader.cli"}
    modules = {_module_name(p) for p in _iter_module_files()}
    violations: dict[str, list[str]] = {}
    for path in _iter_module_files():
        if path.name != "__init__.py" or (pkg := _module_name(path)) in known_debt:
            continue
        privates = [
            alias.name
            for node in ast.walk(_parse(path))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if (alias.asname or alias.name).startswith("_")
            and alias.name != "annotations"
            and f"{_resolve_from_target(path, node)}.{alias.name}" not in modules
        ]
        if privates:
            violations[pkg] = privates
    assert not violations, (
        "фасад пакета реэкспортирует приватные имена (issue #830, ARCH-08):\n"
        + "\n".join(f"  {mod}: {', '.join(names)}" for mod, names in violations.items())
    )


def test_boundary_guard_catches_synthetic_violations(tmp_path: pathlib.Path) -> None:
    """Guard-the-guard: детекторы реально ловят приватный импорт / атрибут / прямой grade-core."""
    modules = {"stepik_grader.core.grader_core"}

    priv_import = tmp_path / "bad_import.py"
    priv_import.write_text("from stepik_grader.core.grader_core import _RUNNER\n", encoding="utf-8")
    assert _non_public_core_imports(priv_import, modules) == [
        "stepik_grader.core.grader_core._RUNNER"
    ]

    # issue #830 (ARCH-05): имя без подчёркивания, но выведенное из __all__ ядра.
    excluded = tmp_path / "bad_excluded.py"
    excluded.write_text(
        "from stepik_grader.core.grader_core import SIMILAR_THRESHOLD\n", encoding="utf-8"
    )
    assert _non_public_core_imports(excluded, modules) == [
        "stepik_grader.core.grader_core.SIMILAR_THRESHOLD"
    ], "guard считает публичным всё без подчёркивания — ARCH-05 вернулся"

    priv_attr = tmp_path / "bad_attr.py"
    priv_attr.write_text(
        "from stepik_grader.core import grader_core\nx = grader_core._RUNNER\n", encoding="utf-8"
    )
    assert _private_core_attr_access(priv_attr, modules) == ["grader_core._RUNNER"]

    direct = tmp_path / "bad_direct.py"
    direct.write_text("from stepik_grader.core.grader_core import run_tests\n", encoding="utf-8")
    assert _direct_grade_core_imports(direct) == ["stepik_grader.core.grader_core"]

    # Публичный импорт из non-grade core-модуля — НЕ нарушение.
    ok = tmp_path / "ok.py"
    ok.write_text("from stepik_grader.core.history import record_run\n", encoding="utf-8")
    assert _non_public_core_imports(ok, modules) == []
    assert _direct_grade_core_imports(ok) == []
    assert _private_core_attr_access(ok, {"stepik_grader.core.history"}) == []

    # Импорт самого подмодуля — не имя из поверхности пакета, ``__all__`` там нет.
    submodule = tmp_path / "ok_submodule.py"
    submodule.write_text("from stepik_grader.core import history\n", encoding="utf-8")
    assert _non_public_core_imports(submodule, {"stepik_grader.core.history"}) == []


# ---------------------------------------------------------------------------
# Синхронизация «граф в architecture.md ↔ фактические импорты» (issue #825).
#
# Блок «Граф зависимостей» заявлен источником истины для решения «можно ли
# добавить импорт», но проверялся только глазами и разошёлся с кодом: у
# `web/rules_adapter` по доке было одно ребро (`rules/`), а фактически ещё и
# `core/history`+`core/insights`. Планирование по такому графу занижает
# связность web-слоя с ядром.
#
# Сверяются МЕЖСЛОЙНЫЕ рёбра — те, ради которых граф и читают. Внутрипакетные
# (`cli → cli`, `web → web`) и вездесущий `config` в картинку не входят
# намеренно: она про границы слоёв, а не про машинный дамп 213 рёбер.
# ---------------------------------------------------------------------------

_ARCHITECTURE_MD = pathlib.Path(__file__).parent.parent / "docs" / "dev" / "architecture.md"

# Слои для определения «межслойного» ребра.
_LAYERS = ("web", "cli", "core", "glossary", "rules")

# Цели, которые в граф намеренно не рисуются: `config` импортируют почти все
# модули, и его рёбра сделали бы картинку нечитаемой, ничего не объяснив.
_GRAPH_EXEMPT_TARGETS = frozenset({"stepik_grader.config"})


def _layer(module: str) -> str:
    """Слой модуля: `web`/`cli`/`core`/`glossary`/`rules` либо `top`."""
    rest = module.removeprefix(f"{_PKG}.")
    for layer in _LAYERS:
        if rest == layer or rest.startswith(f"{layer}."):
            return layer
    return "top"


def _doc_token_to_module(token: str) -> str | None:
    """`core/history.py` → `stepik_grader.core.history`; мусор → ``None``."""
    token = token.strip().strip("`")
    if not token or token.startswith("("):
        return None
    token = token.rstrip("/")
    if token.endswith(".py"):
        token = token[:-3]
    if not re.fullmatch(r"[\w/.]+", token):
        return None
    module = f"{_PKG}." + token.replace("/", ".")
    return module.removesuffix(".__init__")


def _graph_block() -> str:
    """Текст fenced-блока «Граф зависимостей» из architecture.md."""
    text = _ARCHITECTURE_MD.read_text(encoding="utf-8")
    return text.split("## Граф зависимостей", 1)[1].split("```")[1]


def _documented_edges() -> set[tuple[str, str]]:
    """Рёбра из fenced-блока «Граф зависимостей» в architecture.md."""
    block = _graph_block()
    edges: set[tuple[str, str]] = set()
    for line in block.splitlines():
        if "──→" not in line:
            continue
        left, right = line.split("──→", 1)
        right = re.sub(r"\([^)]*\)", "", right)  # пояснения в скобках — не цели
        sources = left.split(" / ") if " / " in left else [left]
        for raw_source in sources:
            source = _doc_token_to_module(raw_source)
            if source is None:
                continue
            for raw_target in re.split(r"[,;]", right):
                target = _doc_token_to_module(raw_target)
                if target is not None:
                    edges.add((source, target))
    return edges


def _all_import_edges() -> set[tuple[str, str]]:
    """Все рёбра, включая ленивые и TYPE_CHECKING — для проверки «ребро существует»."""
    files = _iter_module_files()
    modules = {_module_name(p) for p in files}
    edges: set[tuple[str, str]] = set()
    for path in files:
        name = _module_name(path)
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                for target in _project_targets(path, node, modules):
                    if target != name:
                        edges.add((name, target))
    return edges


def test_documented_graph_is_parsed() -> None:
    """Guard-the-guard: блок графа читается и даёт десятки рёбер."""
    assert len(_documented_edges()) > 50


def test_graph_edges_fit_one_line() -> None:
    """Ребро записывается ОДНОЙ строкой — пояснение в скобках на той же строке.

    Guard-the-guard на форму записи: `_documented_edges` вырезает пояснения
    регуляркой `\\([^)]*\\)` построчно, поэтому скобка, перенесённая на вторую
    строку, оставляет строку с незакрытой скобкой — и ребро молча перестаёт
    распознаваться. Живой случай: ребро дописали, гейт остался красным, и
    причина была не в графе, а в переносе.
    """
    broken = [
        line.strip()
        for line in _graph_block().splitlines()
        if "──→" in line and line.count("(") != line.count(")")
    ]
    assert not broken, (
        "docs/dev/architecture.md § «Граф зависимостей»: скобки не закрыты в той же "
        "строке — разбор рёбер идёт построчно, и перенос пояснения ломает его молча:\n"
        + "\n".join(f"  {line}" for line in broken)
        + "\nЗапишите ребро одной строкой, пояснение — в скобках рядом."
    )


def test_documented_edges_exist_in_code() -> None:
    """Каждое ребро из доки есть в коде (пусть и ленивым импортом).

    Ловит обратный дрейф: модуль перестал импортировать то, что граф обещает.
    """
    phantom = sorted(_documented_edges() - _all_import_edges())
    assert not phantom, (
        "docs/dev/architecture.md § «Граф зависимостей» обещает рёбра, которых в "
        "коде нет:\n"
        + "\n".join(f"  {s} ──→ {t}" for s, t in phantom)
        + "\nУберите строку из графа или верните импорт."
    )


def test_cross_layer_edges_are_documented() -> None:
    """Каждое межслойное загрузочное ребро отражено в графе документа."""
    actual = {
        (source, target)
        for source, targets in _build_import_graph().items()
        for target in targets
        if _layer(source) != _layer(target) and target not in _GRAPH_EXEMPT_TARGETS
    }
    missing = sorted(actual - _documented_edges())
    assert not missing, (
        "Появились межслойные импорты, которых нет в docs/dev/architecture.md § "
        "«Граф зависимостей»:\n"
        + "\n".join(f"  {s} ──→ {t}" for s, t in missing)
        + "\nДопишите их в блок графа — он источник истины для решения "
        "«можно ли добавить импорт»."
    )


# ---------------------------------------------------------------------------
# issue #996 (ARCH-3-01) — независимые подпакеты не тянут core/.
#
# Инвариант заявлен в CLAUDE.md § «Архитектурные инварианты» и в ADR-0011, и на
# нём построен весь перенос общих хелперов на верхний уровень (`atomic_io`,
# `db`, `stdio_encoding`, `mtime_cache`). Проверять его было нечем: нарушение
# жило в `rules/json_provider.py` и выглядело безобидно — «утилитарный импорт».
# ---------------------------------------------------------------------------

#: Подпакеты-острова: домен без зависимости от ядра проверки. Ребро отсюда в
#: ``core/`` означает, что общий хелпер лежит не там, где должен, — лечится
#: переносом хелпера на верхний уровень, а не расширением этого списка.
_CORE_INDEPENDENT_PACKAGES = ("stepik_grader.glossary", "stepik_grader.rules")


@pytest.mark.parametrize("package", _CORE_INDEPENDENT_PACKAGES)
def test_island_packages_do_not_import_core(package: str) -> None:
    offenders = sorted(
        (module, target)
        for module, targets in _build_import_graph().items()
        if module == package or module.startswith(f"{package}.")
        for target in targets
        if target.startswith("stepik_grader.core")
    )
    assert not offenders, (
        f"{package} импортирует core/ — инвариант CLAUDE.md § «Архитектурные "
        "инварианты» и ADR-0011 нарушен:\n"
        + "\n".join(f"  {source} ──→ {target}" for source, target in offenders)
        + "\nОбщий хелпер выносится на верхний уровень (как atomic_io/db/"
        "mtime_cache), а не импортируется из core/."
    )
