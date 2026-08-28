#!/usr/bin/env python3
"""scripts/check_orphan_branches.py — работа без прикреплённого изменения.

Правило 147 каталога: у переключателя, отменяющего операцию, должен быть
адресат отмены. Наш переключатель — префикс ветки: PR открывает `agent-pr.yml`,
и только для `agent/**`. Ветка с другим именем не получает PR **вовсе**, и это
не отказ: прогона нет, значит нет ни красного, ни лога, ни кода возврата.
Успешный `push` выглядит одинаково в обоих случаях, потому что он и есть
одинаковый — толкнувший видит успех своей команды и уходит, считая работу
сданной.

Отсюда форма починки. Лекарство не может жить внутри переключателя: он не
запускался. Заметить обязан кто-то третий — тот, кто смотрит на работу, у
которой нет прикреплённого изменения. Этим третьим и работает ночной обход
(``scripts/nightly_checks.py``), а здесь — сама проверка.

Что считается находкой: ветка на ``origin``, у которой нет открытого PR и
последний коммит старше :data:`GRACE_HOURS` часов. Отсрочка обязательна —
`agent-pr.yml` ходит по расписанию, и только что запушенная ветка законно ещё
без PR.

Запуск::

    python scripts/check_orphan_branches.py
    python scripts/check_orphan_branches.py --hours 6
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _datetime
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["GRACE_HOURS", "IGNORED", "main", "orphan_branches", "orphans"]

#: Сколько ветка вправе прожить без PR. `agent-pr.yml` ходит раз в пятнадцать
#: минут, но пуш мог совпасть с прогоном, а работа — идти в несколько заходов.
GRACE_HOURS = 6

#: Ветки, у которых PR не бывает по устройству: база и публикация бейджей.
IGNORED = frozenset({"main", "badges", "gh-pages"})


def orphans(
    branches: list[dict[str, Any]],
    attached: set[str],
    committed: dict[str, _datetime.datetime | None],
    *,
    hours: int = GRACE_HOURS,
    now: _datetime.datetime,
) -> list[str]:
    """Решение без сети: какие ветки остались без прикреплённого изменения.

    Args:
        branches: ветки в форме ответа GitHub (``name`` + ``commit.sha``).
        attached: имена веток, у которых есть открытый PR.
        committed: момент последнего коммита по sha; ``None`` — даты нет.
        hours: отсрочка в часах от последнего коммита.
        now: текущий момент (UTC).

    Returns:
        Строки «ветка — почему находка», по одной на ветку.
    """
    found: list[str] = []
    for item in branches:
        name = str(item.get("name") or "")
        if not name or name in IGNORED or name in attached:
            continue
        sha = str((item.get("commit") or {}).get("sha") or "")
        stamp = committed.get(sha)
        if stamp is None:
            found.append(f"{name} — даты последнего коммита нет, PR не открыт")
            continue
        age = (now - stamp).total_seconds() / 3600
        if age >= hours:
            found.append(f"{name} — PR не открыт, последний коммит {int(age)} ч назад")
    return found


def orphan_branches(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    hours: int = GRACE_HOURS,
    now: _datetime.datetime | None = None,
    **kwargs: Any,
) -> list[str]:
    """То же, но со сбором состояния из GitHub.

    Дата коммита спрашивается только у веток без PR: их обычно единицы, а
    лишний запрос на каждую ветку репозитория стоил бы квоты ни за что.
    """
    branches = gh_rest.request("GET", f"repos/{repo}/branches?per_page=100", **kwargs).data
    if not isinstance(branches, list):
        return []
    attached = {pull.branch for pull in gh_rest.list_pulls(repo, **kwargs)}
    committed: dict[str, _datetime.datetime | None] = {}
    for item in branches:
        name = str(item.get("name") or "")
        if not name or name in IGNORED or name in attached:
            continue
        sha = str((item.get("commit") or {}).get("sha") or "")
        if not sha:
            continue
        committed[sha] = _committed_at(
            gh_rest.request("GET", f"repos/{repo}/commits/{sha}", **kwargs).data
        )
    return orphans(
        branches,
        attached,
        committed,
        hours=hours,
        now=now or _datetime.datetime.now(_datetime.UTC),
    )


def _committed_at(commit: object) -> _datetime.datetime | None:
    """Момент коммита из ответа GitHub, если он разбирается."""
    if not isinstance(commit, dict):
        return None
    raw = ((commit.get("commit") or {}).get("committer") or {}).get("date")
    if not isinstance(raw, str):
        return None
    with contextlib.suppress(ValueError):
        return _datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return None


def main(argv: list[str] | None = None) -> int:
    """0 — у всякой ветки есть PR; 1 — находка; 2 — GitHub не ответил."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--hours", type=int, default=GRACE_HOURS, help="отсрочка в часах")
    args = parser.parse_args(argv)

    try:
        found = orphan_branches(args.repo, hours=args.hours)
    except gh_rest.RateLimited as exc:
        print(f"квота исчерпана, ветки не проверены: {exc}", file=sys.stderr)
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        print(f"проверить ветки не удалось — GitHub отказал: {exc}", file=sys.stderr)
        return 2

    if found:
        print("работа без прикреплённого изменения:", file=sys.stderr)
        for place in found:
            print(f"  • {place}", file=sys.stderr)
        print(
            "\nPR открывает agent-pr.yml и только для веток `agent/**`: другое имя "
            "означает не отказ, а отсутствие прогона — заметить это может только "
            "тот, кто смотрит на ветки. Переименуйте ветку либо откройте PR руками.",
            file=sys.stderr,
        )
        return 1

    print(f"веток без PR старше {args.hours} ч нет")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
