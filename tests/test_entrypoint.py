"""Subprocess-level regression tests for CLI entrypoints (issue #122).

Существующие тесты cli.main() вызывают его in-process (`cli.main([...])`) —
это не проверяет реальную wiring-цепочку: console_scripts entry point
(`stepik-grader`), `python -m stepik_grader` (`__main__.py`) и
`python -m stepik_grader.grader` (`grader.py`), которые staged-декомпозиция
cli.py (issues #117-#121) обязана не сломать. Эти тесты запускают настоящие
subprocess'ы, а не импортируют cli напрямую — тот же паттерн, что уже
использует tests/test_version_script.py.
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
    # issue #820: версия называет реальную команду, а не файл grader.py,
    # которого при src-layout не существует.
    assert proc.stdout.startswith("stepik-grader ")


def test_module_entrypoint_prints_version() -> None:
    """`python -m stepik_grader` — __main__.py делегирует в cli.main()."""
    proc = subprocess.run(
        [sys.executable, "-m", "stepik_grader", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    # issue #820: версия называет реальную команду, а не файл grader.py,
    # которого при src-layout не существует.
    assert proc.stdout.startswith("stepik-grader ")


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
    # issue #820: версия называет реальную команду, а не файл grader.py,
    # которого при src-layout не существует.
    assert proc.stdout.startswith("stepik-grader ")


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


# ---------------------------------------------------------------------------
# issue #996 (INS-3-01) — файл-однофамилец stdlib в каталоге задачи.
#
# `python -m` кладёт cwd ПЕРВЫМ в sys.path, а грейдер запускают из каталога с
# решением студента. `json.py`, `string.py`, `random.py`, `logging.py` — обычные
# имена для учебной задачи, и каждый перекрывал настоящий stdlib-модуль: грейдер
# падал трейсбеком в свои внутренности, не показав ни одного экрана.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shadow", ["json", "string", "random", "logging"])
def test_module_entrypoint_survives_stdlib_shadow(tmp_path: Path, shadow: str) -> None:
    """Запуск из каталога, где лежит файл с именем stdlib-модуля."""
    (tmp_path / f"{shadow}.py").write_text("raise SystemExit('подмена')\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "stepik_grader", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "stepik-grader" in result.stdout


def test_stdlib_shadow_needed_by_runpy_is_out_of_reach(tmp_path: Path) -> None:
    """Граница фикса, названная вслух: `types.py` не чинится и чиниться не может.

    Модули, нужные самому `runpy`, загружаются ДО первой строки `__main__.py` —
    интерпретатор печатает «Could not import runpy module» и до нашего кода
    управление не доходит. Тест фиксирует это как известный предел, чтобы
    следующий читатель не считал фикс полным и не искал регрессию.
    """
    (tmp_path / "types.py").write_text("X = 1\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "stepik_grader", "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "types" in result.stderr


# ---------------------------------------------------------------------------
# issue #996 (PKG-1-06, AUD-1-04) — метаданные пакета.
# ---------------------------------------------------------------------------


def test_package_exports_version() -> None:
    """`stepik_grader.__version__` — то же, что печатает `--version`."""
    import stepik_grader

    assert stepik_grader.__all__ == ["__version__"]
    assert stepik_grader.__version__
    assert isinstance(stepik_grader.__version__, str)


def test_package_version_agrees_with_cli() -> None:
    """Три места читают одни метаданные — значение обязано совпадать."""
    import stepik_grader
    from stepik_grader import cli

    assert stepik_grader.__version__ == cli.__version__


def test_core_package_declares_empty_all() -> None:
    """`core/` ничего не реэкспортирует — звёздочка не должна ничего приносить."""
    import stepik_grader.core

    assert stepik_grader.core.__all__ == []


def test_core_docstring_is_russian() -> None:
    """CLAUDE.md § Язык артефактов: докстринги — по-русски (issue #996, AUD-1-04)."""
    import stepik_grader.core

    doc = stepik_grader.core.__doc__ or ""
    assert any("а" <= letter.lower() <= "я" for letter in doc), doc
