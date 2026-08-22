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

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_ruff_pin.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_ruff_pin", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрация ДО exec_module: `from __future__ import annotations` делает
    # аннотации строками, и `@dataclass` разрешает их через
    # `sys.modules[cls.__module__]`. Незарегистрированный модуль даёт там None
    # и падение на импорте — то есть тест ломался бы не по существу.
    sys.modules[spec.name] = module
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


# --- список инструментов вердикта (issue #1349) -------------------------------


def test_mypy_is_pinned_like_ruff() -> None:
    """Приёмка #1349: mypy — инструмент вердикта, и границы у него есть.

    Красный до правки: `mypy>=1.10` без верхней границы означал ложное красное
    у того, кто пересоздал окружение в день минорного релиза, — на неизменном
    коде и с новой диагностикой.
    """
    names = [tool.name for tool in _MODULE.VERDICT_TOOLS]
    assert "mypy" in names, f"mypy не в списке пришпиливаемого: {names}"

    req = _MODULE.tool_requirement(
        next(tool for tool in _MODULE.VERDICT_TOOLS if tool.name == "mypy")
    )
    assert any(spec.operator in ("<", "<=", "==", "~=") for spec in req.specifier), req


@pytest.mark.parametrize("tool", _MODULE.VERDICT_TOOLS, ids=lambda t: t.name)
def test_every_verdict_tool_has_an_upper_bound(tool: object) -> None:
    """Ни один инструмент вердикта не входит в окружение без верхней границы."""
    req = _MODULE.tool_requirement(tool)
    assert _MODULE.specifier_violations(req, None) == [], req


@pytest.mark.parametrize("tool", _MODULE.VERDICT_TOOLS, ids=lambda t: t.name)
def test_installed_version_is_recognised(tool: object) -> None:
    """Вывод `--version` разбирается: иначе проверка «установлено то» немая.

    Немая проверка хуже отсутствующей — она зеленеет на любом окружении, и
    устаревшая установка проходит гейт незамеченной.
    """
    version = _MODULE.installed_version(tool)
    assert version is not None, f"{tool.name}: версия не распознана в выводе --version"


def test_unknown_tool_names_itself_in_the_error() -> None:
    """Отсутствие инструмента в [dev] — отказ с именем, а не молчание."""
    missing = _MODULE.VerdictTool("нет-такого", "нет_такого", r"^x (\d+)")
    with pytest.raises(SystemExit) as exc:
        _MODULE.tool_requirement(missing)
    assert "нет-такого" in str(exc.value)


def test_violation_message_names_the_tool() -> None:
    """Сообщение называет инструмент — иначе на двух пинах не понять, чей сломан."""
    problems = _MODULE.specifier_violations(Requirement("mypy>=1.10"), Version("2.3.1"))
    assert problems, "спецификатор без верхней границы обязан быть нарушением"
    assert any("mypy" in problem for problem in problems), problems


def test_run_reports_every_tool() -> None:
    """Отчёт перечисляет все инструменты: пропущенный пин не должен быть невидим."""
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for tool in _MODULE.VERDICT_TOOLS:
        assert tool.name in completed.stdout, completed.stdout
