"""Tests for scripts/version.py — версионирование по схеме проекта (issue #68).

Схема (CONTRIBUTING.md §Версионирование) — НЕ SemVer: MAJOR.MINOR из тега
``vX.Y.0``, PATCH = число коммитов после тега; до первого тега — fallback на
MAJOR.MINOR из pyproject + полное число коммитов.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "version.py"

# X.Y.Z, где каждая часть — неотрицательное целое (совпадает по форме с PEP 440
# release-сегментом, хотя схема проекта трактует Z как счётчик коммитов).
_XYZ = re.compile(r"^\d+\.\d+\.\d+$")


def _load_module() -> ModuleType:
    """Загрузить scripts/version.py как модуль (папка scripts/ не на sys.path)."""
    spec = importlib.util.spec_from_file_location("_version_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_version_matches_scheme() -> None:
    """project_version() возвращает строку вида X.Y.Z."""
    version = _load_module().project_version()
    assert _XYZ.match(version), version


def test_version_script_cli_prints_version() -> None:
    """`python scripts/version.py` печатает валидную версию и завершается 0
    (acceptance-критерий issue #68)."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0
    assert _XYZ.match(result.stdout.strip()), result.stdout


def test_tagged_path_parses_commits_as_patch(monkeypatch) -> None:
    """При наличии тега PATCH = число коммитов после него (git describe)."""
    module = _load_module()
    monkeypatch.setattr(module, "_git", lambda *a: "v1.2.0-17-g3fa9c21")
    assert module.project_version() == "1.2.17"


def test_fallback_when_no_tags(monkeypatch) -> None:
    """До первого тега (git describe → None) — MAJOR.MINOR из pyproject,
    PATCH = число коммитов; версия всё равно валидна."""
    module = _load_module()

    def fake_git(*args: str) -> str | None:
        if args[:1] == ("describe",):
            return None  # тегов ещё нет
        if args[:2] == ("rev-list", "--count"):
            return "42"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    version = module.project_version()
    assert version.endswith(".42"), version
    assert _XYZ.match(version), version
