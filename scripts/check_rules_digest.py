#!/usr/bin/env python3
"""scripts/check_rules_digest.py — второй рубеж не разошёлся с ответом проекта.

Дайджест правил (`docs/agent/rules/DIGEST.md`) читается окном на старте и потому
обязан говорить правду о том, чем правило держится: строка в разделе «не
держится ничем» — это указание помнить правило самому, а в разделе «держится
гейтом» — разрешение на него положиться. Разошлось с `.rules/bindings.json` —
и окно либо тратит внимание впустую, либо не тратит там, где надо.

Сверка идёт БЕЗ сети и без клона каталога: предмет здесь — соответствие двух
файлов репозитория. Отставание дайджеста от самого каталога (появилось новое
правило) — другой вопрос и другой прогон: `generate_rules_digest.py --check`
в ночном обходе, где клон уже есть.

Проверяется три факта:

* каждое правило со статусом `active`/`unreviewed` названо в дайджесте;
* названо в ТОЙ группе, которая следует из его ответа;
* требуемые хуки зарегистрированы — `SessionStart` (без него дайджест
  существует, но окном не читается) и `PreToolUse` (без него правила 012 и 013
  снова держатся только памятью).

Запуск::

    python scripts/check_rules_digest.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

__all__ = [
    "GROUP_TITLES",
    "check_digest",
    "check_hook_is_registered",
    "digest_groups",
    "expected_groups",
    "main",
]

_ROOT = pathlib.Path(__file__).parent.parent
_DIGEST = _ROOT / "docs" / "agent" / "rules" / "DIGEST.md"
_BINDINGS = _ROOT / ".rules" / "bindings.json"
_SETTINGS = _ROOT / ".claude" / "settings.json"

#: Хуки, без которых правила осиротеют молча: событие → файл, который его
#: обслуживает. `SessionStart` кладёт дайджест в стартовый контекст (второй
#: рубеж), `PreToolUse` отвергает действия, которые CI поймать не может, —
#: heredoc с экранированием (правило 013) и пуш в чужую ветку (правило 012).
_REQUIRED_HOOKS: dict[str, str] = {
    "SessionStart": "session_start.py",
    "PreToolUse": "pre_tool_use.py",
}

#: Заголовок раздела → ключ группы. Сверяется по НАЧАЛУ заголовка, потому что
#: в конце стоит число, а оно меняется с каждым правилом.
GROUP_TITLES: dict[str, str] = {
    "Не держится ничем": "none",
    "Ответа по правилу ещё нет": "unreviewed",
    "Держится шагом процесса": "process-step",
    "Держится гейтом": "gate",
}

_HEADING_RE = re.compile(r"^## (.+?)(?: — \d+)?$")
_ITEM_RE = re.compile(r"^- \*\*(\d{3})\*\* ")


def expected_groups() -> dict[str, str]:
    """Какое правило в какой группе обязано стоять — по ответу проекта."""
    answers = json.loads(_BINDINGS.read_text(encoding="utf-8"))["rules"]
    expected: dict[str, str] = {}
    for rule_id, answer in answers.items():
        status = answer.get("status")
        if status == "unreviewed":
            expected[rule_id] = "unreviewed"
        elif status == "active":
            expected[rule_id] = answer.get("mechanism") or "none"
    return expected


def digest_groups(text: str | None = None) -> dict[str, str]:
    """Какое правило в какой группе стоит сейчас — по самому дайджесту."""
    source = _DIGEST.read_text(encoding="utf-8") if text is None else text
    found: dict[str, str] = {}
    group = ""
    for line in source.splitlines():
        if (heading := _HEADING_RE.match(line)) is not None:
            title = heading.group(1)
            group = next(
                (key for prefix, key in GROUP_TITLES.items() if title.startswith(prefix)), ""
            )
            continue
        if (item := _ITEM_RE.match(line)) is not None and group:
            found[item.group(1)] = group
    return found


def check_digest(errors: list[str]) -> None:
    """Состав и группы дайджеста совпадают с ответом проекта."""
    if not _DIGEST.exists():
        errors.append(
            "дайджеста нет — окно на старте не прочитает ни одного правила; соберите: "
            "python scripts/generate_rules_digest.py --catalogue <клон каталога>"
        )
        return
    expected = expected_groups()
    actual = digest_groups()

    for rule_id, group in sorted(expected.items()):
        if rule_id not in actual:
            errors.append(f"правило {rule_id} ({group}) не названо в дайджесте")
        elif actual[rule_id] != group:
            errors.append(
                f"правило {rule_id}: в дайджесте «{actual[rule_id]}», "
                f"а в .rules/bindings.json «{group}»"
            )
    for rule_id in sorted(set(actual) - set(expected)):
        errors.append(f"правило {rule_id} есть в дайджесте, но ответа по нему нет")


def check_hook_is_registered(errors: list[str], settings: str | None = None) -> None:
    """Все требуемые хуки объявлены: снятый хук осиротит правило молча."""
    raw = _SETTINGS.read_text(encoding="utf-8") if settings is None else settings
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f".claude/settings.json не разбирается: {exc}")
        return
    hooks = data.get("hooks", {})
    for event, script in _REQUIRED_HOOKS.items():
        commands = [
            hook.get("command", "")
            for entry in hooks.get(event, [])
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        ]
        if not any(script in command for command in commands):
            errors.append(
                f"хук {event} не зовёт {script} — файл на месте, но механизма нет: "
                "правило снова держится только памятью окна"
            )


def main() -> int:
    """0 — второй рубеж на месте; 1 — разошёлся."""
    errors: list[str] = []
    check_digest(errors)
    check_hook_is_registered(errors)

    if errors:
        print("второй рубеж разошёлся с ответом проекта:", file=sys.stderr)
        for error in errors:
            print(f"  • {error}", file=sys.stderr)
        print(
            "\nПересоберите дайджест: "
            "python scripts/generate_rules_digest.py --catalogue <клон каталога>",
            file=sys.stderr,
        )
        return 1

    counted = len(expected_groups())
    events = ", ".join(_REQUIRED_HOOKS)
    print(f"второй рубеж на месте: правил в дайджесте {counted}, хуки объявлены — {events}")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
