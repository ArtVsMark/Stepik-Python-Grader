#!/usr/bin/env python3
"""scripts/link_rules_to_issues.py — задача знает, что породила правило.

Связь между задачей и правилом каталога сейчас **односторонняя**: у правила есть
раздел «След» с номером нашей задачи, а у задачи о правиле нет ни слова. Читатель
задачи не узнает, что из неё вышло правило, — и следующий разбор того же места
начинается с нуля, потому что вывод прошлого разбора лежит в другом репозитории.

Скрипт достраивает вторую сторону:

* **метка** :data:`DEFAULT_LABEL` — видна в списке задач, то есть отвечает на
  вопрос «какие наши задачи дали правила» без чтения тел;
* **один комментарий** с номерами правил и ссылками — не новый на каждый прогон,
  а тот же самый, обновляемый по месту. Идемпотентность держится **скрытым
  маркером** :data:`MARKER` в теле, а не совпадением текста: текст меняется,
  когда правил становится больше, и сравнение по нему плодило бы дубли.

Источник — машинный экспорт каталога (``export/rules.json``), а не разбор
Markdown: у экспорта есть поле ``trails`` с репозиторием и номером задачи, и это
контракт, объявленный самим каталогом.

**Пишет только с** ``--apply``. Без него печатает план: какой задаче какая метка
и какой комментарий достанется. Умолчание сухое намеренно — скрипт пишет в чужой
трекер, и «случайно запустил» не должно означать «прошёлся по тридцати задачам».

Коды возврата: ``0`` — сделано (или нечего делать), ``1`` — часть задач не
обновилась, ``2`` — прочитать нечем (нет каталога, нет токена, кончилась квота).

Запуск::

    python scripts/link_rules_to_issues.py --catalogue /tmp/playbook          # план
    python scripts/link_rules_to_issues.py --catalogue /tmp/playbook --apply  # запись
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Импорт после правки sys.path: `scripts/` не пакет, а связь с трекером идёт тем
# же транспортом, что и остальной конвейер.
import contextlib

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "DEFAULT_LABEL",
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "MARKER",
    "backlinks",
    "comment_body",
    "main",
]

EXIT_OK = 0
EXIT_FAIL = 1
#: Прочитать состояние нечем — не то же самое, что «состояние плохое».
EXIT_UNKNOWN = 2

#: Метка ставится на задачу-источник. По-русски, как и прочие смысловые подписи:
#: `area/*` и `difficulty/*` — идентификаторы, а эта метка — текст для читателя.
DEFAULT_LABEL = "породило правило"

_LABEL_COLOR = "5319e7"
_LABEL_DESCRIPTION = "из этой задачи вышло правило каталога Engineering-Incidents-Playbook"

#: Скрытый маркер: по нему комментарий узнаётся при следующем прогоне. Сравнивать
#: по тексту нельзя — он меняется, когда правил становится больше.
MARKER = "<!-- rules-backlink -->"

_CATALOGUE_URL = "https://github.com/ArtVsMark/Engineering-Incidents-Playbook"


def backlinks(export: dict[str, Any], repo: str) -> dict[int, list[dict[str, str]]]:
    """Какие правила ссылаются на какую задачу этого репозитория.

    Args:
        export: содержимое ``export/rules.json`` каталога.
        repo: ``OWNER/NAME`` нашего репозитория.

    Returns:
        ``{номер задачи: [{id, slug, title}, …]}``, правила внутри — по номеру.
    """
    found: dict[int, list[dict[str, str]]] = {}
    for rule in export.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        for trail in rule.get("trails") or []:
            if not isinstance(trail, dict) or trail.get("repo") != repo:
                continue
            raw = str(trail.get("issue") or "")
            if not raw.isdigit():
                continue
            title = rule.get("title")
            found.setdefault(int(raw), []).append(
                {
                    "id": str(rule.get("id", "")),
                    "slug": str(rule.get("slug", "")),
                    "title": str((title or {}).get("ru") or (title or {}).get("en") or ""),
                }
            )
    for rules in found.values():
        rules.sort(key=lambda item: item["id"])
    return found


def comment_body(rules: list[dict[str, str]]) -> str:
    """Собрать тело идемпотентного комментария.

    Args:
        rules: правила, чей след ведёт на эту задачу.

    Returns:
        Текст комментария вместе со скрытым маркером :data:`MARKER`.
    """
    word = "правило" if len(rules) == 1 else "правила"
    lines = [
        MARKER,
        f"**Эта задача породила {word} каталога** "
        f"[Engineering-Incidents-Playbook]({_CATALOGUE_URL}):",
        "",
    ]
    for rule in rules:
        link = f"{_CATALOGUE_URL}/blob/main/rules/ru/{rule['id']}-{rule['slug']}.md"
        lines.append(f"- [{rule['id']}]({link}) — {rule['title']}")
    lines += [
        "",
        "Связь односторонней быть не должна: у правила след ведёт сюда, и теперь "
        "отсюда — на правило. Комментарий один и обновляется по месту, нового на "
        "каждый прогон не будет.",
        "",
        "---",
        "_Generated by [Claude Code](https://claude.ai/code)_",
    ]
    return "\n".join(lines)


def _existing_comment(repo: str, number: int) -> dict[str, Any] | None:
    """Найти ранее оставленный комментарий по скрытому маркеру."""
    for comment in gh_rest.issue_comments(repo, number):
        if MARKER in str(comment.get("body") or ""):
            return comment
    return None


def _ensure_label(repo: str, label: str) -> None:
    """Завести метку, если её ещё нет; существующую не трогать."""
    try:
        gh_rest.request("GET", f"repos/{repo}/labels/{label.replace(' ', '%20')}")
    except gh_rest.GitHubError:
        gh_rest.request(
            "POST",
            f"repos/{repo}/labels",
            body={"name": label, "color": _LABEL_COLOR, "description": _LABEL_DESCRIPTION},
        )


def _sync_issue(repo: str, number: int, rules: list[dict[str, str]], label: str) -> str:
    """Привести одну задачу в соответствие; вернуть, что сделано."""
    body = comment_body(rules)
    existing = _existing_comment(repo, number)
    if existing is None:
        gh_rest.comment_issue(repo, number, body)
        action = "комментарий добавлен"
    elif str(existing.get("body") or "").strip() == body.strip():
        action = "комментарий уже верен"
    else:
        gh_rest.request(
            "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", body={"body": body}
        )
        action = "комментарий обновлён"

    labels = gh_rest.add_labels(repo, number, [label])
    if label not in labels:
        return f"{action}; метку поставить не удалось"
    return action


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalogue", type=pathlib.Path, required=True, help="клон каталога")
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--apply", action="store_true", help="писать в трекер, а не печатать план")
    args = parser.parse_args(argv)

    export = args.catalogue / "export" / "rules.json"
    if not export.exists():
        print(f"{export}: экспорта каталога нет — клонируйте {_CATALOGUE_URL}", file=sys.stderr)
        return EXIT_UNKNOWN
    try:
        data = json.loads(export.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{export}: не разбирается ({exc})", file=sys.stderr)
        return EXIT_UNKNOWN

    found = backlinks(data, args.repo)
    if not found:
        print(f"Правил со следом на {args.repo} нет — связывать нечего.")
        return EXIT_OK

    total = sum(len(rules) for rules in found.values())
    print(f"Задач со следом правила: {len(found)}; правил на них: {total}.")

    if not args.apply:
        for number, rules in sorted(found.items()):
            ids = ", ".join(rule["id"] for rule in rules)
            print(f"  #{number} ← {ids}")
        print("\nЭто план. Записать: тот же вызов с --apply.")
        return EXIT_OK

    try:
        _ensure_label(args.repo, args.label)
    except gh_rest.RateLimited as exc:
        print(f"Квота GitHub исчерпана: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    except gh_rest.GitHubError as exc:
        print(f"Метку {args.label!r} завести не удалось: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN

    failed = 0
    for number, rules in sorted(found.items()):
        try:
            action = _sync_issue(args.repo, number, rules, args.label)
        except gh_rest.RateLimited as exc:
            print(f"Квота GitHub исчерпана на #{number}: {exc}", file=sys.stderr)
            return EXIT_UNKNOWN
        except gh_rest.GitHubError as exc:
            failed += 1
            print(f"  #{number}: НЕ обновлена — {exc}", file=sys.stderr)
            continue
        print(f"  #{number}: {action}")

    if failed:
        print(f"\nНе обновлено задач: {failed}.")
        return EXIT_FAIL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
