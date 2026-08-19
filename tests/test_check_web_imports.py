"""Tests for scripts/check_web_imports.py — импорты web-модулей (issue #855).

Guard-the-guard: на реальном репозитории зелёный, а синтетический пропуск
импорта делает его красным. Скрипт лежит в `scripts/` (не на sys.path) —
грузим по пути, тем же приёмом, что `test_check_locale_guardrails.py`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_web_imports.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_web_imports", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_web_imports.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Сам предмет проверки: вызов без импорта
# ---------------------------------------------------------------------------


def test_call_without_import_is_flagged() -> None:
    """Регрессия #855: `kpiGrid()` вызывается, но в импорте его нет."""
    module = _load_module()
    source = 'import { $, esc } from "./core.js";\n$("#x").innerHTML = kpiGrid([]);\n'
    assert module.missing_imports(source, {"kpiGrid", "esc"}) == ["kpiGrid"]


def test_imported_call_is_not_flagged() -> None:
    """Тот же вызов с импортом — не нарушение."""
    module = _load_module()
    source = 'import { $, esc, kpiGrid } from "./core.js";\n$("#x").innerHTML = kpiGrid([]);\n'
    assert module.missing_imports(source, {"kpiGrid", "esc"}) == []


def test_method_call_on_object_is_not_a_bare_call() -> None:
    """`obj.kpiGrid(...)` — свойство объекта, а не имя из core.js."""
    module = _load_module()
    source = 'import { $ } from "./core.js";\nconst h = view.kpiGrid([]);\n'
    assert module.missing_imports(source, {"kpiGrid"}) == []


def test_multiple_import_statements_are_merged() -> None:
    """Имена собираются из всех импортов модуля, а не только из первого."""
    module = _load_module()
    source = (
        'import { $ } from "./core.js";\n'
        'import { kpiGrid } from "./core.js";\n'
        "const h = kpiGrid([]);\n"
    )
    assert module.missing_imports(source, {"kpiGrid"}) == []


def test_aliased_import_counts_as_imported() -> None:
    """`import { kpiGrid as grid }` — используется локальное имя."""
    module = _load_module()
    source = 'import { kpiGrid as grid } from "./core.js";\nconst h = grid([]);\n'
    assert module.missing_imports(source, {"kpiGrid"}) == []


# ---------------------------------------------------------------------------
# Нулевой вход = ошибка (то же правило, что у guard'ов локалей, issue #787)
# ---------------------------------------------------------------------------


def test_zero_modules_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Каталог статики переехал → guard падает, а не рапортует «всё чисто»."""
    module = _load_module()
    static = tmp_path / "static"
    static.mkdir()
    (static / "core.js").write_text("export { kpiGrid };\n", encoding="utf-8")
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_core_imports(errors)
    assert any("нет ни одного .js" in e for e in errors), errors


def test_unparsable_core_exports_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Блок `export {…}` не найден — проверять не с чем, это тоже ошибка."""
    module = _load_module()
    static = tmp_path / "static"
    static.mkdir()
    (static / "core.js").write_text("// без блока export\n", encoding="utf-8")
    (static / "app.js").write_text("const x = 1;\n", encoding="utf-8")
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_core_imports(errors)
    assert any("export" in e for e in errors), errors


