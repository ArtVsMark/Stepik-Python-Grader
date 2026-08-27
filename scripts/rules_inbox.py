#!/usr/bin/env python3
"""scripts/rules_inbox.py — нерассмотренные правила каталога видны в трекере.

Каталог правил пополняется чужими сменами, а решение «принимаем ли мы это
правило и чем оно у нас держится» принимает проект. Между появлением правила и
этим решением есть зазор, и он не виден: `.rules/bindings.json` честно пишет
``unreviewed``, но файл в репозитории никто не открывает по расписанию, а гейт
полноты говорит только «ответа нет» — про правило, ответ по которому есть, но
означает «не дошли руки», он молчит.

Скрипт держит **один постоянный issue** — входящие, — в котором:

* правила со статусом ``unreviewed`` из нашего ответа каталогу;
* правила, которых в ответе нет **вовсе** (появились после последней сверки);
* метрика: сколько нерассмотренных и **сколько дней самому старому** — второе
  важнее первого, потому что растёт само и показывает не объём, а запущенность.

Issue **один и тот же**: он ищется по скрытому маркеру :data:`MARKER` в теле, а
не по номеру в файле и не по заголовку. Номер пришлось бы где-то хранить, а
хранимое состояние разъезжается ровно так же, как разъехался бы список задач в
документе; заголовок меняется вместе с числом в нём.

**Пишет только с** ``--apply``: без него печатает, что было бы в теле.

Коды возврата: ``0`` — сделано, ``1`` — записать не удалось, ``2`` — прочитать
нечем (нет каталога, нет токена, кончилась квота).

Запуск::

    python scripts/rules_inbox.py --catalogue /tmp/playbook           # показать
    python scripts/rules_inbox.py --catalogue /tmp/playbook --apply   # обновить
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Импорт после правки sys.path: `scripts/` не пакет, а трекер опрашивается тем
# же транспортом, что и весь конвейер.
import gh_rest

__all__ = [
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "INBOX_LABEL",
    "MARKER",
    "issue_body",
    "issue_title",
    "main",
    "pending_rules",
]

EXIT_OK = 0
EXIT_FAIL = 1
#: Прочитать состояние нечем — не то же самое, что «состояние плохое».
EXIT_UNKNOWN = 2

#: Скрытый маркер: по нему входящие узнаются при следующем прогоне. Ни номер в
#: файле, ни заголовок для этого не годятся — первый пришлось бы хранить, второй
#: меняется вместе с числом в нём.
MARKER = "<!-- rules-inbox -->"

INBOX_LABEL = "входящие правил"
_LABEL_COLOR = "0e8a16"
_LABEL_DESCRIPTION = "постоянный список нерассмотренных правил каталога"

_BINDINGS = pathlib.Path(__file__).parent.parent / ".rules" / "bindings.json"
_CATALOGUE_URL = "https://github.com/ArtVsMark/claude-code-playbook"


def pending_rules(bindings: dict[str, Any], export: dict[str, Any]) -> list[dict[str, str]]:
    """Правила, по которым решения ещё нет.

    Args:
        bindings: содержимое ``.rules/bindings.json`` проекта.
        export: содержимое ``export/rules.json`` каталога.

    Returns:
        Список правил, старые сверху: ``{id, added, title, state}``, где
        ``state`` — ``unreviewed`` (ответ есть, решения нет) либо ``no-answer``
        (правило появилось после последней сверки).
    """
    answered = bindings.get("rules") or {}
    pending: list[dict[str, str]] = []
    for rule in export.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id", ""))
        if not rule_id:
            continue
        answer = answered.get(rule_id)
        if answer is None:
            state = "no-answer"
        elif str(answer.get("status")) == "unreviewed":
            state = "unreviewed"
        else:
            continue
        title = rule.get("title") or {}
        pending.append(
            {
                "id": rule_id,
                "slug": str(rule.get("slug", "")),
                "added": str(rule.get("added") or ""),
                "title": str(title.get("ru") or title.get("en") or ""),
                "state": state,
            }
        )
    pending.sort(key=lambda item: (item["added"] or "9999", item["id"]))
    return pending


def _oldest_age_days(pending: list[dict[str, str]], today: dt.date) -> int | None:
    """Сколько дней самому старому нерассмотренному правилу."""
    dates = []
    for rule in pending:
        try:
            dates.append(dt.date.fromisoformat(rule["added"]))
        except ValueError:
            continue
    return (today - min(dates)).days if dates else None


def issue_title(pending: list[dict[str, str]]) -> str:
    """Заголовок входящих: число видно из списка задач, без открытия."""
    if not pending:
        return "🧭 Входящие каталога правил: разобрано всё"
    return f"🧭 Входящие каталога правил: {len(pending)} нерассмотренных"


def issue_body(pending: list[dict[str, str]], today: dt.date) -> str:
    """Собрать тело постоянного issue.

    Args:
        pending: правила без решения, старые сверху.
        today: дата прогона — параметром, чтобы тело было воспроизводимым.

    Returns:
        Текст со скрытым маркером :data:`MARKER` в первой строке.
    """
    lines = [MARKER, "", f"_Обновляется автоматически. Последний обход: {today.isoformat()}._", ""]
    if not pending:
        lines += [
            "**Нерассмотренных правил нет.** Каждое правило каталога получило ответ:",
            "принято и чем держится, отклонено с причиной или признано неприменимым.",
            "",
            "Issue остаётся открытым намеренно: он постоянный, и следующее правило "
            "каталога появится здесь само.",
        ]
    else:
        age = _oldest_age_days(pending, today)
        age_text = f"{age} дн" if age is not None else "неизвестно"
        lines += [
            f"**Нерассмотренных: {len(pending)}. Самому старому: {age_text}.**",
            "",
            "Возраст важнее числа: он растёт сам и показывает не объём работы, "
            "а запущенность. Решение по правилу — одно из трёх: принять и назвать, "
            "чем оно здесь держится (`gate` · `process-step` · `none`), отклонить с "
            "причиной или признать неприменимым.",
            "",
            "| Правило | В каталоге с | Состояние | О чём |",
            "|---|---|---|---|",
        ]
        state_ru = {
            "unreviewed": "не рассмотрено",
            "no-answer": "**ответа нет вовсе**",
        }
        for rule in pending:
            link = f"{_CATALOGUE_URL}/blob/main/rules/ru/{rule['id']}-{rule['slug']}.md"
            lines.append(
                f"| [{rule['id']}]({link}) | {rule['added'] or '—'} | "
                f"{state_ru[rule['state']]} | {rule['title']} |"
            )
        lines += [
            "",
            "«Ответа нет вовсе» — правило появилось в каталоге после последней сверки; "
            "его отсутствие в `.rules/bindings.json` роняет гейт формата, поэтому такие "
            "строки исчезают отсюда первыми.",
        ]
    lines += [
        "",
        "Ответ проекта живёт в [`.rules/bindings.json`](.rules/bindings.json), "
        f"каталог — [claude-code-playbook]({_CATALOGUE_URL}).",
        "",
        "---",
        "_Generated by [Claude Code](https://claude.ai/code)_",
    ]
    return "\n".join(lines)


def _find_inbox(repo: str) -> dict[str, Any] | None:
    """Найти постоянный issue по скрытому маркеру среди открытых."""
    page = 1
    while page <= 5:
        data = gh_rest.request(
            "GET", f"repos/{repo}/issues?state=open&per_page=100&page={page}"
        ).data
        if not isinstance(data, list) or not data:
            return None
        for item in data:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            if MARKER in str(item.get("body") or ""):
                return item
        if len(data) < 100:
            return None
        page += 1
    return None


def _ensure_label(repo: str, label: str) -> None:
    """Завести метку входящих, если её ещё нет."""
    try:
        gh_rest.request("GET", f"repos/{repo}/labels/{label.replace(' ', '%20')}")
    except gh_rest.GitHubError:
        gh_rest.request(
            "POST",
            f"repos/{repo}/labels",
            body={"name": label, "color": _LABEL_COLOR, "description": _LABEL_DESCRIPTION},
        )


def _read_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalogue", type=pathlib.Path, required=True, help="клон каталога")
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--apply", action="store_true", help="писать в трекер, а не печатать")
    args = parser.parse_args(argv)

    bindings = _read_json(_BINDINGS)
    if bindings is None:
        print(f"{_BINDINGS}: ответа каталогу нет или он не разбирается", file=sys.stderr)
        return EXIT_UNKNOWN
    export = _read_json(args.catalogue / "export" / "rules.json")
    if export is None:
        print(
            f"{args.catalogue}: экспорта каталога нет — клонируйте {_CATALOGUE_URL}",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    today = dt.date.today()
    pending = pending_rules(bindings, export)
    title = issue_title(pending)
    body = issue_body(pending, today)

    age = _oldest_age_days(pending, today)
    print(f"{title} (самому старому: {age if age is not None else '—'} дн.)")

    if not args.apply:
        print("\n--- тело входящих ---")
        print(body)
        print("--- конец ---\nЭто показ. Записать: тот же вызов с --apply.")
        return EXIT_OK

    try:
        _ensure_label(args.repo, INBOX_LABEL)
        existing = _find_inbox(args.repo)
        if existing is None:
            created = gh_rest.request(
                "POST",
                f"repos/{args.repo}/issues",
                body={"title": title, "body": body, "labels": [INBOX_LABEL]},
            ).data
            number = created.get("number") if isinstance(created, dict) else "?"
            print(f"Входящие заведены: #{number}")
            return EXIT_OK
        number = existing.get("number")
        if str(existing.get("body") or "").strip() == body.strip():
            print(f"Входящие #{number}: без изменений.")
            return EXIT_OK
        gh_rest.request(
            "PATCH", f"repos/{args.repo}/issues/{number}", body={"title": title, "body": body}
        )
        print(f"Входящие #{number}: обновлены.")
    except gh_rest.RateLimited as exc:
        print(f"Квота GitHub исчерпана: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    except gh_rest.GitHubError as exc:
        print(f"Записать не удалось: {exc}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
