"""Tests for scripts/check_mcp_permissions.py — запрет MCP формой (issue #1346).

Скрипт лежит в scripts/ (не на sys.path) — грузим его по пути, тем же приёмом,
что и test_check_ruff_pin.py.

Проверяется не только «сейчас чисто», но и что гейт краснеет на **прежней**
форме запрета: именной список отключился молча, когда сервер консолидировал
инструменты, и промах нашли сверкой руками. Гард, который не умеет краснеть на
том самом дефекте, ради которого написан, — это повторение issue #1280.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from types import ModuleType
from typing import Any

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_mcp_permissions.py"
_SETTINGS = pathlib.Path(__file__).parent.parent / ".claude" / "settings.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_mcp_permissions", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

#: Форма, действующая после issue #1346.
_GOOD: dict[str, Any] = {"permissions": {"deny": ["mcp__github"]}}

#: Форма ДО правки: список консолидированных сервером имён. Ни одно из них
#: больше не существует, поэтому запрет не срабатывал ни разу.
_STALE_NAMES: dict[str, Any] = {
    "permissions": {
        "deny": [
            "mcp__github__get_issue",
            "mcp__github__list_issues",
            "mcp__github__list_pull_requests",
            "mcp__github__rerun_workflow_run",
        ],
        "ask": ["mcp__github"],
    }
}


# --- состояние репозитория ----------------------------------------------------


def test_repository_currently_passes() -> None:
    """Приёмка #1346: в самом репозитории запрет держится формой, а не именами."""
    assert _MODULE.main() == 0


def test_settings_deny_the_whole_server() -> None:
    """Запрет стоит на сервере целиком — иначе он привязан к чужим именам."""
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    assert _MODULE.SERVER in settings["permissions"]["deny"]


def test_settings_carry_no_tool_names() -> None:
    """Ни одной именной записи: такая отключается молча при переименовании."""
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    for name in ("deny", "allow", "ask"):
        for entry in settings["permissions"].get(name) or []:
            assert not entry.startswith(f"{_MODULE.SERVER}__"), (name, entry)


# --- permission_violations ----------------------------------------------------


def test_good_form_has_no_violations() -> None:
    assert _MODULE.permission_violations(_GOOD) == []


def test_stale_name_list_is_reported() -> None:
    """Красный до правки: ровно та форма, что стояла в репозитории до #1346."""
    problems = _MODULE.permission_violations(_STALE_NAMES)
    assert problems, "прежняя форма обязана быть нарушением, иначе гейт бесполезен"
    assert any(_MODULE.SERVER in problem for problem in problems), problems


def test_missing_whole_server_deny_is_reported() -> None:
    """Запрет без сервера целиком — нарушение, даже если список имён свежий."""
    problems = _MODULE.permission_violations({"permissions": {"deny": []}})
    assert any("permissions.deny" in problem for problem in problems), problems


def test_tool_name_in_allow_is_reported() -> None:
    """Точечное разрешение поверх запрета сервера не работает — и это ловится.

    `deny` сильнее `allow`, поэтому запись создаёт ложное впечатление, будто
    инструмент доступен, а на деле он запрещён.
    """
    settings = {
        "permissions": {
            "deny": ["mcp__github"],
            "allow": ["mcp__github__create_pull_request"],
        }
    }
    problems = _MODULE.permission_violations(settings)
    assert any("create_pull_request" in problem for problem in problems), problems


def test_missing_permissions_section_is_a_failure() -> None:
    """Гейт без предмета проверки обязан падать, а не зеленеть на пустоте."""
    problems = _MODULE.permission_violations({})
    assert problems, "отсутствие секции permissions означает, что запрета нет вовсе"


def test_permissions_of_wrong_type_is_a_failure() -> None:
    """Секция не-объект — тот же случай «предмета нет»."""
    assert _MODULE.permission_violations({"permissions": []}) != []


# --- запуск как процесса ------------------------------------------------------


def test_cli_exits_zero_on_current_repo() -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _MODULE.SERVER in completed.stdout


def test_output_survives_cp1252_console() -> None:
    """Вывод по-русски не должен ронять шаг на windows-latest."""
    env = {**dict(**__import__("os").environ), "PYTHONIOENCODING": "cp1252"}
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
