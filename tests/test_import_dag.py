"""tests/test_import_dag.py — граф импортов пакета остаётся DAG без циклов, а
канонические leaf-модули не тянут ничего из проекта (issue #410, инвариант A6).

CLAUDE.md § «Архитектурные инварианты» фиксирует два правила, которые до сих пор
держались только на код-ревью и docs/architecture.md, но ничем не проверялись:

1. **DAG без циклов** — новые импорты не создают циклических зависимостей между
   модулями ``stepik_grader``. Цикл на уровне *загрузки модуля* — это реальный
   ``ImportError`` в проде, а не косметика.
2. **Leaf-модули** — ``core/storage.py``, ``core/normalizers.py``,
   ``core/glossary.py`` не импортируют ничего из проекта (только stdlib).

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
import pathlib

import pytest

_SRC_ROOT = pathlib.Path(__file__).parent.parent / "src"
_PKG = "stepik_grader"
_PKG_ROOT = _SRC_ROOT / _PKG

# CLAUDE.md § «Архитектурные инварианты», п.2 — эти три модуля leaf по контракту.
_LEAF_MODULES = (
    "stepik_grader.core.storage",
    "stepik_grader.core.normalizers",
    "stepik_grader.core.glossary",
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
    """Leaf-инвариант (CLAUDE.md п.2): storage/normalizers/glossary — только stdlib.

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


def test_cycle_detector_catches_synthetic_cycle() -> None:
    """Guard-the-guard: детектор реально ловит цикл (иначе тест выше — пустой)."""
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}}) is not None
    assert _find_cycle({"a": {"b"}, "b": {"c"}, "c": set()}) is None


def test_import_graph_is_non_trivial() -> None:
    """Санити: граф действительно построен (модули найдены и рёбра извлечены)."""
    graph = _build_import_graph()
    assert len(graph) > 50, "Ожидались десятки модулей — путь к пакету сломан?"
    assert sum(len(v) for v in graph.values()) > 50, "Рёбра не извлеклись — сломан парсер импортов?"
