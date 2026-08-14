#!/usr/bin/env python3
"""scripts/check_pr_ready.py — можно ли мержить этот PR (issue #997).

Инцидент, из которого вырос скрипт: PR смержили, когда 9 проверок из 14 были
``queued``/``in_progress``. Проверка была наивной — «нет ``failure`` и нет
``pending`` → мержим», — а сразу после ``git push`` GitHub ещё не успевает
создать check-runs и REST отдаёт **пустой список**. На пустоте условие «нет
красных, нет ожидающих» выполняется идеально.

Отсюда три правила, которые здесь и закодированы:

1. **Пустой или неполный список проверок — это «CI не стартовал», а не «зелено».**
   Поэтому смотрим не только check-runs, но и прогоны Actions для того же
   коммита: пока прогон в ``queued``/``in_progress``, часть джобов ещё не
   существует, и судить по ним нельзя.
2. **Набор проверок сверяется с эталоном** — именами с последнего завершённого
   прогона на ``main``. Отсутствующее имя означает «джоб не создан», и это
   ровно тот случай, который прошлую проверку обманул.
3. **Только REST.** ``gh pr view``/``gh pr checks`` ходят через GraphQL, и
   поллинг в цикле выжигает квоту 5000/час до нуля — посреди работы команды
   ``gh`` просто перестают отвечать. Интервал опроса — не чаще раза в 45-60 с.

Запуск::

    python scripts/check_pr_ready.py 1100          # вердикт по PR
    python scripts/check_pr_ready.py 1100 --json   # то же машинно

Код возврата 0 — можно мержить; 1 — нельзя (причина в выводе).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

__all__ = [
    "Verdict",
    "check_names",
    "default_fetch",
    "evaluate",
    "job_names",
    "latest_by_name",
    "main",
    "pending_runs",
    "workflows_running_on_pull_requests",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REPO = "ArtVsMark/Stepik-Python-Grader"
_API = "https://api.github.com"

# Заключения, которые не считаются провалом: пропущенный джоб — это условие в
# workflow, а не отказ, и требовать от него `success` значит никогда не мержить.
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

Fetch = Callable[[str], Any]


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Готовность PR: вердикт, причины «нет» и сводка по проверкам."""

    ready: bool
    reasons: list[str]
    total_checks: int
    completed: int
    missing: list[str]


def _gh_available() -> bool:
    """Есть ли ``gh`` в PATH — в локальном окне он обычно уже авторизован."""
    return shutil.which("gh") is not None


def default_fetch(path: str) -> Any:
    """GET по REST: через ``gh api``, иначе напрямую с токеном из окружения."""
    if _gh_available():
        try:
            raw = subprocess.check_output(
                ["gh", "api", path], cwd=_ROOT, text=True, stderr=subprocess.DEVNULL
            )
            return json.loads(raw)
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    request = urllib.request.Request(f"{_API}/{path.lstrip('/')}")
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"REST-запрос не удался ({path}): {exc}") from exc


def check_names(check_runs: dict[str, Any]) -> set[str]:
    """Имена проверок из ответа ``/commits/{sha}/check-runs``."""
    runs = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    return {str(run.get("name", "")) for run in runs if run.get("name")}


