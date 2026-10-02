#!/usr/bin/env python3
"""scripts/check_mcp_permissions.py — запрет MCP держится формой, а не именами.

Предыстория (issue #1280 → #1346). Запрет GitHub-инструментов MCP был списком
из двадцати девяти **имён**: ``get_issue``, ``list_pull_requests``,
``rerun_workflow_run`` и так далее. Сервер MCP их консолидировал — сегодня он
отдаёт ``issue_read``, ``pull_request_read``, ``actions_list``,
``actions_run_trigger``, — и **ни одно имя из списка больше не существует**.
Запрет был механизмом, а стал текстом: не совпав ни с одним инструментом,
запись не предупреждает, и промах виден только сверкой руками.

Отсюда форма, которую этот гейт стережёт:

1. ``permissions.deny`` запрещает **сервер целиком** (``mcp__github``). Такой
   запрет не зависит от имён на чужой стороне и переживает их переименования.
2. Ни в одном списке (``deny``/``allow``/``ask``) нет записей вида
   ``mcp__github__<инструмент>``. Именная запись хрупка по построению: она
   отключается молча, когда инструмент переименуют. В ``allow`` она вдобавок
   бесполезна — ``deny`` сильнее, и точечное разрешение поверх запрета сервера
   не сработает.
3. Предмет проверки существует: файл настроек читается, секция ``permissions``
   на месте. Гейт, не нашедший предмета, обязан упасть, а не зеленеть на
   пустоте.

**Что стало недоступно и почему это не потеря.** Создание PR через MCP закрыто
вместе с сервером. Агенту оно не нужно: ветка ``agent/**`` подхватывается
``agent-pr.yml``, который открывает PR **от имени владельца** по PAT, — то есть
squash-мерж всё равно атрибутирует коммит человеку. Остальное, чем работает
конвейер, умеет ``scripts/gh_rest.py`` по REST.

Запуск::

    python scripts/check_mcp_permissions.py   # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "SERVER",
    "main",
    "permission_violations",
]

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS = _ROOT / ".claude" / "settings.json"

#: Префикс инструментов GitHub-сервера MCP: он же — запись запрета целиком.
SERVER = "mcp__github"

_LISTS = ("deny", "allow", "ask")


def permission_violations(settings: dict[str, Any]) -> list[str]:
    """Нарушения формы запрета (пустой список — форма верна)."""
    problems: list[str] = []

    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        # Гейт без предмета проверки зеленел бы на любом файле — в том числе на
        # том, из которого секцию удалили.
        return [
            ".claude/settings.json: нет секции `permissions` — проверять нечего, "
            "а значит запрет MCP не действует вовсе (issue #1346)."
        ]

    deny = permissions.get("deny") or []
    if SERVER not in deny:
        problems.append(
            f".claude/settings.json: в `permissions.deny` нет `{SERVER}` — запрет "
            "сервера целиком и есть та форма, которая переживает переименования "
            "инструментов на чужой стороне (issue #1346)."
        )

    for name in _LISTS:
        for entry in permissions.get(name) or []:
            if isinstance(entry, str) and entry.startswith(f"{SERVER}__"):
                problems.append(
                    f".claude/settings.json: `permissions.{name}` содержит `{entry}` — "
                    "именная запись отключается молча при переименовании инструмента, "
                    f"а в `allow` вдобавок бесполезна: `deny {SERVER}` сильнее. "
                    "Запрещайте сервер целиком (issue #1346)."
                )

    return problems


def main() -> int:
    """Вернуть 0, если запрет MCP держится формой; иначе 1 и отчёт."""
    # Windows-консоль по умолчанию cp1252 и кириллицу не кодирует — без этого
    # шаг падал бы UnicodeEncodeError, то есть гард краснел бы не по существу.
    # Тот же приём, что в check_ruff_pin.py и check_contrast.py.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")

    if not _SETTINGS.exists():
        print(f"FAIL: {_SETTINGS} не найден — запрет MCP не действует.")
        return 1

    try:
        settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Битый JSON молча отключает ВСЕ настройки файла, включая запрет.
        print(f"FAIL: .claude/settings.json не разбирается ({exc}) — настройки не действуют.")
        return 1

    problems = permission_violations(settings)
    if problems:
        print("FAIL: форма запрета MCP нарушена:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"MCP permissions: сервер `{SERVER}` запрещён целиком, именных записей нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
