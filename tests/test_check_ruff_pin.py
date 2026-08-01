"""Tests for scripts/check_ruff_pin.py — версия ruff в одном месте (issue #791).

Скрипт лежит в scripts/ (не на sys.path) — грузим его по пути, тем же приёмом,
что и test_combine_coverage.py / test_check_web_imports.py.

Проверяется не только «сейчас чисто», но и что каждое из трёх нарушений скрипт
реально ловит: гард, который не умеет краснеть, — это ровно тот класс дефекта,
ради которого issue и заведён.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from types import ModuleType

from packaging.requirements import Requirement
from packaging.version import Version

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_ruff_pin.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_ruff_pin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_GOOD_PRECOMMIT = """repos:
  - repo: local
    hooks:
      - id: ruff-check
        entry: ruff check --fix
        language: system
      - id: ruff-format
        entry: ruff format
        language: system
"""


# --- состояние репозитория ----------------------------------------------------


def test_repository_currently_passes() -> None:
    """Приёмка #791: в самом репозитории версия ruff задана ровно одним местом."""
    assert _MODULE.main() == 0


def test_dev_requirement_has_upper_bound() -> None:
    """Спецификатор в [dev] ограничен сверху — иначе CI и локаль разъедутся."""
    req = _MODULE.ruff_requirement()
    assert any(spec.operator in ("<", "<=", "==", "~=") for spec in req.specifier), req


# --- precommit_violations -----------------------------------------------------


def test_clean_precommit_has_no_violations() -> None:
    assert _MODULE.precommit_violations(_GOOD_PRECOMMIT) == []


def test_detects_return_of_ruff_pre_commit_repo() -> None:
    """Возврат к `ruff-pre-commit` = второй источник версии — ровно тот дефект."""
    text = (
        _GOOD_PRECOMMIT
        + """
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.21
    hooks:
      - id: ruff-check
"""
    )
    problems = _MODULE.precommit_violations(text)
    assert len(problems) == 1
    assert "ruff-pre-commit" in problems[0]


def test_detects_lost_ruff_hook() -> None:
    """Хук потеряли целиком — локальный гейт перестал зеркалить CI."""
    text = _GOOD_PRECOMMIT.replace("      - id: ruff-format\n", "")
    problems = _MODULE.precommit_violations(text)
    assert len(problems) == 1
    assert "ruff-format" in problems[0]


# --- specifier_violations -----------------------------------------------------


def test_specifier_without_upper_bound_is_reported() -> None:
    problems = _MODULE.specifier_violations(Requirement("ruff>=0.16"), Version("0.16.0"))
    assert len(problems) == 1
    assert "верхней границы" in problems[0]


def test_installed_version_outside_specifier_is_reported() -> None:
    """Устаревшая dev-установка даёт ложное «всё чисто» — ловим до того."""
    problems = _MODULE.specifier_violations(Requirement("ruff>=0.16,<0.17"), Version("0.15.21"))
    assert len(problems) == 1
    assert "0.15.21" in problems[0]


def test_missing_ruff_is_not_a_violation() -> None:
    """Без установленного ruff (голое окружение) гард не ругается: проверять
    нечего, а падение здесь мешало бы прогону в окружении без dev-extra."""
    assert _MODULE.specifier_violations(Requirement("ruff>=0.16,<0.17"), None) == []


def test_matching_installed_version_passes() -> None:
    assert _MODULE.specifier_violations(Requirement("ruff>=0.16,<0.17"), Version("0.16.0")) == []


# --- вывод на консоли без UTF-8 ----------------------------------------------


def test_output_survives_cp1252_console() -> None:
    """Гард не падает на Windows-консоли, которая не знает кириллицы.

    Так он упал на первом же прогоне CI: `python scripts/check_ruff_pin.py` в
    windows-job'е печатал русский отчёт в cp1252 и валился
    ``UnicodeEncodeError`` — то есть шаг краснел не из-за расхождения версий, а
    из-за собственного вывода. Воспроизводится на любой ОС: подсовываем stdout
    с cp1252, как на Windows-раннере.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert b"UnicodeEncodeError" not in proc.stderr
