#!/usr/bin/env python3
"""scripts/check_gate_tests.py — у каждого гейта есть прогон того, что он обязан отвергнуть.

Правило 140 каталога: пока через гейт нарочно не прогнали предмет, который он
**обязан не пропустить**, утверждение «гейт не пропустит» остаётся обещанием.
И написано оно обычно там, где его прочитают и поверят, — в своде.

Проверка ищет для каждого ``scripts/check_*.py`` тест, который его загружает, и
в нём — **оба** случая, потому что у проверяющего две ошибки и они разной цены
(правило 097):

* **отвергающий** — утверждение, ожидающее непустой список находок либо
  ненулевой код возврата. Без него набор зеленеет и тогда, когда проверка
  сломана и не находит ничего никогда (правило 075);
* **пропускающий** — предмет, который гейт обязан пропустить. Без него
  незамеченной остаётся вторая ошибка: гейт, краснеющий на здоровом коде.
  Она дороже, чем кажется, — такой гейт не чинят, а отключают, и вместе с ним
  исчезает первая проверка тоже.

Эвристика намеренно мягкая. У проверяющего две ошибки (правило 097), и здесь
дешевле ложное «прошло»: гейт, который не заметит слабого теста, стоит меньше,
чем гейт, который краснеет на здоровом наборе и которого начнут обходить.

**Долг объявлен числом.** Гейты без двустороннего набора перечислены в
:data:`KNOWN_DEBT` вместе с причиной. Список — храповик: он может только
уменьшаться, и тест на это есть.

Запуск::

    python scripts/check_gate_tests.py
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

__all__ = [
    "ACCEPTANCE_MARKERS",
    "KNOWN_DEBT",
    "REJECTION_MARKERS",
    "gates_without_rejection",
    "main",
    "tests_for",
]

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_TESTS = _ROOT / "tests"

#: Гейты, у которых двустороннего набора ещё нет. Причина обязательна: молча
#: внесённое исключение — это отключённая проверка, а не объявленный долг.
KNOWN_DEBT: dict[str, str] = {
    "check_contrast.py": (
        "считает контраст по токенам темы; отвергаемый предмет — палитра, которой нет"
    ),
    "check_issue_checklists.py": "ходит в трекер; отвергаемый предмет — состояние чужого issue",
}

#: Признаки отвергающего случая в тексте теста. Список закрытый: догадываться
#: по слову «assert» нельзя — тогда любой односторонний набор пройдёт.
REJECTION_MARKERS: tuple[str, ...] = (
    # Ожидание непустой находки — как бы её ни звали в конкретном гейте.
    "assert errors",
    "assert problems",
    "assert violations",
    "assert broken",
    "assert missing",
    "assert any(",
    "assert len(",
    # Ожидание непустого результата разбора: гейт назвал предмет, а не смолчал.
    '== {"',
    "!= []",
    "!= {}",
    # Ожидание отказа как кода возврата.
    "returncode == 1",
    ") == 1",
    "EXIT_FAIL",
    "pytest.raises",
)

#: Признаки пропускающего случая: ожидание ПУСТОГО результата или нулевого
#: кода. Он же обычно и «живой репозиторий чист» — этого достаточно: предмет,
#: который гейт обязан пропустить, здесь настоящий.
ACCEPTANCE_MARKERS: tuple[str, ...] = (
    "== []",
    "== {}",
    "== set()",
    "errors == []",
    "is None",
    ") == 0",
    "returncode == 0",
    "EXIT_OK",
    "assert not ",
)

_MODULE_RE = re.compile(r"[\w/]*(check_[a-z0-9_]+)")


def tests_for(gate: str, tests: dict[str, str] | None = None) -> list[str]:
    """Файлы тестов, которые загружают этот гейт (по имени модуля в тексте)."""
    module = pathlib.Path(gate).stem
    if tests is None:
        tests = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_TESTS.glob("test_*.py"))
        }
    return [name for name, text in tests.items() if module in text]


def gates_without_rejection(
    gates: list[str] | None = None, tests: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    """Гейты без прогона отвергаемого предмета — как (гейт, что именно не так)."""
    if gates is None:
        gates = [path.name for path in sorted(_SCRIPTS.glob("check_*.py"))]
    if tests is None:
        tests = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_TESTS.glob("test_*.py"))
        }

    problems: list[tuple[str, str]] = []
    for gate in gates:
        if gate in KNOWN_DEBT:
            continue
        related = tests_for(gate, tests)
        if not related:
            problems.append(
                (gate, "тестов нет вовсе — утверждение «не пропустит» ничем не проверено")
            )
            continue
        joined = "\n".join(tests[name] for name in related)
        if not any(marker in joined for marker in REJECTION_MARKERS):
            problems.append(
                (
                    gate,
                    f"в {', '.join(related)} нет отвергающего случая — набор односторонний "
                    "и зеленеет даже у сломанной проверки",
                )
            )
        elif not any(marker in joined for marker in ACCEPTANCE_MARKERS):
            problems.append(
                (
                    gate,
                    f"в {', '.join(related)} нет пропускающего случая — вторая ошибка "
                    "проверяющего (ложное «не прошло») осталась бы незамеченной, "
                    "а гейт, краснеющий на здоровом коде, отключают целиком",
                )
            )
    return problems


def main() -> int:
    """0 — у каждого гейта двусторонний набор; 1 — нет."""
    problems = gates_without_rejection()
    if problems:
        print("гейт не проверен тем, что обязан отвергнуть:", file=sys.stderr)
        for gate, reason in problems:
            print(f"  • {gate}: {reason}", file=sys.stderr)
        print(
            "\nПрогоните через гейт подделанный предмет и убедитесь, что он краснеет. "
            "Долг объявляется в KNOWN_DEBT вместе с причиной, а не молчанием.",
            file=sys.stderr,
        )
        return 1

    total = len(list(_SCRIPTS.glob("check_*.py")))
    print(
        f"гейты проверены отвергаемым предметом: {total - len(KNOWN_DEBT)} из {total}, "
        f"объявленный долг — {len(KNOWN_DEBT)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
