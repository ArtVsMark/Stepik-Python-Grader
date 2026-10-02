#!/usr/bin/env python3
"""scripts/check_container_closure.py — закрытие контейнера не закрывает работу.

Правило 121 каталога: эпик, веха, спринт закрываются по своим критериям, а
единицы работы — по своим. Завершение доказывается **счётчиком незакрытых
единиц**, а не тем, что контейнер выглядит пустым.

Предмет здесь — трекер: закрытый эпик, у которого остались открытые дочерние
задачи. Такой эпик врёт дважды. Снаружи он говорит «направление закончено», и
никто больше не смотрит внутрь; изнутри его дочерние задачи остаются в работе,
но теряют контекст — тот, кто их найдёт, не поймёт, зачем они, потому что
объясняющий их эпик закрыт.

Обратный случай — открытый эпик, у которого все дочерние закрыты, — проверяется
тоже, но говорится о нём мягче: работа может быть сделана, а приёмка нет, и это
законное состояние. Отличать одно от другого машине нечем, поэтому здесь она
называет факт, а решает человек.

**Три исхода** (правило 039): чисто, находка, «прочитать не удалось». Последний
отдельно: без доступа к трекеру ответ «нарушений нет» был бы ложью, а не
результатом.

Запуск::

    python scripts/check_container_closure.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import contextlib

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["Mismatch", "closure_mismatches", "main"]


class Mismatch:
    """Расхождение между состоянием контейнера и его дочерних задач.

    Attributes:
        parent: номер эпика.
        title: его заголовок — иначе номер ничего не говорит.
        parent_closed: закрыт ли сам эпик.
        open_children: номера открытых дочерних задач.
        closed_children: сколько дочерних закрыто.
    """

    __slots__ = ("closed_children", "open_children", "parent", "parent_closed", "title")

    def __init__(
        self,
        parent: int,
        title: str,
        *,
        parent_closed: bool,
        open_children: list[int],
        closed_children: int,
    ) -> None:
        self.parent = parent
        self.title = title
        self.parent_closed = parent_closed
        self.open_children = open_children
        self.closed_children = closed_children

    @property
    def severe(self) -> bool:
        """Закрытый контейнер с незакрытой работой — та самая ложь."""
        return self.parent_closed and bool(self.open_children)

    def line(self) -> str:
        """Строка отчёта: что именно не сходится."""
        if self.severe:
            numbers = ", ".join(f"#{number}" for number in self.open_children)
            return (
                f"#{self.parent} «{self.title}» закрыт, но открыты дочерние: {numbers}. "
                "Снаружи направление выглядит законченным, изнутри работа идёт — "
                "и потерявшие контекст задачи ищут его в закрытом эпике"
            )
        return (
            f"#{self.parent} «{self.title}» открыт, а все {self.closed_children} дочерних "
            "закрыты — возможно, осталась только приёмка"
        )


def closure_mismatches(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    parents: list[dict[str, object]] | None = None,
    children: dict[int, list[dict[str, object]]] | None = None,
    **kwargs: Any,
) -> list[Mismatch]:
    """Эпики, чьё состояние разошлось с состоянием их дочерних задач.

    Args:
        repo: владелец/репозиторий.
        parents: подмена списка эпиков для тестов.
        children: подмена дочерних задач: номер эпика → список задач.
        **kwargs: прокидываются в транспорт (токен, opener).

    Returns:
        Расхождения; тяжёлые (закрытый контейнер с открытой работой) первыми.
    """
    if parents is None:
        parents = [
            item
            for item in gh_rest.issues_with_label(repo, "epic", state="all", **kwargs)
            if "pull_request" not in item
        ]

    found: list[Mismatch] = []
    for parent in parents:
        number = int(str(parent.get("number")))
        kids = (
            children.get(number, [])
            if children is not None
            else gh_rest.sub_issues(repo, number, **kwargs)
        )
        if not kids:
            continue
        open_children = [
            int(str(kid.get("number"))) for kid in kids if str(kid.get("state")) == "open"
        ]
        closed = len(kids) - len(open_children)
        parent_closed = str(parent.get("state")) == "closed"
        if parent_closed and open_children:
            found.append(
                Mismatch(
                    number,
                    str(parent.get("title", "")),
                    parent_closed=True,
                    open_children=open_children,
                    closed_children=closed,
                )
            )
        elif not parent_closed and not open_children:
            found.append(
                Mismatch(
                    number,
                    str(parent.get("title", "")),
                    parent_closed=False,
                    open_children=[],
                    closed_children=closed,
                )
            )
    found.sort(key=lambda item: (not item.severe, item.parent))
    return found


def main(argv: list[str] | None = None) -> int:
    """0 — состояния сходятся; 1 — есть расхождение; 2 — трекер не прочитан."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    args = parser.parse_args(argv)

    try:
        found = closure_mismatches(args.repo)
    except gh_rest.RateLimited as exc:
        print(f"квота исчерпана, состояние трекера не прочитано: {exc}", file=sys.stderr)
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        # «Прочитать не удалось» — не «нарушений нет»: третий исход отдельно,
        # иначе отсутствие доступа выглядело бы чистым трекером.
        print(f"трекер не прочитан: {exc}", file=sys.stderr)
        return 2

    if not found:
        print("контейнеры и их дочерние задачи сходятся по состоянию")
        return gh_rest.EXIT_OK

    severe = [item for item in found if item.severe]
    print("состояние контейнера разошлось с работой:", file=sys.stderr)
    for item in found:
        print(f"  • {item.line()}", file=sys.stderr)
    if severe:
        print(
            "\nЗакрытый эпик с открытой работой: либо дочерние закрываются, "
            "либо эпик открывается — счётчик незакрытых единиц и есть доказательство.",
            file=sys.stderr,
        )
    return gh_rest.EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
