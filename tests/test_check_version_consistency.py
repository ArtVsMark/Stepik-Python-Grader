"""Tests for scripts/check_version_consistency.py — version-drift guard (issue #165).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_version_script.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_version_consistency.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_version_consistency", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main дрейфа быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_version_consistency.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pyproject_static_version_is_flagged(monkeypatch) -> None:
    """Возврат статической [project].version → ошибка (source-of-truth регресс)."""
    module = _load_module()
    errors: list[str] = []

    real_load = module.tomllib.load

    def fake_load(f):
        data = real_load(f)
        data.setdefault("project", {})["version"] = "9.9.9"
        data["project"]["dynamic"] = []
        return data

    monkeypatch.setattr(module.tomllib, "load", fake_load)
    module._check_pyproject_dynamic(errors)
    assert any("statically" in e for e in errors), errors


def test_checkpoint_drift_is_flagged(monkeypatch) -> None:
    """CHECKPOINT с чужим MAJOR.MINOR относительно baseline → ошибка."""
    module = _load_module()
    errors: list[str] = []
    monkeypatch.setattr(
        module.Path, "read_text", lambda self, encoding="utf-8": "## Текущая версия: 1.4.0\n"
    )
    module._check_checkpoint((1, 5, 0), errors)
    assert any("disagrees" in e for e in errors), errors


def test_checkpoint_matching_minor_passes(monkeypatch) -> None:
    """CHECKPOINT с совпадающим MAJOR.MINOR (PATCH может отличаться) → без ошибок."""
    module = _load_module()
    errors: list[str] = []
    monkeypatch.setattr(
        module.Path, "read_text", lambda self, encoding="utf-8": "## Текущая версия: 1.5.3\n"
    )
    module._check_checkpoint((1, 5, 0), errors)
    assert errors == []


def test_skips_without_git_tags(monkeypatch, capsys) -> None:
    """Нет baseline (нет git/тегов) → SKIP сверки доков, main() всё равно 0
    (pyproject на текущем репо динамический)."""
    module = _load_module()
    monkeypatch.setattr(module, "_latest_tag_baseline", lambda: None)
    assert module.main() == 0
    assert "SKIP" in capsys.readouterr().out