def latest_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить по одной, самой свежей записи на каждое имя проверки/прогона.

    ``ci.yml`` держит concurrency-группу и гасит устаревшие прогоны, а
    перезапуск (снятие черновика, повторный пуш) создаёт вторую запись с тем же
    именем на том же коммите. В ответе REST обе лежат рядом, и без этого отбора
    отменённый предшественник держал бы PR красным вечно — то есть гейт врал бы
    ровно там, где должен помогать.
    """
    freshest: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", ""))
        current = freshest.get(name)
        if current is None or _started(item) >= _started(current):
            freshest[name] = item
    return list(freshest.values())


def _started(item: dict[str, Any]) -> tuple[str, int]:
    """Ключ свежести: время старта, при равенстве — идентификатор."""
    return str(item.get("started_at") or item.get("run_started_at") or ""), int(item.get("id") or 0)


def pending_runs(workflow_runs: dict[str, Any]) -> list[str]:
    """Прогоны Actions, которые ещё не завершились (по ним джобы не созданы)."""
    runs = workflow_runs.get("workflow_runs", []) if isinstance(workflow_runs, dict) else []
    return [
        f"{run.get('name', 'workflow')} ({run.get('status')})"
        for run in latest_by_name(runs)
        if run.get("status") != "completed"
    ]


def evaluate(
    pull: dict[str, Any],
    workflow_runs: dict[str, Any],
    check_runs: dict[str, Any],
    expected: set[str],
) -> Verdict:
    """Собрать вердикт из состояния PR, прогонов Actions и check-runs."""
    reasons: list[str] = []

    if pull.get("state") != "open":
        reasons.append(f"PR не открыт (state={pull.get('state')})")
    if pull.get("draft"):
        reasons.append("PR — черновик")
    mergeable_state = pull.get("mergeable_state")
    if mergeable_state not in {"clean", "unstable", "has_hooks"}:
        reasons.append(f"ветка не готова к мержу (mergeable_state={mergeable_state})")
    if pull.get("mergeable") is False:
        reasons.append("GitHub сообщает о конфликте с базовой веткой")

    raw_runs = workflow_runs.get("workflow_runs", []) if isinstance(workflow_runs, dict) else []
    runs = latest_by_name(raw_runs)
    if not runs:
        reasons.append("для этого коммита нет ни одного прогона Actions — CI ещё не стартовал")
    still_running = pending_runs(workflow_runs)
    if still_running:
        reasons.append("прогоны не завершены: " + ", ".join(sorted(still_running)))
    failed_runs = [
        str(run.get("name", "workflow"))
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") not in _OK_CONCLUSIONS
    ]
    if failed_runs:
        reasons.append("красные прогоны: " + ", ".join(sorted(failed_runs)))

    raw_checks = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    listed = latest_by_name(raw_checks)
    if not listed:
        reasons.append("список проверок пуст — это «не стартовало», а не «зелено»")
    unfinished = [
        f"{run.get('name')} ({run.get('status')})"
        for run in listed
        if run.get("status") != "completed"
    ]
    if unfinished:
        reasons.append("проверки не завершены: " + ", ".join(sorted(unfinished)))
    red = [
        str(run.get("name"))
        for run in listed
        if run.get("status") == "completed" and run.get("conclusion") not in _OK_CONCLUSIONS
    ]
    if red:
        reasons.append("красные проверки: " + ", ".join(sorted(red)))

    missing = sorted(expected - check_names(check_runs))
    if missing:
        reasons.append("не создано проверок из эталонного набора: " + ", ".join(missing))

    completed = sum(1 for run in listed if run.get("status") == "completed")
    return Verdict(
        ready=not reasons,
        reasons=reasons,
        total_checks=len(listed),
        completed=completed,
        missing=missing,
    )


def job_names(jobs: dict[str, Any]) -> set[str]:
    """Имена джобов из ответа ``/actions/runs/{id}/jobs``."""
    listed = jobs.get("jobs", []) if isinstance(jobs, dict) else []
    return {str(job.get("name", "")) for job in listed if job.get("name")}


def workflows_running_on_pull_requests(workflows_dir: pathlib.Path) -> set[str]:
    """Имена workflow'ов (`name:`), у которых есть триггер ``pull_request``.

    Читается локально, а не через API: список триггеров живёт в репозитории и
    меняется вместе с ним. Нужно, чтобы в эталон не попадали прогоны, которых
    на PR не бывает по определению — иначе гейт краснеет всегда, а гейт,
    который краснеет всегда, обходят.
    """
    names: set[str] = set()
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        body, _, _ = text.partition("\njobs:")
        if not re.search(r"^\s{2}pull_request:", body, re.MULTILINE):
            continue
        title = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if title:
            names.add(title.group(1).strip().strip("'\""))
    return names


def _expected_names(fetch: Fetch, repo: str, workflows_dir: pathlib.Path) -> set[str]:
    """Эталонный набор — джобы тех прогонов ``main``, что бывают и на PR.

    Эталон берётся из живого состояния, а не из константы в коде: список джобов
    меняется вместе с ``ci.yml``, а зашитое число устаревает молча. Прогоны,
    которые на PR не запускаются (у их workflow нет триггера ``pull_request``),
    из эталона исключаются — иначе недостающим числится то, чего быть и не
    должно.
    """
    allowed = workflows_running_on_pull_requests(workflows_dir)
    try:
        sha = str(fetch(f"repos/{repo}/commits/main").get("sha", ""))
        if not sha:
            return set()
        runs = fetch(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
        names: set[str] = set()
        for run in runs.get("workflow_runs", []):
            if allowed and str(run.get("name", "")) not in allowed:
                continue
            jobs = fetch(f"repos/{repo}/actions/runs/{run.get('id')}/jobs?per_page=100")
            names |= job_names(jobs)
        return names
    except RuntimeError:
        return set()


def main(argv: list[str] | None = None, *, fetch: Fetch | None = None) -> int:
    """Напечатать вердикт готовности PR; 0 — можно мержить."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pull", type=int, help="номер pull request")
    parser.add_argument("--repo", default=_REPO, help="owner/repo (по умолчанию текущий проект)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    call = fetch or default_fetch
    try:
        pull = call(f"repos/{args.repo}/pulls/{args.pull}")
        sha = str(pull.get("head", {}).get("sha", ""))
        workflow_runs = call(f"repos/{args.repo}/actions/runs?head_sha={sha}&per_page=100")
        check_runs = call(f"repos/{args.repo}/commits/{sha}/check-runs?per_page=100")
    except RuntimeError as exc:
        print(f"Не удалось опросить GitHub: {exc}", file=sys.stderr)
        return 1

    expected = _expected_names(call, args.repo, _ROOT / ".github" / "workflows")
    verdict = evaluate(pull, workflow_runs, check_runs, expected)

    if args.json:
        print(json.dumps(dataclasses.asdict(verdict), ensure_ascii=False))
        return 0 if verdict.ready else 1

    print(f"PR #{args.pull}: проверок {verdict.completed}/{verdict.total_checks} завершено")
    if verdict.ready:
        print("Готов к мержу: все проверки созданы, завершены и зелёные.")
        return 0
    print("Мержить нельзя:")
    for reason in verdict.reasons:
        print(f"  — {reason}")
    print("\nОпрашивать не чаще раза в 45-60 с: частый поллинг выжигает квоту GitHub.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
