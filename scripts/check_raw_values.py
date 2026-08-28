#!/usr/bin/env python3
"""scripts/check_raw_values.py — наружу уходит число, а не его вид.

Правило 122 каталога: форматирование — операция с потерей. Отдавая величину для
показа, отдают рядом исходное число; разбор строки обратно — это восстановление
того, что отдающий сам и уничтожил.

Предмет здесь — **ответы веб-слоя**: словари, которые уезжают в JSON. Поле вида
``"time": f"{seconds:.1f} с"`` заставляет клиента разбирать строку обратно —
причём вместе с локалью, единицей и округлением, которых он не выбирал. Сырое
число (``duration_ms``, ``total_time``) клиент отформатирует сам и так, как
нужно его экрану.

Что НЕ считается нарушением и почему: человеческие поля — ``message``,
``error``, ``hint``, ``title``, ``label``, ``summary`` — это текст по замыслу, и
числа внутри фразы («не удалось за 3 попытки») никакой величиной наружу не
являются. Требовать сырое рядом с ними значило бы ломать тексты ради буквы.

Запуск::

    python scripts/check_raw_values.py
"""

from __future__ import annotations

import ast
import contextlib
import pathlib
import sys

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["HUMAN_KEYS", "formatted_numbers_in_responses", "main"]

_ROOT = pathlib.Path(__file__).parent.parent
_WEB = _ROOT / "src" / "stepik_grader" / "web"

#: Ключи, где строка и есть содержимое: их форматируют для человека.
HUMAN_KEYS: frozenset[str] = frozenset(
    {
        "message",
        "error",
        "hint",
        "title",
        "label",
        "summary",
        "text",
        "reason",
        "detail",
        "description",
        "status_text",
    }
)

#: Спецификаторы, по которым видно, что форматируют ЧИСЛО, а не подставляют имя.
_NUMERIC_SPECS = (".0f", ".1f", ".2f", ".3f", ",d", ".0%", ".1%", "%")


def _is_numeric_format(node: ast.JoinedStr) -> bool:
    """Есть ли внутри f-строки числовое форматирование величины."""
    for part in node.values:
        if not isinstance(part, ast.FormattedValue):
            continue
        spec = part.format_spec
        if spec is None:
            continue
        rendered = "".join(
            piece.value
            for piece in getattr(spec, "values", [])
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
        )
        if any(marker in rendered for marker in _NUMERIC_SPECS):
            return True
    return False


def formatted_numbers_in_responses(
    sources: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Поля ответов, где наружу уходит вид числа вместо самого числа.

    Args:
        sources: подмена содержимого для тестов: путь → текст.

    Returns:
        Пары (путь, ключ поля).
    """
    if sources is None:
        sources = {
            str(path.relative_to(_ROOT)): path.read_text(encoding="utf-8")
            for path in sorted(_WEB.rglob("*.py"))
            if "__pycache__" not in path.parts
        }

    problems: list[tuple[str, str]] = []
    for name, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value in HUMAN_KEYS:
                    continue
                if isinstance(value, ast.JoinedStr) and _is_numeric_format(value):
                    problems.append((name, key.value))
    return problems


def main() -> int:
    """0 — наружу уходят числа; 1 — где-то уходит их вид."""
    problems = formatted_numbers_in_responses()
    if problems:
        print("в ответ уходит вид числа вместо самого числа:", file=sys.stderr)
        for name, key in problems:
            print(f"  • {name}: поле {key}", file=sys.stderr)
        print(
            "\nФорматирование теряет данные: клиенту придётся разбирать строку обратно "
            "вместе с единицей и округлением, которых он не выбирал. Отдайте сырое "
            "число, а показ оставьте клиенту.",
            file=sys.stderr,
        )
        return 1

    print("ответы веб-слоя несут числа, а не их вид")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
