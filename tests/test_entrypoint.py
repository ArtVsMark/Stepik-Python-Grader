"""Subprocess-level regression tests for CLI entrypoints (issue #122).

Существующие тесты cli.main() вызывают его in-process (`cli.main([...])`) —
это не проверяет реальную wiring-цепочку: console_scripts entry point
(`stepik-grader`), `python -m stepik_grader` (`__main__.py`) и
`python -m stepik_grader.grader` (`grader.py`), которые staged-декомпозиция
cli.py (issues #117-#121) обязана не сломать. Эти тесты запускают настоящие
subprocess'ы, а не импортируют cli напрямую — тот же паттерн, что уже
используют tests/test_version_script.py и tests/test_executor.py.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _console_script_path() -> Path | None:
    """Найти установленный console_scripts entry point `stepik-grader`.

    `pip install -e .` кладёт скрипт рядом с sys.executable (Scripts/ на
    Windows, bin/ на POSIX) в том же venv; PATH — надёжный fallback, если
    раскладка окружения отличается.
    """
    name = "stepik-grader.exe" if sys.platform == "win32" else "stepik-grader"
    candidate = Path(sys.executable).parent / name
    if candidate.is_file():
        return candidate
    found = shutil.which("stepik-grader")
    return Path(found) if found else None


def test_console_script_prints_version() -> None:
    """`stepik-grader --version` — реальный console_scripts entry point."""
    script = _console_script_path()
    if script is None:
        pytest.skip("stepik-grader console script not found (pip install -e . not run?)")
    proc = subprocess.run([str(script), "--version"], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0
    assert "grader.py" in proc.stdout


def test_module_entrypoint_prints_version() -> None:
    """`python -m stepik_grader` — __main__.py делегирует в cli.main()."""
    proc = subprocess.run(
        [sys.executable, "-m", "stepik_grader", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "grader.py" in proc.stdout


def test_grader_module_entrypoint_prints_version() -> None:
    """`python -m stepik_grader.grader` — задокументированная в grader.py
    точка входа (её __all__/compat facade не должны ломаться staged-декомпозицией)."""
    proc = subprocess.run(
        [sys.executable, "-m", "stepik_grader.grader", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "grader.py" in proc.stdout


def test_console_script_runs_mode_1_end_to_end(tmp_path: Path) -> None:
    """Полный прогон через реальный subprocess: console script → cli/__init__.py
    (тонкая обёртка) → cli/commands.py (CliContext) → фактическая проверка решения.

    Ловит то, что in-process cli.main([...]) тесты не могут: поломанный
    console_scripts entry point, ошибки импорта в установленном пакете,
    расхождение между facade-именами и реальной wiring-цепочкой.
    """
    script = _console_script_path()
    if script is None:
        pytest.skip("stepik-grader console script not found (pip install -e . not run?)")

    solution = tmp_path / "task1.py"
    solution.write_text("print(int(input()) + 1)\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text("4", encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text("5", encoding="utf-8")

    proc = subprocess.run(
        [str(script), "--mode", "1", "--file", str(solution), "--output", "json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert '"passed": 1' in proc.stdout
    assert '"failed": 0' in proc.stdout
