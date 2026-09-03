#!/usr/bin/env python3
"""scripts/check_issue_state_after_merge.py — судьба задачи ПОСЛЕ слияния (issue #1419).

Правило 173 каталога состоит из двух половин, и у нас держалась только первая.
``check_pr_ready.py`` отвергает изменение без связи с задачей — это проверка
**намерения**, и стоит она до слияния. Врёт же связь после: площадка закрыла не
ту задачу, не закрыла ничего, либо частичная работа уехала, а остаток назвать
забыли. Проверка обязана стоять по ту сторону слияния (правило 139: механизм
подтверждается прогоном, а не чтением).

Задача — это **состояние**, а изменение — событие, и перевод между ними
односторонний: площадка умеет закрыть задачу по слову в теле и не умеет ничего
сказать про «сделана половина». Значит частичное выполнение существует ровно
настолько, насколько о нём сказано вслух.

Отсюда асимметрия, задающая форму находок. Незакрытая сделанная задача дешева
на вид и дорога по цене: держит очередь, попадает в отчёты, и следующее окно
берёт её заново. Ложно закрытая — хуже: остаток исчезает вместе с ней.

**Сторож не закрывает задачи.** Он говорит «сделано, а задача открыта», и не
более того: закрывает задачу человек или его изменение. Автозакрытие здесь
превратило бы находку в потерю остатка — ровно то, что правило запрещает.

Три исхода (правило 039): 0 — сходится, 1 — находка, 2 — проверка не отработала
(нет доступа к API, исчерпана квота). «Не знать» и «знать плохое» — разное.

Запуск::

    python scripts/check_issue_state_after_merge.py
    python scripts/check_issue_state_after_merge.py --limit 50
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import re
import sys
from collections.abc import Iterable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "DEFAULT_LIMIT",
    "MACHINE_LED_LABELS",
    "Mismatch",
    "closing_numbers",
    "machine_led",
    "main",
    "mismatches",
    "partial_numbers",
    "remainder_is_named",
]

#: Сколько последних слитых изменений смотреть. Не «все»: предмет — свежая
#: работа, а старые расхождения либо уже разобраны, либо перестали быть правдой.
DEFAULT_LIMIT = 30

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)

#: Второй ответ правила 173: «Часть #N — что именно сделано».
_PARTIAL_RE = re.compile(r"^\s*Часть\s+#(\d+)\s*[—–-]\s*\S", re.MULTILINE)

#: Незакрытая галочка чек-листа: ``- [ ] ...``. Остаток называется галочками, а
#: не прозой (правило 028) — по прозе состояние приходится вычислять чтением.
_UNCHECKED_RE = re.compile(r"^\s*[-*]\s*\[ \]\s*\S", re.MULTILINE)

#: Задачи, чьё СОСТОЯНИЕ ведёт механизм, а не человек. Их эта проверка не
#: трогает: ночной обход закрывает свою задачу, когда чисто, и переоткрывает,
#: когда находки вернулись, — то есть открытая задача там означает «есть что
#: разобрать», а не «обещание закрыть не выполнено».
#:
#: Проверка нашла это на себе, на первом же прогоне по свежей `main`:
#: PR #1406 закрыл задачу обхода, обход её потом переоткрыл, и правило 173
#: прочиталось как нарушенное. Гейт, краснеющий на верном ответе, снимают
#: первой же правкой, поэтому исключение названо, а не подразумевается.
MACHINE_LED_LABELS = frozenset({"ночной обход"})


class Mismatch:
    """Одно расхождение между сделанным и состоянием задачи."""

    __slots__ = ("issue", "pull", "what")

    def __init__(self, pull: int, issue: int, what: str) -> None:
        self.pull = pull
        self.issue = issue
        self.what = what

    def __str__(self) -> str:
        return f"#{self.issue} ← PR #{self.pull}: {self.what}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mismatch):
            return NotImplemented
        return (self.pull, self.issue, self.what) == (other.pull, other.issue, other.what)

    def __hash__(self) -> int:
        return hash((self.pull, self.issue, self.what))


def closing_numbers(body: str) -> list[int]:
    """Задачи, которые изменение объявило закрытыми."""
    return sorted({int(number) for number in _CLOSES_RE.findall(body)})


def partial_numbers(body: str) -> list[int]:
    """Задачи, по которым изменение объявило себя частью."""
    return sorted({int(number) for number in _PARTIAL_RE.findall(body)})


def remainder_is_named(issue_body: str) -> bool:
    """Назван ли остаток — незакрытой галочкой, а не прозой.

    Проза законна рядом, но состоянием не является: чтобы понять, что осталось,
    её приходится читать целиком и решать за автора.
    """
    return bool(_UNCHECKED_RE.search(issue_body))


def machine_led(labels: Iterable[str]) -> bool:
    """Ведёт ли состояние этой задачи механизм, а не человек."""
    return bool(MACHINE_LED_LABELS & {str(label) for label in labels})


def mismatches(
    pulls: list[dict[str, Any]],
    issue_state: dict[int, tuple[str, str]],
    *,
    machine_issues: Iterable[int] = (),
) -> list[Mismatch]:
    """Расхождения между объявленным в изменениях и состоянием задач.

    Args:
        pulls: Слитые изменения (нужны ``number`` и ``body``).
        issue_state: Номер задачи → (состояние, тело). Отсутствие номера
            означает «спросить не удалось» и находкой не считается.

    Returns:
        Расхождения в порядке изменений.
    """
    skip = set(machine_issues)
    found: list[Mismatch] = []
    for pull in pulls:
        number = int(pull.get("number") or 0)
        body = str(pull.get("body") or "")
        for issue in closing_numbers(body):
            state = issue_state.get(issue)
            if state is None or issue in skip:
                continue
            if state[0] != "closed":
                found.append(
                    Mismatch(
                        number,
                        issue,
                        "изменение объявило её закрытой и слито, а задача открыта — "
                        "площадка закрытие не выполнила; закрыть обязан человек, "
                        "а не сторож",
                    )
                )
        for issue in partial_numbers(body):
            state = issue_state.get(issue)
            if state is None or issue in skip:
                continue
            if state[0] == "closed":
                # Закрытие ПОСЛЕ того, как остаток доделан, — нормальный конец
                # жизни задачи, а не находка: частичное изменение к тому
                # моменту уже перестало быть частичным. Предмет правила —
                # остаток, который ИСЧЕЗ вместе с задачей, то есть незакрытая
                # галочка в закрытой задаче.
                if remainder_is_named(state[1]):
                    found.append(
                        Mismatch(
                            number,
                            issue,
                            "изменение объявило себя ЧАСТЬЮ, задача закрыта, а "
                            "остаток в ней не закрыт — он исчез вместе с ней, и "
                            "найти его можно только по памяти того, кто сливал",
                        )
                    )
            elif not remainder_is_named(state[1]):
                found.append(
                    Mismatch(
                        number,
                        issue,
                        "изменение объявило себя частью, а остаток не назван "
                        "галочками — по прозе состояние приходится вычислять чтением",
                    )
                )
    return found


def main(argv: list[str] | None = None) -> int:
    """0 — сходится, 1 — находка, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    try:
        pulls = gh_rest.merged_pulls(args.repo, limit=args.limit)
        wanted: set[int] = set()
        for pull in pulls:
            body = str(pull.get("body") or "")
            wanted |= set(closing_numbers(body)) | set(partial_numbers(body))
        state: dict[int, tuple[str, str]] = {}
        machine: set[int] = set()
        for number in sorted(wanted):
            issue = gh_rest.issue(args.repo, number)
            state[number] = (str(issue.get("state") or ""), str(issue.get("body") or ""))
            labels = [
                str(label.get("name", ""))
                for label in (issue.get("labels") or [])
                if isinstance(label, dict)
            ]
            if machine_led(labels):
                machine.add(number)
    except gh_rest.RateLimited as error:
        print(f"проверка не отработала: {error}")
        return 2
    except (gh_rest.GitHubError, gh_rest.MissingToken, OSError) as error:
        print(f"проверка не отработала: {error}")
        return 2

    found = mismatches(pulls, state, machine_issues=machine)
    # Правило 165, вторая половина: охват называется числом. Молчание означает и
    # «расхождений нет», и «ничего не смотрели».
    print(
        f"Судьба задач после слияния: изменений просмотрено — {len(pulls)}, "
        f"задач — {len(state)}, из них ведёт механизм — {len(machine)}."
    )
    if found:
        print("FAIL: сделанное разошлось с состоянием задачи:")
        for mismatch in found:
            print(f"  - {mismatch}")
        return 1
    print("Закрытые закрыты, частичные открыты и несут остаток.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
