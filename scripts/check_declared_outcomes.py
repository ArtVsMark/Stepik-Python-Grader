#!/usr/bin/env python3
"""scripts/check_declared_outcomes.py — прогнан каждый объявленный исход, а не первый.

Правило 145 каталога: механизм, у которого объявлено больше одного исхода,
проверяется прогоном **каждого**, а не только успешного. Прогон одного пути
подтверждает, что механизм запускается, — и ничего больше. Необъявленные ветки
при этом обычно и оказываются сломанными: их никто не видел работающими.

Предмет проверяемый: **коды возврата скрипта**. Если ``main()`` объявляет
несколько разных кодов, то у каждого НЕуспешного должен быть тест, ожидающий
именно его. Иначе «ждать» и «не отработала» существуют только в исходнике:
написаны, задокументированы и ни разу не прогнаны.

Успешный путь под правило не подпадает: его прогоняет живой предмет в каждой
проверке («на репозитории чисто»), и требовать для него отдельного вызова
``main()`` значило бы добавить обряд, а не проверку.

**Долг объявлен числом** (:data:`BUDGET`): у части скриптов ветка отказа
никогда не прогонялась, и это состояние честнее показать счётчиком, чем
внести в исключения поимённо. Бюджет опускают починкой, а не правкой числа.

Что считается прогоном: любое утверждение в тестах, где этот код ожидается, —
``== 2``, ``== gh_rest.EXIT_WAIT``, ``returncode == 1``. Форму не навязываем:
гейт следит за наличием прогона, а не за его оформлением.

Запуск::

    python scripts/check_declared_outcomes.py
"""

from __future__ import annotations

import ast
import contextlib
import pathlib
import re
import sys

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["BUDGET", "KNOWN_DEBT", "declared_codes", "main", "outcomes_never_run"]

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_TESTS = _ROOT / "tests"

#: Скрипты, чьи исходы прогоняются иначе, — с причиной.
KNOWN_DEBT: dict[str, str] = {
    "preflight.py": (
        "исходы прогоняются самим гейтом на каждом коммите: он и есть тот прогон, "
        "а его собственные тесты проверяют шаги по отдельности"
    ),
}

#: Сколько объявленных исходов пока не прогнано. Храповик: число опускают
#: починкой (добавили тест на ветку отказа), а не правкой самого числа —
#: иначе долг перестанет быть виден и перестанет уменьшаться.
BUDGET = 5

#: Как называются коды в проекте: имя → число, чтобы `EXIT_WAIT` и `2` считались
#: одним и тем же исходом, а не двумя.
_NAMED_CODES = {"EXIT_OK": 0, "EXIT_FAIL": 1, "EXIT_WAIT": 2, "EXIT_UNKNOWN": 2}


def declared_codes(source: str) -> set[int]:
    """Коды возврата, объявленные в ``main()`` скрипта."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    codes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "main":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            value = child.value
            if isinstance(value, ast.Constant) and isinstance(value.value, int):
                codes.add(value.value)
            elif isinstance(value, ast.Attribute) and value.attr in _NAMED_CODES:
                codes.add(_NAMED_CODES[value.attr])
            elif isinstance(value, ast.Name) and value.id in _NAMED_CODES:
                codes.add(_NAMED_CODES[value.id])
    return codes


def _tests_text(module: str, tests: dict[str, str]) -> str:
    """Тексты тестов, которые вообще упоминают этот модуль."""
    return "\n".join(text for text in tests.values() if module in text)


def outcomes_never_run(
    scripts: dict[str, str] | None = None, tests: dict[str, str] | None = None
) -> list[tuple[str, int]]:
    """Объявленные исходы, которые ни разу не прогнаны.

    Args:
        scripts: подмена скриптов для тестов: имя → текст.
        tests: подмена тестов: имя → текст.

    Returns:
        Пары (скрипт, код возврата).
    """
    if scripts is None:
        scripts = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_SCRIPTS.glob("*.py"))
        }
    if tests is None:
        tests = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_TESTS.glob("test_*.py"))
        }

    problems: list[tuple[str, int]] = []
    for name, source in scripts.items():
        if name in KNOWN_DEBT:
            continue
        codes = declared_codes(source)
        if len(codes) < 2:
            # Один исход — прогонять «каждый» нечего, правило молчит.
            continue
        related = _tests_text(pathlib.Path(name).stem, tests)
        if not related:
            continue
        for code in sorted(codes):
            # Успешный путь прогоняется живым предметом каждой проверкой («на
            # репозитории чисто»), и требовать для него отдельного вызова
            # `main()` значило бы добавить обряд, а не проверку. Правило про
            # ветки, которых никто не видел работающими, — это отказ и «не
            # отработала».
            if code == 0:
                continue
            names = [key for key, value in _NAMED_CODES.items() if value == code]
            pattern = "|".join([str(code), *names])
            if not re.search(rf"==\s*(?:\w+\.)?(?:{pattern})\b", related):
                problems.append((name, code))
    return problems


def main() -> int:
    """0 — прогнан каждый объявленный исход; 1 — какой-то не прогнан."""
    problems = outcomes_never_run()
    for name, code in problems:
        print(f"  • {name}: код {code} ни разу не прогнан")

    if len(problems) > BUDGET:
        print(
            f"\nнепрогнанных исходов {len(problems)} при бюджете {BUDGET}. "
            "Прогон одного пути подтверждает, что механизм запускается, — и ничего "
            "больше: ветка, которую никто не видел работающей, обычно и оказывается "
            "сломанной. Бюджет опускают починкой, а не правкой числа.",
            file=sys.stderr,
        )
        return 1

    print(f"\nнепрогнанных исходов {len(problems)} при бюджете {BUDGET}")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
