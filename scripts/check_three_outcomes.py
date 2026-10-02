#!/usr/bin/env python3
"""scripts/check_three_outcomes.py — у проверки три исхода, а не два.

Правило 039 каталога: «чисто», «нашли проблему» и **«проверка не отработала»** —
три разных исхода с тремя разными действиями. Третий обычно и теряется: скрипт,
сходивший во внешний источник и не получивший ответа, печатает то же самое, что
и скрипт, ничего не нашедший. Разница видна только в том, что чинить: находку
чинит владелец предмета, неотработавший механизм — тот, кто его запускает.

Предмет здесь узкий и потому проверяемый: **скрипты, которые ходят в GitHub**
(импортируют ``gh_rest``). У внешнего источника отказ — штатное состояние: нет
прав, кончилась квота, сеть не ответила. Такой скрипт обязан отличать это от
чистого результата — перехватывать ``GitHubError``/``RateLimited`` и возвращать
третий код (``2`` или :data:`gh_rest.EXIT_WAIT`), а не падать трассировкой и не
молчать.

**Долг объявляется, а не замалчивается** (правило 057). Скрипт, у которого
третий исход осознанно не нужен, перечислен в :data:`KNOWN_DEBT` с причиной.
Молча внесённое исключение — это отключённая проверка.

Запуск::

    python scripts/check_three_outcomes.py
"""

from __future__ import annotations

import contextlib
import pathlib
import re
import sys

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["KNOWN_DEBT", "OUTCOME_MARKERS", "main", "scripts_without_third_outcome"]

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"

#: Скрипты, которым третий исход осознанно не нужен, — с причиной.
KNOWN_DEBT: dict[str, str] = {
    "nightly_checks.py": (
        "сам является адресатом: неотработавшую проверку он называет отдельным "
        "разделом задачи, а красным прогон делать нельзя — это снова сигнал без "
        "адресата (правило 142)"
    ),
    "gh_rest.py": (
        "источник самих исходов: EXIT_WAIT и коды возврата объявлены здесь, "
        "и проверять его собственным правилом значило бы проверять определение"
    ),
}

#: Признак того, что отказ внешнего источника отличён от результата.
OUTCOME_MARKERS: tuple[str, ...] = (
    "EXIT_WAIT",
    "EXIT_UNKNOWN",
    "RateLimited",
    "return 2",
    "= 2\n",
)

_IMPORTS_GH = re.compile(r"^\s*(?:import gh_rest|from gh_rest import)", re.MULTILINE)


def scripts_without_third_outcome(
    sources: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Скрипты, ходящие в GitHub без отдельного исхода «не отработала».

    Args:
        sources: подмена содержимого для тестов: имя файла → текст.

    Returns:
        Пары (имя, чего не хватает).
    """
    if sources is None:
        sources = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_SCRIPTS.glob("*.py"))
        }

    problems: list[tuple[str, str]] = []
    for name, text in sources.items():
        if name in KNOWN_DEBT or not _IMPORTS_GH.search(text):
            continue
        if not any(marker in text for marker in OUTCOME_MARKERS):
            problems.append(
                (
                    name,
                    "ходит в GitHub, но отказ источника не отличён от результата: "
                    "нет ни перехвата RateLimited, ни третьего кода возврата — "
                    "«прав нет» и «нарушений нет» выглядят одинаково",
                )
            )
    return problems


def main() -> int:
    """0 — третий исход есть у всех, кто ходит наружу; 1 — нет."""
    problems = scripts_without_third_outcome()
    if problems:
        print("у проверки два исхода вместо трёх:", file=sys.stderr)
        for name, reason in problems:
            print(f"  • {name}: {reason}", file=sys.stderr)
        print(
            "\nПерехватите gh_rest.RateLimited (код EXIT_WAIT — «ждать») и "
            "gh_rest.GitHubError (код 2 — «не отработала»), либо объявите долг "
            "в KNOWN_DEBT с причиной.",
            file=sys.stderr,
        )
        return 1

    watched = sum(
        1 for path in _SCRIPTS.glob("*.py") if _IMPORTS_GH.search(path.read_text("utf-8"))
    )
    print(
        f"третий исход отличён у всех, кто ходит наружу: скриптов {watched}, "
        f"объявленный долг — {len(KNOWN_DEBT)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
