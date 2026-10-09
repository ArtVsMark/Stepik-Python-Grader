#!/usr/bin/env python3
"""scripts/ci_complete_wake.py — будильник сводного гейта (issue #1554).

``ci-complete`` ждёт соседей не дольше своего срока
(``ci_aggregate.DEADLINE_SECONDS``). Под очередью раннеров прогон ``CI``
кончается позже срока, и сводка краснеет «не дождались» при полностью зелёной
голове. Перезапускается она только событиями pull request, поэтому красное
остаётся навсегда и держит мерж: ``ci-complete`` — единственная обязательная
проверка. Замер 01.10.2026 (PR #1548): сводка упала ровно по сроку, а у ``CI``
ещё шли пять джобов из двадцати; то же на #1528 и #1530.

ПРИЁМ ПЕРЕНЯТ У Engineering-Pipeline-Mechanisms (``ci_complete_wake.py``,
решение владельца там от 04.10.2026). Прогон по завершении ``CI`` перезапускает
красную сводку на той же голове, если её вердикт вынесен, пока ``CI`` ещё шёл.

ПЕРЕЗАПУСК, А НЕ НОВАЯ ЗАПИСЬ. Прогон по ``workflow_run`` вешает свои
проверки на голову ``main``, а не изменения, и защита ветки их не увидит.
Перезапуск прогона ``ci-complete`` идёт на его собственном событии
``pull_request``, и вердикт ложится на ту голову, которую ждёт защита.

ЧЬЯ СВОДКА ПЕРЕЗАПУСКАЕТСЯ. Последняя на голове, завершённая красным, не позже
чем через :data:`VERDICT_TAIL` после конца ``CI``: время завершения площадка
ставит после хвоста джоба (выгрузка вывода, закрытие шагов), а не в миг
вердикта. Идущую не трогают — дождётся сама; отменённую тоже — её заменил
более новый заход. Красное, вынесенное позже конца ``CI`` и запаса, —
настоящее.

ОТЛИЧИЕ ОТ ИСТОЧНИКА. Будильник просыпается только на ЗЕЛЁНОМ ``CI``
(фильтр в ``ci-complete-wake.yml``). Красный ``CI`` перекрасит сводку в тот же
красный, и перезапуск был бы холостым прогоном в той же перегруженной очереди;
у механизмов эта граница названа и оплачена, здесь она закрыта условием.

ЧЕГО БУДИЛЬНИК НЕ ЛЕЧИТ. Обязательная проверка, упавшая не по коду (зависшая
установка Playwright — тот же PR #1548), красит сам ``CI``, и будильник не
просыпается. Это отдельный вопрос: перезапуск упавшего шага подготовки трогает
правило «обязательные не перезапускаются никогда».

Исходы: ``0`` — перезапущено или перезапускать нечего, что именно — названо;
``2`` — будильник не отработал (нет токена, площадка не ответила).

Запуск::

    python scripts/ci_complete_wake.py --sha <голова> --ci-done-at <ISO 8601>
    python scripts/ci_complete_wake.py ... --dry-run  # назвать, не перезапуская
"""

import argparse
import contextlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "MAX_ATTEMPTS",
    "SUMMARY_FLOW",
    "VERDICT_TAIL",
    "NotRun",
    "main",
    "stale_summary",
    "summary_runs",
    "wake",
]

#: Файл прогона сводного гейта: его прогоны на голове и перезапускаются.
SUMMARY_FLOW: Final = "ci-complete.yml"
#: После скольких попыток прогона сводки будильник его больше не зовёт. Три:
#: два завершения ``CI`` на одной голове (прогон и его перезапуск) плюс запас.
#: Держится номером попытки у площадки, а не памятью будильника.
MAX_ATTEMPTS: Final = 3
#: Сколько после конца ``CI`` сводка ещё может завершиться с вердиктом,
#: вынесенным до него: время завершения площадка ставит после хвоста джоба.
VERDICT_TAIL: Final = timedelta(minutes=2)


