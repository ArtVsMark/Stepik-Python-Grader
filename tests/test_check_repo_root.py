"""Тесты scripts/check_repo_root.py — корень репозитория по белому списку (issue #1587)."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "check_repo_root.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_repo_root", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate() -> ModuleType:
    """Свежий модуль гейта."""
    return _load()


def test_live_root_is_clean(gate: ModuleType) -> None:
    """На настоящем дереве лишних записей в корне нет."""
    assert gate.unexpected(gate.tracked_root_entries()) == []


def test_stray_file_is_rejected(gate: ModuleType) -> None:
    """Случайно закоммиченный файл примера — находка (прецедент data.json)."""
    assert gate.unexpected({"src", "data.json", "data.pkl"}) == ["data.json", "data.pkl"]


def test_allowed_entries_pass(gate: ModuleType) -> None:
    """Записи из белого списка находками не считаются."""
    assert gate.unexpected({"src", "README.md", ".github"}) == []


def test_finding_exits_one(gate: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Лишняя запись — код 1."""
    monkeypatch.setattr(gate, "tracked_root_entries", lambda: {"src", "data.json"})
    assert gate.main([]) == 1


def test_git_failure_is_a_broken_run(gate: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """git не ответил — проверка не отработала, код 2."""

    def broken() -> set[str]:
        raise subprocess.CalledProcessError(128, ["git"])

    monkeypatch.setattr(gate, "tracked_root_entries", broken)
    assert gate.main([]) == 2
