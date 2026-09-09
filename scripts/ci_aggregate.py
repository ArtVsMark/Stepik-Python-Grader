#!/usr/bin/env python3
"""scripts/ci_aggregate.py — одна обязательная проверка вместо одиннадцати (issue #1420).

Правило каталога 168: **обязательная проверка одна, имя её постоянно, и собрана
она не через** ``needs:``.

Сегодня обязательных одиннадцать, и шесть из них — имена ячеек матрицы вида
``test (ubuntu-latest, 3.12, false)``. Имя обязательной проверки держит внешняя
настройка репозитория: её не видит ревью, не ломает переименование и не
проверяет ни один прогон. Разъезд двух сторон производит не красное, а
**ожидание**, а ожидание неотличимо от «проверки ещё идут». Класс поломки —
самоблокировка: условие достижимо только после слияния, то есть чинится снятием
защиты.

**Почему не** ``needs:``. Джоб, собранный через зависимости, при падении соседа
не краснеет, а **пропускается** — и защита ветки засчитывает пропущенное как
пройденное. То есть агрегатор на ``needs:`` разрешает слияние ровно тогда, когда
обязан запретить. Поэтому этот вердикт читает **проверки на голове изменения**
через API и доходит до ответа при любом исходе соседей.

**Эталон выводится из дерева, а не хранится копией** (правило 171): состав ждём
тот, что объявлен в ``ci.yml``, — имена ячеек матрицы собирает
``check_branch_protection.matrix_checks``. Копия в константе позволила бы
переименовать ячейку и остаться «согласной» с настройкой, разойдясь с работой.

**Пустой список — это «прогон не стартовал», а не «зелено».** Отсутствующее имя
из эталона держит ожидание до дедлайна и затем становится отказом с именем того,
чего не дождались.

Три исхода (правило 039): 0 — все обязательные завершились успехом; 1 —
хотя бы одна не прошла либо не появилась к дедлайну; 2 — спросить площадку не
удалось (нет токена, нет прав, квота).

Запуск (в CI аргументы берутся из окружения)::

    python scripts/ci_aggregate.py
    python scripts/ci_aggregate.py --sha <sha> --repo owner/name --once
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import sys
import time
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import check_branch_protection  # noqa: E402 — путь к соседу добавлен строкой выше
import ci_matrix  # noqa: E402
import gh_rest  # noqa: E402

__all__ = [
    "AGGREGATE_NAME",
    "CI_WORKFLOW",
    "DEADLINE_SECONDS",
    "OK_CONCLUSIONS",
    "POLL_SECONDS",
    "expected_checks",
    "main",
    "verdict",
]

_ROOT = pathlib.Path(__file__).parent.parent

CI_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"

#: Постоянное имя. Оно и есть весь смысл: единственная строка, которую держит
#: внешняя настройка, не должна меняться никогда — ни при переименовании джоба,
#: ни при добавлении ОС в матрицу.
AGGREGATE_NAME = "ci-complete"

#: Исходы, которые слияние НЕ держат. Список закрытый и перечисляет только
#: хорошее: всё остальное — отказ, потому что «не знаю» и «всё хорошо» разные
#: вещи, а перечислять плохое означало бы пропускать неизвестное.
#:
#: ``skipped`` здесь намеренно: джоб может быть законно пропущен своим ``if:``,
#: и требовать от него успеха значило бы краснеть на верном ответе.
#: ``neutral`` — то же самое со стороны приложений.
OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

#: Сколько ждать. Матрица из трёх ОС идёт около десяти минут, ``e2e`` дольше;
#: сорок пять минут — запас на очередь исполнителей, а не на зависший джоб.
DEADLINE_SECONDS = 45 * 60

#: Интервал опроса. Реже, чем кажется нужным: на PR идут два-три прогона разом,
#: и частый опрос выжигает общий лимит токена быстрее, чем приносит пользу.
POLL_SECONDS = 45


def expected_checks(text: str) -> set[str]:
    """Обязательный состав, выведенный из ``ci.yml``.

    Args:
        text: содержимое ``ci.yml``.

    Returns:
        Имена джобов и стабильных комбинаций матрицы.

    Экспериментальные комбинации (``continue-on-error``, оканчиваются на
    ``, true)``) в состав не входят: они не должны держать слияние — это уже
    объявлено в ``check_branch_protection`` и повторяется здесь той же меркой.
    """
    matrix = set(ci_matrix.blocking_names(ci_matrix.matrix_names(text)))
    return set(check_branch_protection.PLAIN_JOBS) | matrix


def verdict(runs: list[dict[str, Any]], expected: set[str]) -> tuple[str, list[str]]:
    """Что решать по проверкам на голове изменения.

    Args:
        runs: check-run'ы головы (``name``, ``status``, ``conclusion``).
        expected: обязательный состав.

    Returns:
        Пара ``(исход, строки объяснения)``, где исход — ``ok`` (всё зелено),
        ``fail`` (есть отказ) или ``wait`` (состав ещё не полон).

    Отказ побеждает ожидание: дожидаться остальных, когда одна уже упала,
    значит держать очередь ради вердикта, который уже известен.
    """
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = str(run.get("name") or "")
        if name in expected:
            # Проверка могла быть перезапущена: последняя запись и есть текущая.
            latest[name] = run

    failed = [
        f"{name}: {run.get('conclusion')}"
        for name, run in sorted(latest.items())
        if str(run.get("status")) == "completed"
        and str(run.get("conclusion")) not in OK_CONCLUSIONS
    ]
    if failed:
        return "fail", ["обязательная проверка не прошла:", *(f"  - {line}" for line in failed)]

    pending = sorted(name for name, run in latest.items() if str(run.get("status")) != "completed")
    missing = sorted(expected - set(latest))
    if pending or missing:
        lines = []
        if pending:
            lines.append("ещё идут: " + ", ".join(pending))
        if missing:
            # Ровно та ловушка, ради которой агрегатор и нужен: пустой список
            # проверок читается как «нет красных», хотя означает «не стартовало».
            lines.append("ещё не появились: " + ", ".join(missing))
        return "wait", lines

    return "ok", [f"все обязательные завершились успехом: {len(expected)}"]


def _head_sha(env: dict[str, str]) -> str:
    """Голова изменения: у ``pull_request`` это НЕ ``GITHUB_SHA``.

    ``GITHUB_SHA`` на pull_request указывает на merge-коммит, которого нет ни у
    одной проверки: check-run'ы висят на голове ветки. Спутать их — значит
    вечно ждать состав, который никогда не появится.
    """
    return env.get("PR_HEAD_SHA") or env.get("GITHUB_SHA") or ""


def main(argv: list[str] | None = None) -> int:
    """0 — всё зелено, 1 — отказ или не дождались, 2 — спросить не удалось."""
    parser = argparse.ArgumentParser(
        prog="python scripts/ci_aggregate.py",
        description="Один вердикт по проверкам на голове изменения.",
    )
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY") or gh_rest.DEFAULT_REPO
    )
    parser.add_argument("--sha", default=_head_sha(dict(os.environ)))
    parser.add_argument("--once", action="store_true", help="один опрос без ожидания")
    parser.add_argument(
        "--deadline", type=int, default=DEADLINE_SECONDS, help="сколько ждать, секунд"
    )
    args = parser.parse_args(argv)

    if not args.sha:
        print(
            "не отработала: голова изменения неизвестна (нет PR_HEAD_SHA/GITHUB_SHA)",
            file=sys.stderr,
        )
        return 2
    try:
        expected = expected_checks(CI_WORKFLOW.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"не отработала: {CI_WORKFLOW.name} не прочитан — {error}", file=sys.stderr)
        return 2
    if not expected:
        print("не отработала: состав из ci.yml не выведен — сверять нечего", file=sys.stderr)
        return 2

    print(f"Голова {args.sha[:12]}, обязательных проверок ожидается {len(expected)}.")

    started = time.monotonic()
    while True:
        try:
            payload = gh_rest.pull_checks(args.repo, args.sha)
        except gh_rest.RateLimited as error:
            print(f"не отработала: квота исчерпана — {error}", file=sys.stderr)
            return gh_rest.EXIT_WAIT
        except gh_rest.GitHubError as error:
            print(f"не отработала: {error}", file=sys.stderr)
            return 2

        state, lines = verdict(list(payload.get("check_runs") or []), expected)
        for line in lines:
            print(line)
        if state == "ok":
            return 0
        if state == "fail":
            return 1
        if args.once or time.monotonic() - started > args.deadline:
            print(
                "\nFAIL: состав так и не собрался. Пустой список проверок — это "
                "«прогон не стартовал», а не «зелено»: молчание засчитывать нельзя.",
                file=sys.stderr,
            )
            return 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
