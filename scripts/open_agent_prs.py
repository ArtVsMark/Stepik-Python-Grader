#!/usr/bin/env python3
"""scripts/open_agent_prs.py — конвейер открывает PR сам (issue #1302).

Последний шаг, где конвейеру был нужен человек. Всё остальное уже
автоматизировано: проверки идут сами, ``merge-queue.yml`` двигает голову
очереди, GitHub мержит по зелёному. А PR открывал владелец руками — и ветка,
зелёная целиком, стояла до тех пор, пока он не нажмёт кнопку.

**Почему PR не может открыть сама сессия.** Прокси облачной агентской сессии
подменяет учётные данные на записи: ``POST /pulls`` проходит, но автором
становится приложение (``claude[bot]``). Такой PR гейт ``check_pr_ready.py`` не
мержит намеренно — squash атрибутирует итоговый коммит **автору pull request**,
и бот навсегда остался бы автором в ``main``, где историю не переписать. Внутри
Actions этого ограничения нет: PR, созданный с PAT владельца, получает автором
человека — тот же секрет ``MERGE_QUEUE_TOKEN``, которым workflow уже двигает
очередь.

**Что делает скрипт.** Для каждой ветки ``agent/**`` без открытого PR: берёт
сообщение её последнего коммита (оно же уедет в squash), делает из него
заголовок и тело, открывает PR и включает авто-мерж. Дальше PR ведут уже
существующие механизмы.

Правила, без которых это опасно:

* **идемпотентность** — ветка с открытым PR пропускается. Скрипт зовётся и по
  пушу, и по расписанию, поэтому «создать второй PR на ту же ветку» — не
  теоретический случай, а гарантированный;
* **ветка без коммитов сверх базы пропускается** — PR из нуля изменений создать
  можно, и он будет вечно висеть красным;
* **трейлеры в тело PR не уезжают** — авторство подставляет харнесс в коммит, а
  в теле PR стоит строка из ``.claude/settings.json``, чтобы источник был один.

Запуск::

    python scripts/open_agent_prs.py                # открыть, что накопилось
    python scripts/open_agent_prs.py --dry-run      # только показать
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_rest

__all__ = [
    "DEFAULT_PREFIX",
    "attribution_line",
    "branch_names",
    "branches_without_pull",
    "describing_message",
    "main",
    "open_for_branch",
    "title_and_body",
]

DEFAULT_PREFIX = "agent/"

# issue #1325: та же метка, что у `merge_when_green.py` — согласие по умолчанию.
_CONSENT_LABEL = "merge-when-green"

# Трейлеры коммита: авторство живёт в самом коммите, в теле PR оно избыточно и
# ломает читаемость. Совпадение по началу строки, регистр не важен.
_TRAILERS = ("co-authored-by:", "claude-session:", "signed-off-by:")

_SETTINGS = Path(__file__).resolve().parent.parent / ".claude" / "settings.json"


def branch_names(repo: str = gh_rest.DEFAULT_REPO, **kwargs: Any) -> list[str]:
    """Имена веток репозитория — одна страница на сотню, дальше нам не нужно."""
    data = gh_rest.request("GET", f"/repos/{repo}/branches?per_page=100", **kwargs).data
    items = data if isinstance(data, list) else []
    return [str(item.get("name", "")) for item in items if isinstance(item, dict)]


def branches_without_pull(
    branches: list[str],
    open_heads: set[str],
    prefix: str = DEFAULT_PREFIX,
) -> list[str]:
    """Ветки с нужным префиксом, у которых открытого PR ещё нет.

    Отдельная функция, потому что это и есть вся логика выбора: остальное —
    сеть. Проверяется на подделанных списках, без обращения к GitHub.
    """
    return sorted(name for name in branches if name.startswith(prefix) and name not in open_heads)


def title_and_body(message: str, attribution: str = "") -> tuple[str, str]:
    """Заголовок и тело PR из сообщения коммита.

    Первая строка — заголовок (она же уедет в squash), остальное — тело без
    трейлеров. ``attribution`` дописывается в конец, если задан.
    """
    lines = message.strip().splitlines()
    title = lines[0].strip() if lines else ""
    kept = [line for line in lines[1:] if not line.strip().lower().startswith(_TRAILERS)]
    body = "\n".join(kept).strip()
    if attribution:
        body = f"{body}\n\n{attribution}".strip()
    return title, body


def attribution_line(settings: Path | None = None) -> str:
    """Строка соавторства для тела PR из ``.claude/settings.json``.

    Источник один: тот же ключ, которым харнесс подписывает PR, открытые
    вручную. Файла нет или ключа нет — пустая строка, а не выдуманный текст.
    """
    path = settings or _SETTINGS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    attribution = data.get("attribution") if isinstance(data, dict) else None
    line = attribution.get("pr") if isinstance(attribution, dict) else None
    return str(line) if isinstance(line, str) else ""


def describing_message(commits: object) -> str:
    """Сообщение коммита, который описывает работу: последний НЕ merge.

    Заголовок PR берётся из коммита, и merge-коммит в вершине ветки испортил бы
    его молча: «merge: подтянуть main» вместо темы работы. Подтягивать базу в
    ветку приходится регулярно — защита требует актуальной ветки, — поэтому
    случай штатный, а не редкий.
    """
    items = commits if isinstance(commits, list) else []
    plain = [
        str(item.get("commit", {}).get("message", ""))
        for item in items
        if isinstance(item, dict) and len(item.get("parents") or []) == 1
    ]
    if plain:
        return plain[-1]
    tail = items[-1] if items else None
    return str(tail.get("commit", {}).get("message", "")) if isinstance(tail, dict) else ""


def open_for_branch(
    branch: str,
    repo: str = gh_rest.DEFAULT_REPO,
    *,
    base: str = "main",
    attribution: str = "",
    dry_run: bool = False,
    **kwargs: Any,
) -> str:
    """Открыть PR для ветки; вернуть строку отчёта о том, что вышло."""
    # Один запрос отвечает на оба вопроса — «есть ли что мержить» и «из чего
    # брать текст PR»: `compare` отдаёт и `ahead_by`, и сами коммиты.
    payload = gh_rest.request("GET", f"/repos/{repo}/compare/{base}...{branch}", **kwargs).data
    data = payload if isinstance(payload, dict) else {}
    if not data.get("ahead_by"):
        return f"{branch}: нет коммитов сверх {base} — пропущена"

    title, body = title_and_body(describing_message(data.get("commits")), attribution)
    if not title:
        return f"{branch}: у последнего коммита пустое сообщение — заголовок брать неоткуда"

    if dry_run:
        return f"{branch}: открыл бы PR «{title}»"

    created = gh_rest.create_pull(repo, title=title, head=branch, base=base, body=body, **kwargs)
    number = int(created.get("number", 0))
    if not number:
        return f"{branch}: GitHub не вернул номер PR — авто-мерж включать нечему"
    # issue #1325: согласие по умолчанию ставится сразу при открытии, а не ждёт
    # обхода по расписанию. Метка здесь не только включатель — она же и след:
    # по ней видно, что PR отдан автоматике, а `hold` этот след снимает.
    with contextlib.suppress(gh_rest.GitHubError):
        gh_rest.add_labels(repo, number, [_CONSENT_LABEL], **kwargs)
    try:
        gh_rest.enable_auto_merge(repo, number, **kwargs)
    except gh_rest.GitHubError as exc:
        return f"{branch}: PR #{number} открыт, но авто-мерж не включился ({exc})"
    return f"{branch}: PR #{number} открыт, авто-мерж включён"


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — сделано или делать нечего; 1 — сеть/квота (то есть «повторить позже»)."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/open_agent_prs.py",
        description="Открыть PR для веток agent/** и включить им авто-мерж.",
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument("--base", default="main", help="базовая ветка PR")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="префикс агентских веток")
    parser.add_argument("--dry-run", action="store_true", help="показать, ничего не создавая")
    args = parser.parse_args(argv)

    try:
        open_heads = {pull.branch for pull in gh_rest.list_pulls(args.repo)}
        pending = branches_without_pull(branch_names(args.repo), open_heads, args.prefix)
    except gh_rest.RateLimited as exc:
        # Квота — «ждать», а не «сломалось»: повтор её не лечит, а отодвигает
        # сброс (правило 058).
        print(f"::warning::квота исчерпана, состояние не прочитано: {exc}", file=sys.stderr)
        return gh_rest.EXIT_WAIT
    except gh_rest.GitHubError as exc:
        # Третий исход (правило 039): «веток без PR нет» и «состояние не
        # прочитано» — разные вещи с разными действиями, и код возврата обязан
        # их различать. Раньше здесь стоял тот же код, что у находки.
        print(f"::warning::не удалось прочитать состояние репозитория: {exc}", file=sys.stderr)
        return 2

    if not pending:
        print(f"Веток {args.prefix}** без открытого PR нет.")
        return 0

    attribution = attribution_line()
    for branch in pending:
        try:
            print(
                open_for_branch(
                    branch,
                    args.repo,
                    base=args.base,
                    attribution=attribution,
                    dry_run=args.dry_run,
                )
            )
        except gh_rest.GitHubError as exc:
            # Одна упавшая ветка не должна уносить остальные: следующий запуск
            # по расписанию попробует её снова.
            print(f"::warning::{branch}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
