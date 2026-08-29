#!/usr/bin/env python3
"""scripts/rerun_flaky_checks.py — мигнувшую проверку перезапускает механизм.

Частичный перезапуск у нас есть (``gh_rest.rerun_failed_jobs``), и он умеет
ровно то, что нужно. Беда в том, **кто** может его позвать: запись в Actions
требует ``actions:write``, а у облачной сессии его нет — прокси закрывает
запись, и GitHub отвечает ``403 Resource not accessible by integration``.
Дежурное окно видит мигнувшую проверку, умеет её перезапустить и не может
этого сделать; остаётся звать человека ради одного клика.

У workflow права есть — тот же приём, которым живёт ``merge-queue.yml``, где
не хватало штатного токена. Этот скрипт и есть то, что дежурное окно сделало
бы руками.

**Главный предохранитель — закрытый список.** Перезапуск не чинит, он меняет
исход, не меняя причины, поэтому автоматическим он допустим только там, где
красный цвет заведомо не означает «в коде проблема». Сейчас в списке
:data:`AUTO_RERUN` одно имя — ``claude-review``: найденный ревьюером дефект не
роняет job (замечания приходят комментариями, прогон завершается успешно), то
есть красный означает осечку исполнения. Настоящие проверки (``test``,
``static``, ``docs-guardrails`` и прочие из обязательного набора) не
перезапускаются никогда, и добавить их в список можно только отдельным PR с
обоснованием.

Второй предохранитель — **одна попытка**. ``run_attempt`` за порогом
:data:`gh_rest.MAX_ATTEMPTS` означает уже не мигание, а дефект, и разбирать
надо его.

Третий — **все красные разом**. Если рядом с разрешённой к перезапуску
проверкой красна хоть одна другая, PR не трогается вовсе: чинить надо ту,
вторую, а зелёная соседка ничего не изменит.

Запуск::

    python scripts/rerun_flaky_checks.py             # перезапустить, что можно
    python scripts/rerun_flaky_checks.py --dry-run   # показать, ничего не делая
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contextlib

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "AUTO_RERUN",
    "SKIP_LABEL",
    "Outcome",
    "failed_checks",
    "main",
    "rerun_flaky",
    "runs_by_suite",
]

#: Проверки, которые механизм вправе перезапустить сам. Список **закрытый** и
#: пополняется отдельным PR: каждое имя здесь — утверждение «красный цвет этой
#: проверки не означает дефект в коде», и оно требует обоснования.
#:
#: ``claude-review`` (workflow «Claude Code Review») отвечает этому условию:
#: замечания ревьюер оставляет комментариями, а job при этом зеленеет, — то
#: есть красный получается только от осечки исполнения. Прецедент того же
#: класса — падение на каждом PR из-за события от бота, чинившееся строкой
#: ``allowed_bots``.
AUTO_RERUN: frozenset[str] = frozenset({"claude-review"})

#: Стоп-метка конвейера: раз PR намеренно придержан, ничего ему не перезапускаем.
SKIP_LABEL = "hold"

#: Прогон, ещё не завершившийся, перезапускать нечего — и незачем.
_COMPLETED = "completed"


class Outcome:
    """Что механизм сделал за проход — для отчёта и для тестов."""

    def __init__(self) -> None:
        self.rerun: list[int] = []
        self.lines: list[str] = []

    def say(self, line: str) -> None:
        """Записать строку отчёта (она же — строка summary прогона)."""
        self.lines.append(line)

    @property
    def report(self) -> str:
        """Отчёт целиком; пустой проход тоже говорит о себе вслух."""
        if not self.lines:
            return "перезапускать нечего: ни одного PR с одной лишь мигнувшей проверкой"
        return "\n".join(self.lines)


def failed_checks(repo: str, sha: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Завершённые красные check-run'ы коммита.

    Что считать красным, решает :func:`gh_rest.summarize_checks` — тот же
    источник, которым пользуются очередь и гейт мержа. Своей копии правила
    здесь нет намеренно: разойдясь, она давала бы механизму другое мнение о
    цвете PR, чем у всего остального конвейера.

    Отбор идёт по **свежайшей** записи на имя. При перезапуске старый красный
    check-run никуда не девается, и без этого механизм видел бы его рядом с
    новым зелёным — то есть перезапускал бы уже перезапущенное.

    Незавершённые сюда не попадают: «ещё идёт» — не «упало».
    """
    payload = gh_rest.pull_checks(repo, sha, **kwargs)
    listed = payload.get("check_runs", []) if isinstance(payload, dict) else []
    freshest = gh_rest.latest_checks_by_name([item for item in listed if isinstance(item, dict)])
    _, _, red = gh_rest.summarize_checks(payload)
    red_names = set(red)
    return [run for run in freshest if str(run.get("name", "")) in red_names]


