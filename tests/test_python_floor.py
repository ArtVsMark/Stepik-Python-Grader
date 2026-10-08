"""Пакет отказывает словами под Python ниже планки (issue #1570, эпик #1575).

После подъёма планки код пакета перестаёт разбираться прежними
интерпретаторами. Уже стоящий `.venv` на 3.12/3.13 падал бы `SyntaxError` с
номером строки — выглядит поломкой грейдера, а не «обновите Python».
Корневой `__init__` проверяет версию первым делом и выходит с сообщением.
"""

from __future__ import annotations

import ast
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import stepik_grader

_ROOT = pathlib.Path(__file__).parent.parent
_INIT = _ROOT / "src" / "stepik_grader" / "__init__.py"


def test_the_floor_in_the_code_is_the_floor_of_the_project() -> None:
    """Копия планки в `__init__` не расходится с `requires-python`.

    Копия намеренная — импортировать `importlib.metadata` в корневом `__init__`
    нельзя (`json.py` в каталоге задачи), — поэтому расхождение ловит тест.
    """
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python = ">=(\d+)\.(\d+)"', text)

    assert match is not None
    assert stepik_grader._PYTHON_FLOOR == (int(match.group(1)), int(match.group(2)))


def test_the_init_parses_on_an_old_interpreter() -> None:
    """Сказать «Python не тот» можно только тем Python, что прочёл файл."""
    ast.parse(_INIT.read_text(encoding="utf-8"), feature_version=(3, 9))


def test_the_message_names_both_versions_and_the_way_out() -> None:
    message = stepik_grader._wrong_python_message((3, 12, 3), "/usr/bin/python3.12")
    need = ".".join(map(str, stepik_grader._PYTHON_FLOOR))

    assert f"Python {need} или новее" in message
    assert "Python 3.12.3" in message
    assert f"python{need} -m venv .venv" in message
    assert "stepik-python-grader<1.13" in message


def _older_python() -> str | None:
    """Любой установленный интерпретатор ниже планки — для живой проверки."""
    floor = stepik_grader._PYTHON_FLOOR
    for minor in range(floor[1] - 1, 8, -1):
        found = shutil.which(f"python{floor[0]}.{minor}")
        if found:
            return found
    return None


@pytest.mark.skipif(_older_python() is None, reason="интерпретатора ниже планки в системе нет")
def test_an_old_interpreter_gets_words_and_its_own_exit_code() -> None:
    """Живой запуск: сообщение и код `_EXIT_WRONG_PYTHON`, а не traceback."""
    python = _older_python()
    assert python is not None

    result = subprocess.run(
        [python, "-c", "import stepik_grader"],
        env={"PYTHONPATH": str(_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    assert result.returncode == stepik_grader._EXIT_WRONG_PYTHON
    assert "Грейдеру нужен Python" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_current_interpreter_is_not_refused() -> None:
    """Тесты идут на планке — пакет импортирован, значит отказа не было."""
    assert sys.version_info >= stepik_grader._PYTHON_FLOOR
