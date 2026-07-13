"""Tests for scripts/generate_ci_coveragerc.py — per-OS coverage config (issue #294).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_docs_guardrails.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "generate_ci_coveragerc.py"
_REAL_ROOT = _SCRIPT.parent.parent

_MIN_PYPROJECT = """\
[tool.coverage.run]
source = ["src"]
omit = ["tests/*"]

[tool.coverage.report]
fail_under = 85
exclude_lines = ["pragma: no cover"]
"""


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_ci_coveragerc", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_other_platforms_omit_linux() -> None:
    """Linux job omits только macOS/Windows backend'ы, не свой собственный."""
    module = _load_module()
    extra = module.other_platforms_omit("Linux")
    assert "src/stepik_grader/core/sandbox/_macos.py" in extra
    assert "src/stepik_grader/core/sandbox/_windows.py" in extra
    assert "src/stepik_grader/core/sandbox/_linux.py" not in extra
    assert "src/stepik_grader/core/sandbox/_posix_common.py" not in extra


def test_other_platforms_omit_darwin() -> None:
    """macOS job омитит Linux/Windows backend'ы, POSIX-общий код — оставляет."""
    module = _load_module()
    extra = module.other_platforms_omit("Darwin")
    assert "src/stepik_grader/core/sandbox/_linux.py" in extra
    assert "src/stepik_grader/core/sandbox/_windows.py" in extra
    assert "src/stepik_grader/core/sandbox/_macos.py" not in extra
    assert "src/stepik_grader/core/sandbox/_posix_common.py" not in extra


def test_other_platforms_omit_windows() -> None:
    """Windows job омитит Linux/macOS backend'ы И общий POSIX-код (недостижим)."""
    module = _load_module()
    extra = module.other_platforms_omit("Windows")
    assert "src/stepik_grader/core/sandbox/_linux.py" in extra
    assert "src/stepik_grader/core/sandbox/_macos.py" in extra
    assert "src/stepik_grader/core/sandbox/_posix_common.py" in extra
    assert "src/stepik_grader/core/sandbox/_windows.py" not in extra


def test_main_writes_valid_coveragerc(tmp_path, monkeypatch) -> None:
    """main() пишет .coveragerc.ci с корректным составом omit для заданной ОС."""
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(_MIN_PYPROJECT, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["generate_ci_coveragerc.py", "Windows"])

    result = module.main()

    assert result == 0
    generated = tmp_path / ".coveragerc.ci"
    assert generated.exists()
    text = generated.read_text(encoding="utf-8")
    assert "src/stepik_grader/core/sandbox/_linux.py" in text
    assert "src/stepik_grader/core/sandbox/_macos.py" in text
    assert "src/stepik_grader/core/sandbox/_posix_common.py" in text
    assert "src/stepik_grader/core/sandbox/_windows.py" not in text
    assert "fail_under = 85" in text
    assert "tests/*" in text  # base omit entry survives alongside the extra ones


def test_main_writes_base_omit_from_real_pyproject(monkeypatch) -> None:
    """Base omit-список подтягивается из настоящего pyproject.toml, не дублируется вручную."""
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", _REAL_ROOT)
    monkeypatch.setattr(sys, "argv", ["generate_ci_coveragerc.py", "Linux"])

    result = module.main()

    assert result == 0
    generated = _REAL_ROOT / ".coveragerc.ci"
    try:
        assert generated.exists()
        text = generated.read_text(encoding="utf-8")
        assert "src/stepik_grader/pytest_plugin.py" in text  # real base omit entry (issue #57)
        assert "src/stepik_grader/core/sandbox/_macos.py" in text
        assert "src/stepik_grader/core/sandbox/_linux.py" not in text
    finally:
        generated.unlink(missing_ok=True)


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, cwd=_REAL_ROOT
    )


def test_cli_rejects_unknown_platform() -> None:
    """Неизвестная платформа -> usage на stderr, exit 1, файл не создаётся."""
    result = _run_cli(["Linux2000"])
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_cli_rejects_missing_arg() -> None:
    result = _run_cli([])
    assert result.returncode == 1
