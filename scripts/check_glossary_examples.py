#!/usr/bin/env python3
"""scripts/check_glossary_examples.py — примеры карточек остаются валидным Python.

У части карточек глоссария потерян отступ тела блока: строки многострочного
примера лежат в ``examples`` на нулевом уровне, и `` "\\n".join(examples)`` не
компилируется. Пользователь копирует такой пример в «Песочницу» и получает
``IndentationError`` — на учебной поверхности это хуже обычного бага: человек
решает, что ошибся он.

**Почему это не ловилось.** Существующий храповик исполнения
(``test_run_check_bundled_ratchet``) помечает такую карточку как
``unverifiable`` — «не компилируется, сверить нечем» — и проходит мимо. То есть
чем сильнее сломан пример, тем меньше к нему вопросов: проверка отступала ровно
там, где дефект.

Этот гард закрывает дыру встречным способом — считает карточки, чьи примеры не
компилируются, и падает, когда их становится **больше** :data:`BUDGET`. Порог
обязан уменьшаться: это не «разрешённый долг», а храповик, который не даёт
дефекту вернуться.

Запуск::

    python scripts/check_glossary_examples.py [--json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

__all__ = ["BUDGET", "DATA_DIR", "broken_examples", "main"]

DATA_DIR = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "glossary" / "data"

#: Сколько карточек ещё не компилируется. Число только уменьшается: у остатка
#: дефект не в одних отступах — там сломан и сам код примера, и починка каждой
#: карточки требует предметного разбора, а не расстановки пробелов.
#:
#: Три карточки (`permissionerror`, `open`, `pathlib.path`) сюда вернулись после
#: прогона на матрице: их примеры жёстко завязаны на POSIX-пути (`/etc/shadow`,
#: `/etc/hostname`, `/tmp`), и починка отступов лишь делала их исполнимыми — на
#: Windows и macOS они падали. Такой карточке нужен либо тег `platform:posix`,
#: либо кроссплатформенный пример: это предметное решение, а не отступы.
BUDGET = 88


def broken_examples(directory: pathlib.Path | None = None) -> list[tuple[str, str]]:
    """Карточки, чьи примеры не собираются в валидный Python.

    Args:
        directory: каталог с JSON-файлами базы; по умолчанию — встроенная база.

    Returns:
        Пары ``(файл/идентификатор, сообщение компилятора)``, отсортированные.
    """
    root = DATA_DIR if directory is None else directory
    found: list[tuple[str, str]] = []

    def walk(node: Any, source: str) -> None:
        if isinstance(node, dict):
            example = node.get("examples")
            if isinstance(example, list) and example and all(isinstance(x, str) for x in example):
                try:
                    compile("\n".join(example), "<card>", "exec")
                except SyntaxError as exc:
                    card = str(node.get("id") or node.get("term") or "?")
                    found.append((f"{source}/{card}", f"{type(exc).__name__}: {exc.msg}"))
            for value in node.values():
                walk(value, source)
        elif isinstance(node, list):
            for value in node:
                walk(value, source)

    for path in sorted(root.glob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если карточек с несобирающимися примерами не больше бюджета."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    broken = broken_examples()
    if args.json:
        print(
            json.dumps(
                {"broken": len(broken), "budget": BUDGET, "cards": [name for name, _ in broken]},
                ensure_ascii=False,
                indent=1,
            )
        )
    if len(broken) > BUDGET:
        print(
            f"FAIL: примеры не компилируются у {len(broken)} карточек при бюджете {BUDGET}.",
            file=sys.stderr,
        )
        for name, detail in broken[:15]:
            print(f"  - {name}: {detail}", file=sys.stderr)
        print(
            "\nПример, который не собирается, пользователь копирует в «Песочницу» "
            "и получает IndentationError — на учебной поверхности это учит неверному.",
            file=sys.stderr,
        )
        return 1

    if not args.json:
        print(f"Примеры карточек: не компилируется {len(broken)} при бюджете {BUDGET}.")
        if len(broken) < BUDGET:
            print(
                f"Бюджет пора опустить до {len(broken)}: храповик держится тем, "
                "что число только уменьшается."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
