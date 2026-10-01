#!/usr/bin/env python3
"""scripts/rerun_red_main.py — красная база получает ОДИН перезапуск (issue #1524).

Красная ``main`` замораживает очередь мержа (#1326, #1510): пока база сломана,
не двигается никто, кроме PR с меткой ``blocker``. Правило верное — мержить
поверх сломанной базы бессмысленно, — но у него не было выхода из положения,
когда база красная **не по делу**.

Замер 08–09.09.2026, две красноты подряд, обе по ОДНОЙ упавшей проверке:

* ``test (macos-latest, 3.12)`` после мержа #1516 — следующие три мержа дали
  зелёные прогоны, то есть это было мигание;
* ``e2e`` после мержа #1518 — весь набор на той же голове локально прошёл
  целиком (128 passed).

Каждый раз очередь замирала, и разорвать круг было нечем: новый прогон ``main``
рождается только новым мержем, которого заморозка и не даёт, а перезапуск из
облачной сессии закрыт (``403`` на ``actions:write``). Оставался клик человека.

ПОЧЕМУ ЭТО НЕ ГЛУШИЛКА КРАСНОТЫ. Соседний механизм (``rerun_flaky_checks.py``)
защищается **закрытым списком имён**: обязательную проверку не перезапускают
никогда, потому что вторая попытка способна превратить настоящий дефект в
зелёный прогон. Здесь риск снимается по другой оси, и обе половины обязательны:

* **упала ровно одна** проверка. Настоящая поломка почти никогда не роняет одну
  ячейку матрицы — она валит сборку, линтер или весь ряд ОС; мигание, наоборот,
  почти всегда одиночное. Две красные и больше — механизм не трогает ничего;
* **попытка ровно одна**. Второй перезапуск — уже не проверка мигания, а
  добывание зелёного.

Не помогло — заморозка остаётся, и двигается только PR с меткой ``blocker``.
Механизм не отменяет правило, а доводит его до конца.

СЛЕД ОБЯЗАТЕЛЕН. Перезапуск пишет, что и почему перезапустил: «прогон был
красным и стал зелёным» без объяснения выглядит как исчезнувшая улика.

Запуск::

    python scripts/rerun_red_main.py            # перезапустить, если условия сошлись
    python scripts/rerun_red_main.py --dry-run  # сказать, что сделал бы
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_rest
import rerun_flaky_checks

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "BASE_BRANCH",
    "Decision",
    "decide",
    "main",
    "rerun_red_base",
]

#: Ветка, чья краснота замораживает очередь.
BASE_BRANCH = "main"

#: Сколько упавших проверок ещё считается миганием. Ровно одна — и это не
#: настройка «пока хватает», а сама суть правила: см. модульный докстринг.
_LONE_FAILURE = 1


class Decision:
    """Что делать с красной базой и почему именно это.

    Отдельный тип, а не кортеж: решение печатается в отчёт дословно, и причина
    нужна одинаковая и человеку, и тесту.
    """

    __slots__ = ("reason", "rerun", "run_id")

    def __init__(self, *, rerun: bool, reason: str, run_id: int | None = None) -> None:
        self.rerun = rerun
        self.reason = reason
        self.run_id = run_id

    def __repr__(self) -> str:  # pragma: no cover — диагностика в отладке
        return f"Decision(rerun={self.rerun}, reason={self.reason!r}, run_id={self.run_id})"


def decide(
    *,
    conclusion: str | None,
    status: str | None,
    failed: list[str],
    attempt: int,
    run_id: int | None,
) -> Decision:
    """Решение по последнему прогону базы — чистая функция, без сети.

    Args:
        conclusion: исход прогона (``failure``/``success``/…); ``None`` — не завершён.
        status: состояние прогона; всё, кроме ``completed``, означает «идёт».
        failed: имена упавших проверок на голове базы.
        attempt: какая это попытка прогона (``run_attempt`` GitHub).
        run_id: идентификатор прогона — его и перезапускать.

    Returns:
        Решение с причиной, годной для отчёта.
    """
    if status != "completed":
        return Decision(rerun=False, reason="прогон базы ещё идёт — ждать, а не перезапускать")
    # Приватное имя транспорта взято намеренно: своя копия списка зелёных
    # исходов дала бы механизму СВОЁ мнение о цвете базы, а расходиться ему
    # с очередью и гейтом нельзя. Публичной точки для этого пока нет —
    # issue заведена, здесь ждёт её единственный потребитель извне.
    if conclusion in gh_rest._OK_CONCLUSIONS:
        return Decision(rerun=False, reason="база зелёная — перезапускать нечего")
    if attempt > 1:
        return Decision(
            rerun=False,
            reason=(
                f"это уже попытка {attempt} — мигание проверяют один раз, "
                "дальше разбирают вручную; очередь двигает PR с меткой blocker"
            ),
        )
    if not failed:
        return Decision(
            rerun=False,
            reason=(
                "прогон красный, но ни одна проверка не названа упавшей — "
                "перезапуск тут не поможет, разбирать надо сам прогон"
            ),
        )
    if len(failed) > _LONE_FAILURE:
        return Decision(
            rerun=False,
            reason=(
                f"упало {len(failed)} проверок ({', '.join(sorted(failed))}) — "
                "это не мигание, а поломка; заморозка остаётся"
            ),
        )
    return Decision(
        rerun=True,
        reason=f"упала ровно одна проверка ({failed[0]}) на первой попытке — похоже на мигание",
        run_id=run_id,
    )


def rerun_red_base(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    dry_run: bool = False,
    **kwargs: Any,
) -> rerun_flaky_checks.Outcome:
    """Перезапустить упавшие джобы последнего прогона базы, если условия сошлись.

    Ветка не параметризуется: ``gh_rest.main_run`` спрашивает про ``main``
    жёстко, и лишний аргумент обещал бы настраиваемость, которой нет.
    """
    outcome = rerun_flaky_checks.Outcome()
    payload = gh_rest.main_run(repo, **kwargs)
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    listed = [run for run in runs if isinstance(run, dict)]
    if not listed:
        outcome.say(f"прогонов {BASE_BRANCH} не найдено — судить не по чему")
        return outcome

    run = listed[0]
    sha = str(run.get("head_sha", ""))
    failed = [str(name) for name in _failed_names(repo, sha, **kwargs)] if sha else []
    decision = decide(
        conclusion=run.get("conclusion"),
        status=run.get("status"),
        failed=failed,
        attempt=int(run.get("run_attempt") or 1),
        run_id=int(run.get("id") or 0) or None,
    )
    if not decision.rerun:
        outcome.say(f"{BASE_BRANCH}: {decision.reason}")
        return outcome

    if dry_run:
        outcome.say(f"{BASE_BRANCH}: перезапустил бы прогон {decision.run_id} — {decision.reason}")
        return outcome

    try:
        gh_rest.rerun_failed_jobs(repo, decision.run_id or 0, **kwargs)
    except gh_rest.GitHubError as exc:
        # Отказ площадки — не повод краснеть: прав на запись в Actions может не
        # быть (облачная сессия), и тогда это сообщение, а не поломка механизма.
        outcome.say(f"{BASE_BRANCH}: перезапуск не удался — {exc}")
        return outcome
    outcome.rerun.append(decision.run_id or 0)
    outcome.say(f"{BASE_BRANCH}: перезапущен прогон {decision.run_id} — {decision.reason}")
    return outcome


def _failed_names(repo: str, sha: str, **kwargs: Any) -> list[str]:
    """Имена красных проверок на голове базы — тем же разбором, что у соседа."""
    return [
        str(check.get("name", ""))
        for check in rerun_flaky_checks.failed_checks(repo, sha, **kwargs)
    ]


def main(argv: list[str] | None = None) -> int:
    """0 — механизм отработал (перезапустил или обоснованно не стал)."""
    parser = argparse.ArgumentParser(
        prog="python scripts/rerun_red_main.py",
        description=f"Один перезапуск красной «{BASE_BRANCH}», если упала ровно одна проверка.",
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не меняя")
    args = parser.parse_args(argv)

    try:
        outcome = rerun_red_base(args.repo, dry_run=args.dry_run)
    except gh_rest.RateLimited as exc:
        print(f"квота GitHub исчерпана: {exc}")
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        print(f"состояние базы не прочитано: {exc}")
        return gh_rest.EXIT_FAIL

    print(outcome.report)
    return gh_rest.EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