class NotRun(RuntimeError):
    """Будильник не отработал: третий исход, а не «перезапускать нечего»."""


def moment(said: str) -> datetime:
    """Время площадки (ISO 8601, ``Z`` или смещение); непрочитанное — отказ захода."""
    try:
        return datetime.fromisoformat(said)
    except ValueError as exc:
        raise NotRun(f"время площадки не прочитано: {said!r}") from exc


def stale_summary(runs: list[dict[str, Any]], ci_done_at: str) -> dict[str, Any] | None:
    """Прогон сводки, чей красный вердикт вынесен раньше конца ``CI``; иначе ``None``.

    Берётся ПОСЛЕДНИЙ прогон головы: прежние заменены им, и перезапуск старого
    вынес бы вердикт рядом с новым. Сравнивается время, а не строки — иначе к
    нему не прибавить запас.
    """
    if not runs:
        return None
    last = max(runs, key=lambda run: str(run.get("created_at") or ""))
    if last.get("status") != "completed" or last.get("conclusion") != "failure":
        return None
    # Запас вычитается из времени сводки, а не прибавляется к концу CI: у
    # ручного захода конец CI может быть последним мигом календаря.
    if moment(str(last.get("updated_at") or "")) - VERDICT_TAIL > moment(ci_done_at):
        return None
    if int(last.get("run_attempt") or 1) >= MAX_ATTEMPTS:
        return None
    return last


def summary_runs(repo: str, sha: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Прогоны сводного гейта на голове — только на событии ``pull_request``."""
    data = gh_rest.request(
        "GET",
        f"repos/{repo}/actions/workflows/{SUMMARY_FLOW}/runs"
        f"?head_sha={sha}&event=pull_request&per_page=100",
        **kwargs,
    ).data
    runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
    return [run for run in runs if isinstance(run, dict)]


def wake(repo: str, sha: str, ci_done_at: str, *, dry_run: bool = False, **kwargs: Any) -> str:
    """Перезапускает устаревшую сводку на голове; возвращает, что сделано, словами."""
    short = sha[:8]
    runs = summary_runs(repo, sha, **kwargs)
    if not runs:
        return f"на голове {short} прогонов сводки нет — будить нечего"
    stale = stale_summary(runs, ci_done_at)
    if stale is None:
        return (
            f"сводка на голове {short} не устарела — "
            "идёт, зелёная, отменена, вынесена после CI или исчерпала попытки"
        )
    run_id = int(stale["id"])
    if dry_run:
        return f"перезапустил бы сводку {run_id} на голове {short}"
    try:
        restarted = gh_rest.rerun_failed_jobs(repo, run_id, **kwargs)
    except gh_rest.GitHubError as exc:
        raise NotRun(f"сводка {run_id} не перезапущена: {exc}") from exc
    if not restarted:
        return f"сводка {run_id} на голове {short} уже перезапускается — второй раз не зову"
    return f"сводка {run_id} на голове {short} перезапущена: её вердикт вынесен раньше конца CI"


def main(argv: list[str] | None = None) -> int:
    """Точка входа: одна голова, одно завершение ``CI``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY") or gh_rest.DEFAULT_REPO
    )
    parser.add_argument("--sha", default=os.environ.get("HEAD_SHA", ""))
    parser.add_argument(
        "--ci-done-at",
        default=os.environ.get("CI_DONE_AT", ""),
        help="когда кончился прогон CI, ISO 8601 площадки",
    )
    parser.add_argument("--dry-run", action="store_true", help="назвать, не перезапуская")
    args = parser.parse_args(argv)
    try:
        if not args.sha or not args.ci_done_at:
            raise NotRun("не названы голова или время конца CI")
        print(wake(args.repo, args.sha, args.ci_done_at, dry_run=args.dry_run))
    except (NotRun, gh_rest.GitHubError) as exc:
        print(f"будильник не отработал: {exc}", file=sys.stderr)
        return gh_rest.EXIT_WAIT
    return gh_rest.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
