#!/usr/bin/env python3
"""scripts/measure_queue_wakeups.py — чем на самом деле будится очередь (issue #1427).

Правило 169 каталога: расписание площадки — **пожелание, а не частота**, и у
механизма, объявленного страховкой, обязан быть замер. Пока замера нет,
«работает» держится верой, а основной путь строят так, будто страховка есть.

Дороже всего не задержка сама по себе, а то, что страховка **меняет решения о
первом пути**. Список событий у мувера узкий — ``workflows: ["CI"]``, — и узкий
он именно потому, что «в крайнем случае подхватит расписание». У соседа так и
вышло: добавили обязательную проверку, последним стал зеленеть новый прогон,
которого в списке не было, и момент готовности очередь перестала видеть вовсе.

**Замер выводится из живых прогонов, а не хранится.** Площадка помнит, каким
событием запущен каждый прогон; хранимое состояние здесь разъехалось бы и
начало отвечать на другой вопрос.

Исход отказа **один и односторонний**: страховка объявлена, а по расписанию за
всё окно не пришло ни одного прогона — значит её нет вовсе. Порог «сколько
процентов достаточно» намеренно не вводится: он был бы выдуман, а не измерен.
Числа печатаются всегда, потому что вопрос правила — не «хорошо ли», а
«измерено ли».

Три исхода (правило 039): ``0`` — замер снят, ``1`` — страховка объявлена и не
срабатывает, ``2`` — замер снять не удалось.

Запуск::

    python scripts/measure_queue_wakeups.py
    python scripts/measure_queue_wakeups.py --limit 200
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as _datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "DEFAULT_LIMIT",
    "INSURANCE_MARKER",
    "Measurement",
    "declares_insurance",
    "main",
    "measure",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MOVER = _ROOT / ".github" / "workflows" / "merge-queue.yml"

#: Сколько последних прогонов берём. Сто — это около недели работы очереди:
#: достаточно, чтобы интервалы расписания перестали быть единичным наблюдением.
DEFAULT_LIMIT = 100

#: Слово, которым мувер объявляет расписание страховкой. Замер требуется только
#: у объявленной страховки: расписание, никем страховкой не названное, — просто
#: расписание, и правило его не касается.
INSURANCE_MARKER = "Страховка"


class Measurement:
    """Чем будился механизм за окно наблюдения."""

    __slots__ = ("by_event", "gaps_minutes", "first", "last")

    def __init__(
        self,
        by_event: dict[str, int],
        gaps_minutes: list[float],
        first: str,
        last: str,
    ) -> None:
        self.by_event = by_event
        self.gaps_minutes = gaps_minutes
        self.first = first
        self.last = last

    @property
    def scheduled(self) -> int:
        """Сколько прогонов пришло по расписанию."""
        return self.by_event.get("schedule", 0)

    @property
    def window_hours(self) -> float:
        """Длина окна наблюдения в часах; ``0`` — окна нет."""
        if not (self.first and self.last):
            return 0.0
        start = _datetime.datetime.fromisoformat(self.first.replace("Z", "+00:00"))
        end = _datetime.datetime.fromisoformat(self.last.replace("Z", "+00:00"))
        return max((end - start).total_seconds() / 3600, 0.0)


def declares_insurance(text: str) -> bool:
    """Назван ли график страховкой в самом файле прогона."""
    return INSURANCE_MARKER.lower() in text.lower()


def measure(runs: list[dict[str, object]]) -> Measurement:
    """Свести прогоны к замеру: чем будились и с какими интервалами.

    Args:
        runs: Прогоны как их отдаёт площадка (нужны ``event`` и ``created_at``).

    Returns:
        Замер; на пустом входе — пустой замер, а не выдуманные числа.
    """
    by_event = collections.Counter(str(run.get("event") or "") for run in runs)
    stamps = sorted(str(run.get("created_at") or "") for run in runs if run.get("created_at"))
    scheduled = sorted(
        str(run.get("created_at") or "")
        for run in runs
        if run.get("event") == "schedule" and run.get("created_at")
    )
    moments = [_datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) for s in scheduled]
    gaps = [
        (moments[index + 1] - moments[index]).total_seconds() / 60
        for index in range(len(moments) - 1)
    ]
    return Measurement(
        by_event=dict(by_event),
        gaps_minutes=gaps,
        first=stamps[0] if stamps else "",
        last=stamps[-1] if stamps else "",
    )


def _report(measurement: Measurement) -> None:
    """Напечатать замер числами — иначе «работает» держится верой."""
    total = sum(measurement.by_event.values())
    print(f"Пробуждения очереди: прогонов — {total}, окно — {measurement.window_hours:.0f} ч.")
    for event, count in sorted(measurement.by_event.items(), key=lambda item: -item[1]):
        print(f"  · {event}: {count}")
    gaps = measurement.gaps_minutes
    if gaps:
        ordered = sorted(gaps)
        print(
            f"  · интервалы по расписанию, мин: минимум {min(gaps):.0f}, "
            f"медиана {ordered[len(ordered) // 2]:.0f}, максимум {max(gaps):.0f}"
        )


def main(argv: list[str] | None = None) -> int:
    """0 — замер снят, 1 — объявленная страховка не срабатывает, 2 — не отработало."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    try:
        payload = gh_rest.request(
            "GET",
            f"repos/{args.repo}/actions/workflows/merge-queue.yml/runs?per_page={args.limit}",
        ).data
    except gh_rest.RateLimited as error:
        print(f"замер не снят: {error}")
        return 2
    except (gh_rest.GitHubError, gh_rest.MissingToken, OSError) as error:
        print(f"замер не снят: {error}")
        return 2

    runs = (payload or {}).get("workflow_runs") if isinstance(payload, dict) else None
    if not runs:
        print("замер не снят: прогонов мувера не нашлось — окна наблюдения нет")
        return 2

    measurement = measure(list(runs))
    _report(measurement)

    if not _MOVER.exists():
        print("страховкой график нигде не объявлен — замер справочный")
        return 0
    if not declares_insurance(_MOVER.read_text(encoding="utf-8")):
        print("страховкой график не объявлен — правило 169 предмета здесь не имеет")
        return 0

    if measurement.scheduled == 0:
        print(
            "FAIL: график объявлен страховкой, а по расписанию за всё окно не пришло "
            "ни одного прогона — страховки нет, и основной путь строится на том, "
            "чего не существует."
        )
        return 1
    print("График объявлен страховкой и срабатывает; частота — в числах выше, а не в шапке.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
