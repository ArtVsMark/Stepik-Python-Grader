"""Тесты scripts/check_py_style.py — код написан на версии планки (issue #1570)."""

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_py_style.py"

_PYPROJECT = {
    "project": {"requires-python": ">=3.14"},
    "tool": {"ruff": {"per-file-target-version": {"engine/*.py": "py312"}}},
}
_FUTURE = "from __future__ import annotations\n\nx = 1\n"


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_py_style", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_future_import_on_the_floor_is_a_finding(gate: ModuleType) -> None:
    """Отвергаемый предмет: код на планке 3.14 в стиле прежней версии."""
    found = gate.findings({"app.py": _FUTURE}, _PYPROJECT, (3, 14))

    assert found == ["app.py: `from __future__ import annotations` лишний с Python 3.14"]


def test_a_file_with_a_lower_target_keeps_it(gate: ModuleType) -> None:
    """Исключение — та же таблица, что у ruff, а не второй список."""
    assert gate.findings({"engine/tracer.py": _FUTURE}, _PYPROJECT, (3, 14)) == []


def test_below_the_version_nothing_is_required(gate: ModuleType) -> None:
    """Требование включается планкой: на 3.12 future-импорт законен."""
    assert gate.findings({"app.py": _FUTURE}, _PYPROJECT, (3, 12)) == []


def test_clean_code_passes(gate: ModuleType) -> None:
    assert gate.findings({"app.py": "x = 1\n"}, _PYPROJECT, (3, 14)) == []


def test_the_floor_comes_from_requires_python(gate: ModuleType) -> None:
    assert gate.floor(_PYPROJECT) == (3, 14)
    assert gate.floor({"project": {}}) is None


def test_no_floor_is_a_broken_check(gate: ModuleType, tmp_path: pathlib.Path) -> None:
    """Нет планки — «не отработала», а не «чисто» (правило 075)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_BROKEN


def test_the_live_repository_is_clean(gate: ModuleType) -> None:
    """Guard-the-guard: дерево проекта чисто, и проверка видит его файлы."""
    assert gate.main([]) == gate.EXIT_OK


def test_the_gate_rejects_a_repository_on_the_old_style(
    gate: ModuleType, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Подделанный предмет через сам гейт: отслеживаемый файл в стиле 3.12 — отказ."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.14"\n', encoding="utf-8"
    )
    (tmp_path / "app.py").write_text(_FUTURE, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True)

    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_FINDINGS
    assert any("app.py" in line for line in capsys.readouterr().out.splitlines())