def runs_by_suite(repo: str, sha: str, **kwargs: Any) -> dict[int, dict[str, Any]]:
    """Прогоны Actions коммита, разложенные по ``check_suite_id``.

    Связь именно по suite, а не по имени: имя джоба (``claude-review``) и имя
    workflow («Claude Code Review») — разные строки, и сопоставлять их значило
    бы завести второй список, который разойдётся с первым.
    """
    payload = gh_rest.workflow_runs(repo, sha, **kwargs)
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    result: dict[int, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        suite = run.get("check_suite_id")
        if isinstance(suite, int):
            result[suite] = run
    return result


def _suite_id(check: dict[str, Any]) -> int | None:
    suite = check.get("check_suite")
    if isinstance(suite, dict) and isinstance(suite.get("id"), int):
        return int(suite["id"])
    return None


def _skip_reason(pull: gh_rest.PullSummary) -> str | None:
    """Почему этот PR не рассматривается вовсе (``None`` — рассматривается)."""
    if pull.draft:
        return "черновик"
    if pull.fork:
        return "из форка: чужой репозиторий, прав на его Actions у нас нет"
    if SKIP_LABEL in pull.labels:
        return f"метка «{SKIP_LABEL}»: PR придержан намеренно"
    return None


def rerun_flaky(
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    dry_run: bool = False,
    **kwargs: Any,
) -> Outcome:
    """Перезапустить мигнувшие проверки там, где выполнены все условия."""
    outcome = Outcome()

    for pull in gh_rest.list_pulls(repo, **kwargs):
        skip = _skip_reason(pull)
        if skip is not None:
            continue  # молча: это не кандидаты, а обычное состояние конвейера

        failed = failed_checks(repo, pull.sha, **kwargs)
        if not failed:
            continue

        names = {str(check.get("name", "")) for check in failed}
        foreign = sorted(names - AUTO_RERUN)
        if foreign:
            outcome.say(
                f"#{pull.number}: не трогаю — красные проверки вне списка: "
                f"{', '.join(foreign)}. Их чинят, а не перезапускают"
            )
            continue

        suites = runs_by_suite(repo, pull.sha, **kwargs)
        seen: set[int] = set()
        for check in failed:
            suite = _suite_id(check)
            run = suites.get(suite) if suite is not None else None
            if run is None:
                outcome.say(
                    f"#{pull.number}: проверке «{check.get('name')}» не нашлось прогона "
                    "Actions — перезапускать нечего"
                )
                continue

            run_id = int(run.get("id", 0))
            if run_id in seen:
                continue
            seen.add(run_id)

            attempt = run.get("run_attempt")
            attempt = attempt if isinstance(attempt, int) else 1
            if attempt >= gh_rest.MAX_ATTEMPTS:
                outcome.say(
                    f"#{pull.number}: прогон {run_id} уже перезапускали "
                    f"(попытка {attempt}) — это не мигание, а дефект; разбирать надо его"
                )
                continue

            if run.get("status") != _COMPLETED:
                outcome.say(f"#{pull.number}: прогон {run_id} ещё идёт — жду его исхода")
                continue

            if dry_run:
                outcome.rerun.append(run_id)
                outcome.say(f"#{pull.number}: перезапустил бы прогон {run_id}")
                continue

            try:
                started = gh_rest.rerun_failed_jobs(repo, run_id, **kwargs)
            except gh_rest.GitHubError as exc:
                # Прогон механизма остаётся зелёным: красный здесь означал бы
                # «механизм сломан», а не «одному PR не повезло».
                outcome.say(f"#{pull.number}: перезапуск прогона {run_id} не прошёл ({exc})")
                continue

            if started:
                outcome.rerun.append(run_id)
                outcome.say(
                    f"#{pull.number}: перезапустил упавшие джобы прогона {run_id} "
                    f"(«{check.get('name')}» — единственная красная проверка)"
                )
            else:
                outcome.say(f"#{pull.number}: в прогоне {run_id} перезапускать нечего")

    return outcome


def main(argv: list[str] | None = None) -> int:
    """Пройти по открытым PR и перезапустить то, что разрешено."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать решения, не трогая Actions",
    )
    args = parser.parse_args(argv)

    try:
        outcome = rerun_flaky(args.repo, dry_run=args.dry_run)
    except gh_rest.RateLimited as exc:
        # Кончившаяся квота — не поломка механизма, а состояние аккаунта:
        # расписание вернётся через полчаса. Красный прогон здесь означал бы
        # «почини перезапуск», а чинить нечего.
        print(f"квота GitHub исчерпана, вернусь следующим прогоном: {exc}")
        return gh_rest.EXIT_OK
    except gh_rest.GitHubError as exc:
        print(f"FAIL: {exc}")
        return gh_rest.EXIT_FAIL

    print(outcome.report)
    return gh_rest.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
