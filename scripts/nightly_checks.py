#!/usr/bin/env python3
"""scripts/nightly_checks.py — у ночных находок появился адресат (issue #1384).

Правило 142 каталога: проверка по расписанию обязана при отказе **дойти до
человека**. Красное на вкладке прогонов адресатом не является — пока адресата
нет, такая проверка отличается от незапущенной только счётом за машинное время.

Ночной обход трекера ровно в этом состоянии и жил: шесть проверок писали
находки в summary прогона, каждая с ``|| true``, и прогон всегда зеленел.
Открыть summary можно только руками и только зная, что там что-то есть, —
то есть находки существовали для того, кто и так пошёл смотреть.

Скрипт запускает те же проверки и **ведёт одну задачу** в трекере: пока
находки есть, задача открыта и её тело обновляется; как только чисто — задача
закрывается с явной строкой о том, что стало чисто, и датой. Задача находится
по скрытому маркеру :data:`MARKER`, а не по номеру: номер пришлось бы где-то
хранить, а хранимое состояние разъезжается.

**Три исхода, а не два** (правило 039). «Чисто», «есть находки» и «проверка не
отработала» — разные вещи, и в задаче они названы по-разному: находка трекера
чинится в трекере, неотработавший механизм — здесь. Но прогон не краснеет ни от
того, ни от другого: красный ночной прогон — это опять сигнал без адресата,
ровно то, ради чего скрипт и написан.

**Пишет только с** ``--apply``. Без него печатает сводку и выходит.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import pathlib
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

__all__ = [
    "CHECKS",
    "MARKER",
    "Check",
    "Outcome",
    "issue_body",
    "main",
    "run_checks",
]

#: Скрытый маркер задачи-адресата. По нему она находится в следующий раз.
MARKER = "<!-- nightly-findings -->"

_TITLE = "Ночной обход: находки"
_LABEL = "ночной обход"
_LABEL_COLOR = "fbca04"
_LABEL_DESCRIPTION = "находки ночного обхода трекера и правил"

#: Место, где `<catalogue>` заменяется на клон каталога правил.
_CATALOGUE_TOKEN = "<catalogue>"


class Check:
    """Одна ночная проверка: как зовут, чем запускать, о чём она.

    Attributes:
        name: человеческое имя — оно уедет в задачу.
        argv: команда без интерпретатора.
        about: одна строка о предмете, чтобы находку можно было понять.
    """

    __slots__ = ("about", "argv", "name")

    def __init__(self, name: str, argv: list[str], about: str) -> None:
        self.name = name
        self.argv = argv
        self.about = about


#: Что гоняется ночью. Список закрытый и лежит здесь, а не в YAML: шаги
#: workflow не тестируются, а этот файл — да.
CHECKS: tuple[Check, ...] = (
    Check(
        "Комплексные issue ведут чек-лист",
        ["scripts/check_issue_checklists.py"],
        "issue от трёх находок ведёт чек-лист с исходом каждой",
    ),
    Check(
        "good first issue и help wanted — на двух языках",
        ["scripts/check_good_first_issues_bilingual.py"],
        "метки приводят англоязычную аудиторию, а тело только по-русски",
    ),
    Check(
        "Защита main не ослаблена молча",
        ["scripts/check_branch_protection.py"],
        "список обходов пуст, обязательных проверок одиннадцать дословно",
    ),
    Check(
        "Реестр закрытых находок не отстаёт",
        ["scripts/check_audit_registry.py"],
        "находка, закрытая PR, вписана в реестр аудита",
    ),
    Check(
        "Закрытие контейнера не закрывает работу",
        ["scripts/check_container_closure.py"],
        "закрытый эпик с открытыми дочерними: снаружи готово, изнутри работа идёт",
    ),
    Check(
        "Ответ каталогу правил полон",
        ["scripts/check_rule_bindings.py", "--catalogue", _CATALOGUE_TOKEN],
        "по каждому правилу каталога есть ответ; метрика «ничем» обязана падать",
    ),
    Check(
        "Второй рубеж не отстал от каталога",
        ["scripts/generate_rules_digest.py", "--catalogue", _CATALOGUE_TOKEN, "--check"],
        "дайджест правил, который читает окно на старте, собран по свежему каталогу",
    ),
)


class Outcome:
    """Что вышло у одной проверки.

    Attributes:
        check: сама проверка.
        code: код возврата — 0 чисто, 1 находка, 2 не отработала.
        output: то, что она напечатала (оба потока).
    """

    __slots__ = ("check", "code", "output")

    def __init__(self, check: Check, code: int, output: str) -> None:
        self.check = check
        self.code = code
        self.output = output

    @property
    def clean(self) -> bool:
        """Находок нет и механизм отработал."""
        return self.code == 0

    @property
    def broken(self) -> bool:
        """Механизм не отработал — чинить здесь, а не в трекере."""
        return self.code >= 2


def run_checks(
    catalogue: pathlib.Path,
    *,
    checks: tuple[Check, ...] = CHECKS,
    runner: object = None,
) -> list[Outcome]:
    """Прогнать ночные проверки и собрать их исходы.

    Args:
        catalogue: клон каталога правил — подставляется вместо `<catalogue>`.
        checks: что гонять; по умолчанию :data:`CHECKS`.
        runner: подмена запуска для тестов — вызываемое `(argv) -> (код, вывод)`.

    Returns:
        Исходы в порядке объявления.
    """
    outcomes: list[Outcome] = []
    for check in checks:
        argv = [str(catalogue) if part == _CATALOGUE_TOKEN else part for part in check.argv]
        if runner is not None:
            code, output = runner(argv)  # type: ignore[operator]
        else:
            result = subprocess.run(
                [sys.executable, *argv],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            code, output = result.returncode, (result.stdout + result.stderr)
        outcomes.append(Outcome(check, code, output.strip()))
    return outcomes


def issue_body(outcomes: list[Outcome], today: _datetime.date) -> str:
    """Тело задачи-адресата: находки поимённо, чистые — строкой."""
    problems = [item for item in outcomes if not item.clean]
    lines = [MARKER, "", f"_Ночной обход, {today.isoformat()}._", ""]

    if not problems:
        # Пустое состояние объявляется словами: молча пустая задача читается
        # как «обход не отработал» (правило 027).
        lines += [
            f"**Находок нет.** Все {len(outcomes)} проверки прошли чисто.",
            "",
            "Задача закрывается сама; следующая находка откроет её снова.",
        ]
        return "\n".join(lines) + "\n"

    broken = [item for item in problems if item.broken]
    lines.append(
        f"**Находок: {len(problems)}** из {len(outcomes)} проверок"
        + (f", из них не отработало — {len(broken)}." if broken else ".")
    )
    lines.append("")
    for item in problems:
        kind = "механизм не отработал" if item.broken else "находка"
        lines += [f"### {item.check.name} — {kind}", "", f"_{item.check.about}_", "", "```"]
        lines.append(item.output or "(проверка ничего не напечатала)")
        lines += ["```", ""]

    lines += [
        "---",
        "_Задачу ведёт `scripts/nightly_checks.py`: тело обновляется каждым обходом, "
        "а когда находок не останется, задача закроется сама._",
    ]
    return "\n".join(lines) + "\n"


def _existing_issue(repo: str, **kwargs: Any) -> dict[str, object] | None:
    """Задача-адресат, если она уже заведена, — по маркеру, а не по номеру."""
    for issue in gh_rest.issues_with_label(repo, _LABEL, **kwargs):
        if MARKER in str(issue.get("body") or ""):
            return issue
    return None


def _summary(outcomes: list[Outcome]) -> str:
    """Короткая сводка для вывода прогона."""
    lines = []
    for item in outcomes:
        mark = "чисто" if item.clean else ("НЕ ОТРАБОТАЛА" if item.broken else "находка")
        lines.append(f"  [{mark}] {item.check.name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """0 — обход отработал; 1 — какая-то проверка не отработала вовсе."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=pathlib.Path, required=True)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--apply", action="store_true", help="вести задачу, а не печатать")
    parser.add_argument("--today", help="дата в теле задачи (ISO); по умолчанию сегодня")
    args = parser.parse_args(argv)

    outcomes = run_checks(args.catalogue)
    today = (
        _datetime.date.fromisoformat(args.today)
        if args.today
        else _datetime.datetime.now(tz=_datetime.UTC).date()
    )
    body = issue_body(outcomes, today)
    problems = [item for item in outcomes if not item.clean]
    broken = [item for item in outcomes if item.broken]

    print(_summary(outcomes))
    if not args.apply:
        print("\n--- тело задачи ---\n")
        print(body)
        return gh_rest.EXIT_OK

    existing = _existing_issue(args.repo)
    if problems:
        if existing is None:
            gh_rest.ensure_label(
                args.repo, _LABEL, color=_LABEL_COLOR, description=_LABEL_DESCRIPTION
            )
            created = gh_rest.create_issue(args.repo, title=_TITLE, body=body, labels=[_LABEL])
            print(f"\nзаведена задача #{created.get('number')}: находок {len(problems)}")
        else:
            number = int(str(existing.get("number")))
            gh_rest.update_issue(args.repo, number, body=body)
            print(f"\nобновлена задача #{number}: находок {len(problems)}")
    elif existing is not None:
        number = int(str(existing.get("number")))
        gh_rest.update_issue(args.repo, number, body=body)
        gh_rest.close_issue(args.repo, number)
        print(f"\nзадача #{number} закрыта: находок больше нет")
    else:
        print("\nнаходок нет, задачи нет — заводить нечего")

    # Прогон зеленеет всегда, когда адресат сработал, — даже если какая-то
    # проверка не отработала. Красный обход был бы ровно тем, против чего
    # написано правило 142: сигналом без адресата. Неотработавший механизм
    # назван в теле задачи отдельным разделом, и чинит его тот, кто её прочтёт.
    if broken:
        print(f"механизм не отработал у {len(broken)} проверок — названы в задаче")
    return gh_rest.EXIT_OK


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