def test_end_to_end_flags_missing_import_in_a_real_layout(monkeypatch, tmp_path: Path) -> None:
    """Полный проход по каталогу находит модуль с пропущенным импортом."""
    module = _load_module()
    static = tmp_path / "static"
    static.mkdir()
    (static / "core.js").write_text("export { kpiGrid, esc };\n", encoding="utf-8")
    (static / "content.js").write_text(
        'import { esc } from "./core.js";\nconst h = kpiGrid([]);\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_core_imports(errors)
    assert len(errors) == 1
    assert "content.js" in errors[0] and "kpiGrid" in errors[0]


# ---------------------------------------------------------------------------
# issue #921: сломанный guard по определению не виден — он проходит.
# Здесь проверяется, что он падает на заведомо сломанном входе.
# ---------------------------------------------------------------------------


def _layout(static: Path) -> None:
    """Минимальная статика: core.js плюс два модуля с ребром между ними."""
    static.mkdir(parents=True, exist_ok=True)
    (static / "core.js").write_text("export { esc };\n", encoding="utf-8")
    (static / "content.js").write_text(
        'import { esc } from "./core.js";\n'
        "function openGlossary() { return esc(1); }\n"
        "export { openGlossary };\n",
        encoding="utf-8",
    )


def test_module_in_a_subdirectory_is_not_invisible(monkeypatch, tmp_path: Path) -> None:
    """AUD-2-03: `glob` вместо `rglob` ослеплял гейт молча.

    Переезд модуля в подкаталог не должен превращать проверку в проверку
    пустоты — она продолжала бы печатать «no missing imports».
    """
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "widgets").mkdir()
    (static / "widgets" / "chart.js").write_text("const h = esc(1);\n", encoding="utf-8")
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_core_imports(errors)

    assert len(errors) == 1
    assert "chart.js" in errors[0] and "esc" in errors[0]


def test_edge_between_two_modules_is_checked(monkeypatch, tmp_path: Path) -> None:
    """AUD-2-04: стерегли одно ребро из десяти, ломаются они одинаково."""
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "grade.js").write_text(
        'import { } from "./content.js";\nopenGlossary();\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_all_edges(errors)

    assert len(errors) == 1
    assert "grade.js" in errors[0] and "openGlossary" in errors[0]
    assert "content.js" in errors[0]


def test_properly_imported_edge_is_quiet(monkeypatch, tmp_path: Path) -> None:
    """Контроль: гейт, который краснеет всегда, обходят."""
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "grade.js").write_text(
        'import { openGlossary } from "./content.js";\nopenGlossary();\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_all_edges(errors)

    assert errors == []


def test_own_definition_is_not_a_missing_import(monkeypatch, tmp_path: Path) -> None:
    """Локальный `render()` не обязан импортироваться из-за тёзки у соседа.

    Без этого обобщение гейта на все рёбра начало бы краснеть без причины — и
    его обошли бы, как любое предупреждение, которое горит всегда.
    """
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "nav.js").write_text("function render() {}\nexport { render };\n", encoding="utf-8")
    (static / "grade.js").write_text(
        'import { } from "./nav.js";\nfunction render() {}\nrender();\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_all_edges(errors)

    assert errors == []


def test_aliased_export_is_matched_by_its_public_name(monkeypatch, tmp_path: Path) -> None:
    """`export { render as renderNavigation }` — наружу видно второе имя."""
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "nav.js").write_text(
        "function render() {}\nexport { render as renderNavigation };\n", encoding="utf-8"
    )
    (static / "grade.js").write_text(
        'import { } from "./nav.js";\nrenderNavigation();\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    errors: list[str] = []
    module.check_all_edges(errors)

    assert len(errors) == 1
    assert "renderNavigation" in errors[0]


def test_real_repository_has_more_than_one_edge(monkeypatch) -> None:
    """Страховка от тихого возврата к «одному ребру из десяти».

    Если однажды проверка снова сузится до `core.js`, счётчик рёбер обнулится,
    а вывод останется бодрым — ровно тот случай, ради которого заведён #921.
    """
    module = _load_module()
    edges = 0
    for path in module.module_files():
        source = path.read_text(encoding="utf-8")
        for match in module._LOCAL_IMPORT.finditer(source):
            if Path(match.group(2)).name != module._CORE.name:
                edges += 1

    assert edges >= 5, f"рёбер модуль↔модуль стало {edges} — проверка сузилась?"


def test_main_actually_runs_the_edge_check(monkeypatch, tmp_path: Path) -> None:
    """Провод, а не только сама проверка: `main()` обязан её звать.

    Первая редакция этих тестов дёргала `check_all_edges` напрямую — и удаление
    вызова из `main()` не роняло ничего. Ровно тот класс, ради которого заведён
    #921: проверка есть, а гейт её не выполняет.
    """
    module = _load_module()
    static = tmp_path / "static"
    _layout(static)
    (static / "grade.js").write_text(
        'import { } from "./content.js";\nopenGlossary();\n', encoding="utf-8"
    )
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_CORE", static / "core.js")

    assert module.main() == 1
