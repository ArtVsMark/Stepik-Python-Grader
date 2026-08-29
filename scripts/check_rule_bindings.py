#!/usr/bin/env python3
"""scripts/check_rule_bindings.py — ответ проекта каталогу правил (issue #1351).

Каталог [claude-code-playbook](https://github.com/ArtVsMark/claude-code-playbook)
отдаёт правила машиночитаемо, а проект-потребитель отвечает, что он с каждым
сделал: статус, чем держится и где. Контракт — `export/README.md` каталога,
схема ``1.0``; заготовка — `templates/bindings.json` там же.

**Почему файл живёт здесь, а не в каталоге.** Здесь живёт механизм: одно и то
же правило в проекте с полным конвейером держится гейтом, в витрине — шагом
сборки, в статическом сайте ничем. Каталог про чужие гейты не знает и проверять
их не может — «у нас держится гейтом» это утверждение проекта, и отвечает за
него его же конвейер, то есть вот этот скрипт.

Отсюда же дефект, из которого выросла задача: уровень «чем держится» указатель
`docs/agent/rules/README.md` брал из раздела «Механизм» **каталога**, поэтому у
88 правил из 89 читалось «не объявлено». Поле пустовало не потому, что у нас
нет гейтов, а потому что заведено не в том репозитории.

Проверяется:

1. **Схема и поля по статусу.** ``active`` требует ``mechanism`` и ``where``;
   ``rejected`` и ``not-applicable`` — ``why``. Отрицательное решение без
   причины через полгода неотличимо от «не дошли руки».
2. **Заявленное существует.** ``where`` с путём к файлу проверяется на диске:
   ответ проекта — это декларация, а декларация обязана сходиться с фактом.
3. **Полнота против каталога** (только с ``--catalogue``): ответ нужен по
   КАЖДОМУ правилу, а не по тем, до которых дошли руки. Правило без записи
   попадает в метрику нерассмотренных, а не исчезает.

Метрика — **сколько правил не обеспечено ничем**: ``unreviewed`` плюс
``active`` с ``mechanism: none``. Она не просто «должна уменьшаться» — её держит
храповик :data:`UNHELD_BUDGET`: правило, принятое на словах, обязано быть либо
закрыто гейтом, либо **записано документом** (``process-step`` с указанием
места). ``none`` означает, что правило не держится ничем, и такого быть не
должно; бюджет опускается починкой, а не правкой числа. На пустом входе гейт
падает, а не зеленеет.

Запуск::

    python scripts/check_rule_bindings.py                        # сверка формата
    python scripts/check_rule_bindings.py --catalogue <клон>      # + полнота
    python scripts/check_rule_bindings.py --catalogue <клон> --sync  # обновить
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "BINDINGS",
    "MECHANISMS",
    "STATUSES",
    "UNHELD_BUDGET",
    "binding_violations",
    "main",
    "unheld_count",
]

_ROOT = Path(__file__).resolve().parent.parent
BINDINGS = _ROOT / ".rules" / "bindings.json"

STATUSES = ("active", "rejected", "not-applicable", "unreviewed")
MECHANISMS = ("gate", "process-step", "none")

#: Сколько правил ещё не закреплено ничем. Не «столько допустимо», а «столько
#: осталось»: каждое такое правило действует ровно до тех пор, пока о нём помнит
#: окно. Число опускается починкой — гейтом или записью решения в документ.
UNHELD_BUDGET = 0

#: Расширения, по которым `where` считается путём, а не описанием шага.
_PATH_SUFFIXES = (".py", ".yml", ".yaml", ".json", ".md", ".txt")


def _looks_like_path(where: str) -> str | None:
    """Первое слово `where`, если оно похоже на путь к файлу; иначе ``None``."""
    head = where.split()[0].strip("`,") if where.split() else ""
    return head if head.endswith(_PATH_SUFFIXES) and "/" in head else None


def binding_violations(data: dict[str, Any], *, root: Path | None = None) -> list[str]:
    """Нарушения контракта в ответе проекта (пустой список — чисто)."""
    base = root if root is not None else _ROOT
    problems: list[str] = []

    if data.get("schema") != "1.0":
        problems.append(
            f"schema={data.get('schema')!r} — контракт каталога сегодня 1.0; "
            "читатель обязан игнорировать незнакомые поля, но не версию"
        )

    rules = data.get("rules")
    if not isinstance(rules, dict) or not rules:
        # Гейт без предмета проверки обязан падать, а не зеленеть на пустоте.
        return [*problems, ".rules/bindings.json: раздел `rules` пуст — отвечать не о чем"]

    for rule_id, raw in sorted(rules.items()):
        if not isinstance(raw, dict):
            problems.append(f"правило {rule_id}: запись не объект")
            continue

        status = raw.get("status")
        if status not in STATUSES:
            problems.append(f"правило {rule_id}: статус {status!r} не из {', '.join(STATUSES)}")
            continue

        if status == "active":
            mechanism = raw.get("mechanism")
            if mechanism not in MECHANISMS:
                problems.append(
                    f"правило {rule_id}: active без механизма из {', '.join(MECHANISMS)} — "
                    "«принято» без ответа на вопрос «чем держится» и есть фикция"
                )
            where = str(raw.get("where") or "")
            if not where:
                problems.append(f"правило {rule_id}: active без `where` — где именно держится?")
            else:
                named = _looks_like_path(where)
                if named is not None and not (base / named).exists():
                    problems.append(
                        f"правило {rule_id}: `where` указывает на {named}, которого нет — "
                        "предмет правила изменился, а запись осталась"
                    )
        elif status in {"rejected", "not-applicable"} and not str(raw.get("why") or "").strip():
            problems.append(
                f"правило {rule_id}: {status} без причины — через полгода это "
                "неотличимо от «не дошли руки»"
            )

    return problems


def unheld_count(data: dict[str, Any]) -> tuple[int, int]:
    """Сколько правил не обеспечено ничем и сколько всего отвечено."""
    rules = data.get("rules") or {}
    unheld = sum(
        1
        for raw in rules.values()
        if isinstance(raw, dict)
        and (
            raw.get("status") == "unreviewed"
            or (raw.get("status") == "active" and raw.get("mechanism") == "none")
        )
    )
    return unheld, len(rules)


def _export_ids(catalogue: Path) -> set[str]:
    """Номера правил из машинного экспорта каталога."""
    export = catalogue / "export" / "rules.json"
    if not export.exists():
        raise FileNotFoundError(
            f"{export}: экспорта каталога нет — клонируйте "
            "https://github.com/ArtVsMark/claude-code-playbook"
        )
    data = json.loads(export.read_text(encoding="utf-8"))
    return {str(rule["id"]) for rule in data.get("rules", []) if rule.get("id")}


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если ответ проекта сходится с контрактом; иначе 1."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, help="клон каталога: проверить полноту ответа")
    args = parser.parse_args(argv)

    if not BINDINGS.exists():
        print(f"FAIL: {BINDINGS} не найден — проект каталогу не отвечает вовсе.")
        return 1

    try:
        data = json.loads(BINDINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: .rules/bindings.json не разбирается ({exc}).")
        return 1

    problems = binding_violations(data)

    if args.catalogue is not None:
        try:
            expected = _export_ids(args.catalogue)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"FAIL: {exc}")
            return 1
        answered = set(data.get("rules") or {})
        missing = sorted(expected - answered, key=int)
        if missing:
            problems.append(
                f"нет ответа по {len(missing)} правил(ам): {', '.join(missing[:10])}"
                + ("…" if len(missing) > 10 else "")
                + " — ответ нужен по каждому, иначе нерассмотренное просто исчезает"
            )
        stale = sorted(answered - expected, key=lambda item: int(item) if item.isdigit() else 0)
        if stale:
            problems.append(f"ответ на несуществующие правила: {', '.join(stale)}")

    unheld, total = unheld_count(data)
    if problems:
        print("FAIL: ответ каталогу правил разошёлся с контрактом:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if unheld > UNHELD_BUDGET:
        print(
            f"FAIL: не обеспечено ничем — {unheld} правил(а) при бюджете {UNHELD_BUDGET}.\n"
            "Правило без механизма обязано быть записано документом "
            "(mechanism: process-step + where), иначе оно действует ровно до тех пор, "
            "пока о нём помнит окно. Бюджет опускают починкой, а не правкой числа."
        )
        return 1

    print(f"Ответ каталогу: {total} правил(а), не обеспечено ничем — {unheld}.")
    print(f"Бюджет — {UNHELD_BUDGET}: правило без механизма записывается документом.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
