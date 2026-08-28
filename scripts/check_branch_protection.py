#!/usr/bin/env python3
"""scripts/check_branch_protection.py — защита ``main`` не ослаблена молча.

Витрина профиля (``ArtVsMark/ArtVsMark``) утверждает публично: **список обходов
защиты ветки пуст**. Это самый сильный сигнал на странице — редкая вещь,
говорящая, что правило действует и на владельца репозитория. Цифра там
статическая: витрина читает чужой репозиторий штатным ``GITHUB_TOKEN``, а
ruleset отдаётся только правам администратора. Значит следить за утверждением
должен тот, у кого права есть, — сам грейдер.

**Почему не «просто помнить».** Список обходов пополняется одним кликом в
настройках — например, чтобы разово продавить срочный фикс, — и возвращается
обратно по памяти. Ни один прогон этого не заметит: CI зелёный, PR мержатся, а
заявленная гарантия тихо перестала существовать. Публичное утверждение о
качестве, которое ничем не проверяется, — обещание, а не механика.

Что сверяется с ruleset ветки ``main``:

1. **Список обходов пуст** (``bypass_actors``). Непустой означает, что правило
   действует не на всех, — то самое утверждение витрины, но наоборот.
2. **Набор обязательных проверок совпадает** с :data:`EXPECTED_CHECKS`
   **дословно**. Имена джобов матрицы входят в ruleset строкой, поэтому
   переименование комбинации в ``ci.yml`` оставляет PR ждать проверку, которой
   больше нет, — расхождение здесь и ловится.
3. **Ветка обязана быть свежей** (``strict_required_status_checks_policy``):
   без этого мержится состояние, которого после слияния не будет.
4. **Удаление и force-push запрещены** — правила ``deletion`` и
   ``non_fast_forward``.
5. **Ruleset активен** (``enforcement: active``): выключенный набор правил
   отдаётся API целиком и снаружи неотличим от работающего.

Ожидаемые значения живут **константами в этом файле**, а не читаются из
настроек: их изменение обязано пройти через ревью PR, а не через веб-интерфейс
молча. В этом весь смысл проверки — сверять состояние с заявленным, а не
с самим собой.

Коды возврата:

* ``0`` — состояние совпало с заявленным;
* ``1`` — расхождение: защита ослаблена или разошлась с ``ci.yml``;
* ``2`` — **проверить не удалось**: нет токена или у него нет прав на ruleset.
  Это отдельный исход, а не провал: «не знаем» и «плохо» ведут к разным
  действиям, и выдавать первое за второе — ровно тот дефект, который
  ``check_pip_audit_report.py`` чинил для аудита зависимостей.

Запуск::

    python scripts/check_branch_protection.py [--repo OWNER/NAME] [--json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Импорт после правки sys.path: `scripts/` не пакет, а гейт ходит тем же
# транспортом, что и остальной конвейер (REST, не GraphQL).
import contextlib

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "EXPECTED_CHECKS",
    "PROTECTED_BRANCH",
    "REQUIRED_RULES",
    "check_ci_jobs",
    "check_ruleset",
    "main",
]

EXIT_OK = 0
EXIT_FAIL = 1
#: Прочитать состояние нечем — не то же самое, что «состояние плохое».
EXIT_UNKNOWN = 2

PROTECTED_BRANCH = "main"

#: Одиннадцать обязательных проверок, дословно как в ruleset. Экспериментальные
#: 3.14 сюда НЕ входят намеренно: они под ``continue-on-error`` и блокировать
#: мерж не должны.
EXPECTED_CHECKS: tuple[str, ...] = (
    "docs-guardrails",
    "static",
    "supply-chain",
    "sandbox-linux",
    "e2e",
    "test (ubuntu-latest, 3.12, false)",
    "test (ubuntu-latest, 3.13, false)",
    "test (windows-latest, 3.12, false)",
    "test (windows-latest, 3.13, false)",
    "test (macos-latest, 3.12, false)",
    "test (macos-latest, 3.13, false)",
)

#: Правила, без которых защита декоративна.
REQUIRED_RULES: tuple[str, ...] = ("deletion", "non_fast_forward")

#: Джобы, чьё имя входит в ``EXPECTED_CHECKS`` как есть (без матрицы). Матричные
#: комбинации складываются из имени джоба и значений, поэтому текстовым поиском
#: не проверяются — их расхождение ловит сверка с ruleset выше.
_PLAIN_JOBS: tuple[str, ...] = ("docs-guardrails", "static", "supply-chain", "sandbox-linux", "e2e")

_CI_WORKFLOW = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def check_ruleset(ruleset: dict[str, object]) -> list[str]:
    """Сверить ruleset с заявленным состоянием; вернуть список расхождений.

    Args:
        ruleset: тело ответа ``GET /repos/{repo}/rulesets/{id}``.

    Returns:
        Расхождения по-русски, по одному на строку. Пустой список — совпало.
    """
    problems: list[str] = []

    enforcement = ruleset.get("enforcement")
    if enforcement != "active":
        problems.append(
            f"ruleset не активен (enforcement={enforcement!r}): выключенный набор правил "
            "снаружи неотличим от работающего"
        )

    raw_bypass = ruleset.get("bypass_actors")
    bypass = raw_bypass if isinstance(raw_bypass, list) else []
    if bypass:
        names = ", ".join(
            str(actor.get("actor_type", "?")) if isinstance(actor, dict) else str(actor)
            for actor in bypass
        )
        problems.append(
            f"список обходов НЕ пуст ({len(bypass)}): {names}. "
            "Витрина профиля утверждает обратное — правило действует не на всех"
        )

    rules = ruleset.get("rules")
    rules = rules if isinstance(rules, list) else []
    kinds = {rule.get("type") for rule in rules if isinstance(rule, dict)}
    for kind in REQUIRED_RULES:
        if kind not in kinds:
            problems.append(f"правило {kind!r} отсутствует: защита без него декоративна")

    checks_rule = next(
        (
            rule
            for rule in rules
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks"
        ),
        None,
    )
    if checks_rule is None:
        problems.append("обязательных проверок нет вовсе: мерж не ждёт ни одного прогона")
        return problems

    params = checks_rule.get("parameters")
    params = params if isinstance(params, dict) else {}
    declared = params.get("required_status_checks")
    declared = declared if isinstance(declared, list) else []
    actual = {
        str(item.get("context"))
        for item in declared
        if isinstance(item, dict) and item.get("context")
    }
    expected = set(EXPECTED_CHECKS)

    missing = sorted(expected - actual)
    if missing:
        problems.append(
            f"обязательных проверок не хватает ({len(missing)}): {', '.join(missing)}. "
            "Мерж перестал их ждать"
        )
    extra = sorted(actual - expected)
    if extra:
        problems.append(
            f"обязательных проверок больше заявленного ({len(extra)}): {', '.join(extra)}. "
            "Либо ruleset правили молча, либо устарел этот файл"
        )

    if not params.get("strict_required_status_checks_policy"):
        problems.append(
            "«ветка обязана быть свежей» выключено: смержится состояние, "
            "проверенное на том, чего после слияния не будет"
        )
    return problems


def check_ci_jobs(text: str) -> list[str]:
    """Проверить, что не-матричные обязательные джобы существуют в ``ci.yml``.

    Дешёвый признак дрейфа в обратную сторону: имя джоба переименовали в
    workflow, а в ruleset осталось старое — PR будет ждать проверку, которой
    больше не бывает.

    Args:
        text: содержимое ``.github/workflows/ci.yml``.

    Returns:
        Расхождения по-русски; пустой список — все имена на месте.
    """
    return [
        f"джоб {job!r} объявлен обязательным, но в ci.yml такого имени нет"
        for job in _PLAIN_JOBS
        if f"\n  {job}:" not in text
    ]


def _fetch(repo: str) -> dict[str, object] | None:
    """Прочитать ruleset ветки ``main``; ``None`` — прочитать нечем."""
    rulesets = gh_rest.request("GET", f"/repos/{repo}/rulesets").data
    if not isinstance(rulesets, list):
        return None
    for entry in rulesets:
        if not isinstance(entry, dict) or entry.get("target") != "branch":
            continue
        detailed = gh_rest.request("GET", f"/repos/{repo}/rulesets/{entry['id']}").data
        if isinstance(detailed, dict):
            return detailed
    return None


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    try:
        ruleset = _fetch(args.repo)
    except gh_rest.RateLimited as exc:
        print(f"Квота GitHub исчерпана: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    except gh_rest.GitHubError as exc:
        print(
            f"Прочитать ruleset не удалось: {exc}\n"
            "Нужен токен с правом administration: read — у штатного GITHUB_TOKEN его нет.",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    if ruleset is None:
        print(
            f"У {args.repo} нет ни одного branch-ruleset: защита {PROTECTED_BRANCH} "
            "не настроена вовсе либо недоступна этому токену.",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    problems = check_ruleset(ruleset)
    if _CI_WORKFLOW.exists():
        problems += check_ci_jobs(_CI_WORKFLOW.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False, indent=1))
    elif problems:
        print(f"Защита {PROTECTED_BRANCH} разошлась с заявленным:")
        for problem in problems:
            print(f"  - {problem}")
    else:
        print(
            f"Защита {PROTECTED_BRANCH}: список обходов пуст, "
            f"обязательных проверок {len(EXPECTED_CHECKS)}, ветка обязана быть свежей, "
            "удаление и force-push запрещены."
        )
    return EXIT_FAIL if problems else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
