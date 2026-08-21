#!/usr/bin/env python3
"""scripts/merge_when_green.py — метка как выраженное согласие на мерж (issue #1303).

Конвейер замкнут почти полностью: PR для веток ``agent/**`` открывает
``open_agent_prs.py`` и сразу включает им авто-мерж, ``move_merge_queue.py``
держит голову очереди актуальной, GitHub мержит по зелёному. Не закрыт один
случай — **PR, открытый вручную**: авто-мерж ему никто не включает, и он стоит
зелёным сколько угодно долго. Живой пример из issue: PR с шестнадцатью
успешными проверками, `clean`, ветка не отстала — и всё равно ждал человека.

**Почему не включать авто-мерж всем подряд.** Тогда в ``main`` уедет и тот PR,
который автор ещё хотел посмотреть. Защита ветки проверяет качество, но не
намерение; согласие на мерж должно быть выражено явно, иначе оно перестаёт быть
согласием.

Отсюда метка :data:`LABEL` — «как позеленеет, мержи без меня». Правила:

1. **Включаем только помеченным.** Черновик пропускается — согласия ещё нет;
   PR из форка тоже: ветку ведёт внешний автор, и авто-мерж за него включать
   не наше дело.
2. **Идемпотентность.** Авто-мерж уже включён — пропускаем молча: скрипт зовётся
   и по событию, и по расписанию, то есть повторный проход гарантирован.
3. **Решение обратимо.** Снятая метка выключает авто-мерж (``--disable``,
   событие ``unlabeled``) — иначе согласие нельзя было бы отозвать, и PR уехал
   бы вопреки автору.
4. **Прогон зелёный, если механизм отработал.** Отказ на одном PR не мешает
   остальным: он назван в отчёте, обход продолжается.

Метку заводит человек — она описана в ``CONTRIBUTING.md``; скрипт её только
читает. Так и задумано: метка меняет поведение конвейера, поэтому её появление
в трекере — осознанный шаг, а не побочный эффект первого запуска.

Запуск::

    python scripts/merge_when_green.py                 # включить всем помеченным
    python scripts/merge_when_green.py --disable 1297  # снять авто-мерж с PR
    python scripts/merge_when_green.py --dry-run       # показать, ничего не меняя
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_rest

__all__ = [
    "CONFLICT_LABEL",
    "HOLD_LABEL",
    "LABEL",
    "Outcome",
    "apply_default_consent",
    "disable_for",
    "enable_for_labelled",
    "labels_of",
    "main",
    "pulls_awaiting_auto_merge",
]

#: Метка, означающая «как позеленеет, мержи без меня».
LABEL = "merge-when-green"

#: Стоп-метка: «этот PR автоматике не отдавать» (issue #1325).
HOLD_LABEL = "hold"
_HOLD_COLOR = "b60205"
_HOLD_DESCRIPTION = "Не отдавать автоматике: не ставить merge-when-green и не мержить"

#: Метка конфликта из очереди мержа: такому PR согласие не выдаётся — сперва
#: слияние вручную (issue #1313).
CONFLICT_LABEL = "needs-rebase"

_CONSENT_COLOR = "0e8a16"
_CONSENT_DESCRIPTION = "Согласие смержить без автора: авто-мерж включится, как позеленеет"


class Outcome:
    """Что механизм сделал — для отчёта и для тестов."""

    __slots__ = ("lines", "touched")

    def __init__(self) -> None:
        self.touched: list[int] = []
        self.lines: list[str] = []

    def say(self, line: str) -> None:
        """Добавить строку отчёта."""
        self.lines.append(line)

    def __repr__(self) -> str:  # pragma: no cover — диагностика в отладке
        return f"Outcome(touched={self.touched}, lines={self.lines})"


def pulls_awaiting_auto_merge(items: list[dict[str, Any]]) -> list[int]:
    """Номера PR из выдачи issue-эндпоинта — чистая функция, без сети.

    GitHub отдаёт PR через тот же эндпоинт, что и issue, отличая их полем
    ``pull_request``. Issue с той же меткой мержить нечего, и без этого
    отбора скрипт полез бы включать им авто-мерж.
    """
    # Проверяется НАЛИЧИЕ ключа, а не его истинность: пустой объект по нему —
    # всё ещё PR, а `if item.get(...)` отбросил бы такой ответ молча.
    return [
        int(item.get("number", 0))
        for item in items
        if "pull_request" in item and int(item.get("number", 0))
    ]


def _auto_merge_enabled(data: dict[str, Any]) -> bool:
    """Включён ли авто-мерж у этого PR (REST отдаёт объект или ``null``)."""
    return bool(data.get("auto_merge"))


def labels_of(item: dict[str, Any]) -> set[str]:
    """Имена меток PR из ответа REST — чистая функция, без сети."""
    raw = item.get("labels") or []
    return {
        str(label.get("name", "")) for label in raw if isinstance(label, dict) and label.get("name")
    }


def apply_default_consent(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    dry_run: bool = False,
    **kwargs: Any,
) -> Outcome:
    """Проставить согласие по умолчанию: метка на каждом PR, кроме исключённых.

    issue #1325 переворачивает умолчание #1303: раньше молчание означало «не
    мержить», теперь — «мержить по зелёному». Размен назван прямо: цена ошибки
    больше не «PR простоял зря», а «PR уехал раньше, чем на него посмотрели».
    Смягчает его защита ветки — уедет только PR со всеми зелёными проверками на
    актуальном состоянии, то есть автоматика ускоряет готовое, а не пропускает
    недоделанное.

    Исключения: черновик (работа не предъявлена), PR из форка (ведёт внешний
    автор), :data:`CONFLICT_LABEL` (сначала слияние вручную) и стоп-метка
    :data:`HOLD_LABEL`.

    **Стоп-метка сильнее и переживает обход.** Отличить «метку ещё не ставили»
    от «поставили и сняли» по состоянию PR нельзя — оно одинаковое, — поэтому
    снятое человеком согласие вернулось бы следующим же проходом. `hold`
    выражает решение явно: увидев её, механизм не ставит согласие, а уже
    стоящее — снимает.
    """
    outcome = Outcome()
    pulls = gh_rest.request("GET", f"repos/{repo}/pulls?state=open&per_page=100", **kwargs).data
    items = [item for item in (pulls if isinstance(pulls, list) else []) if isinstance(item, dict)]
    if not items:
        outcome.say("открытых PR нет — размечать нечего")
        return outcome

    if not dry_run:
        gh_rest.ensure_label(
            repo, LABEL, color=_CONSENT_COLOR, description=_CONSENT_DESCRIPTION, **kwargs
        )
        gh_rest.ensure_label(
            repo, HOLD_LABEL, color=_HOLD_COLOR, description=_HOLD_DESCRIPTION, **kwargs
        )

    for item in items:
        number = int(item.get("number", 0))
        if not number:
            continue
        labels = labels_of(item)
        if HOLD_LABEL in labels:
            if LABEL in labels:
                if not dry_run:
                    gh_rest.remove_label(repo, number, LABEL, **kwargs)
                outcome.touched.append(number)
                outcome.say(f"PR #{number}: стоит «{HOLD_LABEL}» — согласие снято")
            else:
                outcome.say(f"PR #{number}: стоит «{HOLD_LABEL}» — автоматике не отдаём")
            continue
        if item.get("draft"):
            outcome.say(f"PR #{number}: черновик — работа ещё не предъявлена")
            continue
        if (item.get("head") or {}).get("repo", {}).get("fork"):
            outcome.say(f"PR #{number}: из форка — метки ставит мейнтейнер при разборе")
            continue
        if CONFLICT_LABEL in labels:
            outcome.say(f"PR #{number}: стоит «{CONFLICT_LABEL}» — сначала слияние вручную")
            continue
        if LABEL in labels:
            continue
        if not dry_run:
            gh_rest.add_labels(repo, number, [LABEL], **kwargs)
        outcome.touched.append(number)
        outcome.say(f"PR #{number}: согласие проставлено по умолчанию")
    return outcome


def enable_for_labelled(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    label: str = LABEL,
    dry_run: bool = False,
    **kwargs: Any,
) -> Outcome:
    """Включить авто-мерж всем открытым PR с меткой, кому он ещё не включён."""
    outcome = Outcome()
    numbers = pulls_awaiting_auto_merge(gh_rest.issues_with_label(repo, label, **kwargs))
    if not numbers:
        outcome.say(f"PR с меткой «{label}» нет — включать нечего")
        return outcome

    for number in numbers:
        data = gh_rest.pull(repo, number, **kwargs)
        if data.get("draft"):
            outcome.say(f"PR #{number}: черновик — согласия на мерж ещё нет, пропускаю")
            continue
        if (data.get("head") or {}).get("repo", {}).get("fork"):
            outcome.say(f"PR #{number}: из форка — авто-мерж включает его автор, пропускаю")
            continue
        if _auto_merge_enabled(data):
            outcome.say(f"PR #{number}: авто-мерж уже включён")
            continue
        if dry_run:
            outcome.touched.append(number)
            outcome.say(f"PR #{number}: включил бы авто-мерж")
            continue
        try:
            gh_rest.enable_auto_merge(repo, number, **kwargs)
        except gh_rest.GitHubError as exc:
            # Отказ на одном PR не должен лишать остальных: они помечены тем же
            # согласием и ждут того же самого.
            outcome.say(f"PR #{number}: авто-мерж не включился ({exc})")
            continue
        outcome.touched.append(number)
        outcome.say(f"PR #{number}: авто-мерж включён — уедет, как позеленеет")
    return outcome


def disable_for(
    repo: str,
    number: int,
    *,
    dry_run: bool = False,
    **kwargs: Any,
) -> Outcome:
    """Выключить авто-мерж у PR — метку сняли, согласие отозвано."""
    outcome = Outcome()
    data = gh_rest.pull(repo, number, **kwargs)
    if not _auto_merge_enabled(data):
        outcome.say(f"PR #{number}: авто-мерж и не был включён")
        return outcome
    if dry_run:
        outcome.touched.append(number)
        outcome.say(f"PR #{number}: выключил бы авто-мерж")
        return outcome
    try:
        gh_rest.disable_auto_merge(repo, number, **kwargs)
    except gh_rest.GitHubError as exc:
        outcome.say(f"PR #{number}: авто-мерж не выключился ({exc})")
        return outcome
    outcome.touched.append(number)
    outcome.say(f"PR #{number}: авто-мерж выключен — согласие отозвано")
    return outcome


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — механизм отработал; 1 — сеть или квота, то есть «повторить позже»."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/merge_when_green.py",
        description=f"Включить авто-мерж PR с меткой «{LABEL}»; снятая метка его выключает.",
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument("--label", default=LABEL, help="метка-согласие")
    parser.add_argument(
        "--disable",
        type=int,
        metavar="PR",
        help="выключить авто-мерж у PR (метку сняли)",
    )
    parser.add_argument(
        "--no-default-consent",
        action="store_true",
        help="не проставлять метку по умолчанию — только включить авто-мерж помеченным",
    )
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не меняя")
    args = parser.parse_args(argv)

    try:
        if args.disable:
            outcome = disable_for(args.repo, args.disable, dry_run=args.dry_run)
        else:
            # issue #1325: сперва проставить согласие по умолчанию, затем
            # включить авто-мерж помеченным. Порядок именно такой: иначе PR,
            # получивший метку на этом же проходе, ждал бы следующего.
            outcome = Outcome()
            if not args.no_default_consent:
                marked = apply_default_consent(args.repo, dry_run=args.dry_run)
                outcome.touched.extend(marked.touched)
                outcome.lines.extend(marked.lines)
            enabled = enable_for_labelled(args.repo, label=args.label, dry_run=args.dry_run)
            outcome.touched.extend(enabled.touched)
            outcome.lines.extend(enabled.lines)
    except gh_rest.RateLimited as exc:
        print(f"квота GitHub исчерпана: {exc}")
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        print(f"список PR не прочитан: {exc}")
        return gh_rest.EXIT_FAIL

    for line in outcome.lines:
        print(line)
    return gh_rest.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
