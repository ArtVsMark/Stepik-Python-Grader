#!/usr/bin/env python3
"""scripts/move_merge_queue.py — двигать очередь мимо конфликтов (issue #1313).

Голову очереди обновляет ``merge-queue.yml``, и до сих пор он делал это одним
шагом: взять первого готового и позвать ``update-branch``. На конфликтной ветке
GitHub отвечает ошибкой, шаг падал, а вместе с ним весь прогон — и очередь
стояла целиком. Замер: три падения подряд, **14 часов простоя**, четыре
здоровых PR рядом, из которых два с включённым авто-мержем ждали ровно того,
что им никто не подтянет базу.

**Конфликт — штатная ситуация, а не авария.** Он возникает всякий раз, когда
два PR трогают один файл. Механизм, который на этом останавливается, не
защищает конвейер, а становится его единственной точкой отказа.

Отсюда правила, закодированные здесь:

1. **Конфликтный PR пропускается, а не роняет прогон.** Очередь идёт дальше и
   обновляет первого пригодного — остальные не должны ждать чужого конфликта.
2. **Пропуск не молчит.** PR помечается меткой :data:`CONFLICT_LABEL`, иначе он
   вечно обходился бы очередью, и никто бы не узнал почему. Метка снимается
   сама, когда конфликт исчез, — иначе она пережила бы причину и начала врать.
3. **Ошибка обновления по любой другой причине** (403, 422, сеть) тоже не
   роняет прогон: она называется в отчёте, PR помечается, очередь идёт дальше.
4. **``unknown`` — это «GitHub ещё считает», а не «нельзя мержить».**
   ``mergeable_state`` вычисляется асинхронно, поэтому состояние перечитывается
   через паузу; здоровый PR не должен попасть в пропущенные из-за гонки.
5. **Прогон зелёный, если механизм отработал.** Красный здесь означает
   «мувер сломан», а не «у кого-то конфликт» — иначе сигнал теряет смысл.

Обновляется по-прежнему **один** PR за прогон (правило ``CLAUDE.md``: из
``main`` обновляется только голова очереди) — просто теперь это первый
**пригодный**, а не первый по списку.

Запуск::

    python scripts/move_merge_queue.py             # подвинуть очередь
    python scripts/move_merge_queue.py --dry-run   # показать, ничего не меняя
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_rest

__all__ = [
    "CONFLICT_LABEL",
    "CONFLICT_STATES",
    "Outcome",
    "main",
    "mark_conflicted",
    "move_queue",
    "resolve_mergeable_state",
]

# Метка, которой помечается PR, обойдённый из-за конфликта. Метка, а не
# комментарий: мувер срабатывает после каждого прогона `main`, и комментарий
# добавлялся бы снова и снова, а метка идемпотентна по природе.
CONFLICT_LABEL = "needs-rebase"
_LABEL_COLOR = "d93f0b"
_LABEL_DESCRIPTION = "Конфликт с main — очередь мержа обошла PR, нужно слияние вручную"

# `dirty` — конфликт с базой. `blocked`/`behind` конфликтом не являются:
# первый ждёт проверок или ревью, второй как раз и лечится обновлением.
CONFLICT_STATES = frozenset({"dirty"})

# Состояния, при которых GitHub ещё не досчитал mergeable_state.
_PENDING_STATES = frozenset({"unknown", ""})


class Outcome:
    """Что мувер сделал с очередью — для отчёта и для тестов."""

    __slots__ = ("lines", "updated")

    def __init__(self) -> None:
        self.updated: int | None = None
        self.lines: list[str] = []

    def say(self, line: str) -> None:
        """Добавить строку отчёта."""
        self.lines.append(line)

    def __repr__(self) -> str:  # pragma: no cover — диагностика в отладке
        return f"Outcome(updated={self.updated}, lines={self.lines})"


def resolve_mergeable_state(
    repo: str,
    number: int,
    *,
    attempts: int = 3,
    pause: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> str:
    """Дождаться, пока GitHub досчитает ``mergeable_state`` этого PR.

    Значение вычисляется асинхронно: сразу после запроса оно бывает
    ``unknown``, причём у совершенно здорового PR. Судить по такому ответу
    нельзя — иначе очередь обошла бы годный PR и повесила на него метку
    конфликта. Поэтому состояние перечитывается через паузу; исчерпали попытки
    — возвращаем как есть, и вызывающая сторона трактует это как «не трогаем».
    """
    state = ""
    for attempt in range(attempts):
        data = gh_rest.pull(repo, number, **kwargs)
        state = str(data.get("mergeable_state") or "")
        if state not in _PENDING_STATES:
            return state
        if attempt + 1 < attempts:
            sleep(pause)
    return state


def mark_conflicted(repo: str, number: int, *, dry_run: bool = False, **kwargs: Any) -> None:
    """Пометить PR как требующий ручного слияния (метка появится, если её нет)."""
    if dry_run:
        return
    gh_rest.ensure_label(
        repo,
        CONFLICT_LABEL,
        color=_LABEL_COLOR,
        description=_LABEL_DESCRIPTION,
        **kwargs,
    )
    gh_rest.add_labels(repo, number, [CONFLICT_LABEL], **kwargs)


def clear_conflict_mark(repo: str, number: int, *, dry_run: bool = False, **kwargs: Any) -> None:
    """Снять метку конфликта — молча, если её и не было.

    Метка, пережившая свою причину, хуже её отсутствия: PR выглядит сломанным,
    когда с ним уже всё в порядке.
    """
    if dry_run:
        return
    gh_rest.remove_label(repo, number, CONFLICT_LABEL, **kwargs)


def move_queue(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Outcome:
    """Обновить первого пригодного в очереди; конфликтных пометить и обойти."""
    outcome = Outcome()
    report = gh_rest.merge_queue(repo, **kwargs)
    if not report.ready:
        outcome.say("готовых PR нет — двигать нечего")
        return outcome

    for entry in report.ready:
        number = entry.number
        if entry.fork:
            outcome.say(
                f"PR #{number} из форка — ветку обновляет его владелец "
                "или мейнтейнер кнопкой Update branch; иду дальше"
            )
            continue

        state = resolve_mergeable_state(repo, number, sleep=sleep, **kwargs)
        if state in CONFLICT_STATES:
            mark_conflicted(repo, number, dry_run=dry_run, **kwargs)
            outcome.say(
                f"PR #{number} конфликтует с базой (mergeable_state={state}) — "
                f"помечен «{CONFLICT_LABEL}», нужно слияние вручную; иду дальше"
            )
            continue
        if state in _PENDING_STATES:
            outcome.say(
                f"PR #{number}: GitHub не досчитал mergeable_state — "
                "не трогаю, вернусь следующим прогоном"
            )
            continue

        if dry_run:
            outcome.updated = number
            outcome.say(f"PR #{number}: обновил бы ветку из базы")
            return outcome

        try:
            gh_rest.update_branch(repo, number, **kwargs)
        except gh_rest.GitHubError as exc:
            mark_conflicted(repo, number, dry_run=dry_run, **kwargs)
            outcome.say(
                f"PR #{number}: обновление ветки не прошло ({exc}) — "
                f"помечен «{CONFLICT_LABEL}»; иду дальше"
            )
            continue

        clear_conflict_mark(repo, number, dry_run=dry_run, **kwargs)
        outcome.updated = number
        waiting = len(report.ready) - report.position(number)
        outcome.say(f"голова очереди — PR #{number}, за ней ждут: {waiting}")
        return outcome

    outcome.say("пригодных для обновления PR не нашлось — очередь ждёт ручного слияния")
    return outcome


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — механизм отработал (даже если все PR пропущены); 1 — сеть или квота."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/move_merge_queue.py",
        description="Обновить первого пригодного в очереди мержа, конфликтных пометить.",
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не меняя")
    args = parser.parse_args(argv)

    try:
        outcome = move_queue(args.repo, dry_run=args.dry_run)
    except gh_rest.RateLimited as exc:
        # Исчерпанная квота — «ждать», а не «упало»: повторять бессмысленно,
        # счётчик растёт и после нуля.
        print(f"квота GitHub исчерпана: {exc}")
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        # Сюда попадает только поломка самого механизма — очередь не читается.
        # Про конфликты и отказы обновления решает move_queue, и они зелёные.
        print(f"очередь не прочитана: {exc}")
        return gh_rest.EXIT_FAIL

    for line in outcome.lines:
        print(line)
    return gh_rest.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
