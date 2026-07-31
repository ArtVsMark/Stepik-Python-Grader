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
