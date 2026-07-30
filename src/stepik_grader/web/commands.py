"""commands.py — единый реестр команд (docs/dev/web-current.md § Реестр команд).

Список ``COMMANDS`` питает action cards в detail panel: «какую кнопку когда
показывать» решается в одном месте, а не дублируется по фронтенду. Исторически
тот же список питал ещё палитру ``Ctrl+K`` и нижние сценарные кнопки — оба
интерфейса сняты (#658 и #368 соответственно), реестр остался.

Упрощение относительно дизайн-документа (сознательное, issue #125): вместо
свободного predicate-строки (``"case.glossary_ids != []"``) — фиксированный
словарь тегов ``when``. Полноценный expression-evaluator (или ``eval()``) для
7 команд — неоправданная сложность и лишняя поверхность; при появлении
плагинов команд (out of scope здесь) можно расширить.
"""

from __future__ import annotations

from typing import Any

__all__ = ["COMMANDS", "filter_commands"]

# MVP-набор — ровно то, что реализовано в #125. Никогда не добавлять сюда
# create_test/compare_solutions — они design-only (docs/dev/design/web-design.md § Action
# cards, отложенные), реализация вне скоупа этого issue.
COMMANDS: list[dict[str, Any]] = [
    {
        "id": "run_again",
        "title": {"ru": "Повторить проверку", "en": "Run again"},
        "icon": "play",
        "keywords": ["run", "again", "повтор", "запустить"],
        "when": "always",
        "shortcut": None,
    },
    {
        "id": "copy_input",
        "title": {"ru": "Скопировать вход", "en": "Copy input"},
        "icon": "clipboard",
        "keywords": ["copy", "input", "stdin", "скопировать", "вход"],
        "when": "has_stdin",
        "shortcut": None,
    },
    {
        "id": "copy_output",
        "title": {"ru": "Скопировать вывод", "en": "Copy output"},
        "icon": "clipboard",
        "keywords": ["copy", "output", "скопировать", "вывод"],
        "when": "has_output",
        "shortcut": None,
    },
    {
        "id": "explain_error",
        "title": {"ru": "Объяснить ошибку", "en": "Explain error"},
        "icon": "info",
        "keywords": ["explain", "hint", "error", "объяснить", "ошибка"],
        "when": "is_failure",
        "shortcut": None,
    },
    {
        "id": "open_glossary",
        "title": {"ru": "Открыть глоссарий", "en": "Open glossary"},
        "icon": "book",
        "keywords": ["glossary", "help", "глоссарий", "справка"],
        "when": "has_glossary",
        "shortcut": "g",
    },
    {
        "id": "toggle_theme",
        "title": {"ru": "Переключить тему", "en": "Toggle theme"},
        "icon": "moon",
        "keywords": ["theme", "dark", "light", "тема"],
        "when": "always",
        "shortcut": None,
    },
    {
        "id": "switch_section",
        "title": {"ru": "Переключить раздел", "en": "Switch section"},
        "icon": "layout",
        "keywords": ["section", "glossary", "check", "раздел"],
        "when": "always",
        "shortcut": None,
    },
]


def filter_commands(context: set[str] | None = None) -> list[dict[str, Any]]:
    """Команды, доступные в данном контексте.

    ``context`` — набор тегов текущего состояния (напр. ``{"is_failure",
    "has_stdin"}``); ``None`` — вернуть весь реестр без фильтрации (тег
    ``"always"`` проходит в любом случае).
    """
    if context is None:
        return list(COMMANDS)
    return [c for c in COMMANDS if c["when"] == "always" or c["when"] in context]
