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
import pathlib

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


def _private_core_imports(path: pathlib.Path) -> list[str]:
    """Импортируемые из core/glossary/rules ПРИВАТНЫЕ имена (``from X import _name``)."""
    offenders: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_target(path, node)
            if _is_core_pkg(resolved):
                offenders += [f"{resolved}.{a.name}" for a in node.names if a.name.startswith("_")]
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
    """ADR-0010: ни один web-модуль не импортирует приватные ``_``-имена из ядра."""
    violations = {_module_name(p): off for p in _web_files() if (off := _private_core_imports(p))}
    assert not violations, (
        "web импортирует приватные core-имена (нарушение публичной границы, "
        "ADR-0010 / issue #549):\n"
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


def test_boundary_guard_catches_synthetic_violations(tmp_path: pathlib.Path) -> None:
    """Guard-the-guard: детекторы реально ловят приватный импорт / атрибут / прямой grade-core."""
    modules = {"stepik_grader.core.grader_core"}

    priv_import = tmp_path / "bad_import.py"
    priv_import.write_text("from stepik_grader.core.grader_core import _RUNNER\n", encoding="utf-8")
    assert _private_core_imports(priv_import) == ["stepik_grader.core.grader_core._RUNNER"]

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
    assert _private_core_imports(ok) == []
    assert _direct_grade_core_imports(ok) == []
    assert _private_core_attr_access(ok, {"stepik_grader.core.history"}) == []
