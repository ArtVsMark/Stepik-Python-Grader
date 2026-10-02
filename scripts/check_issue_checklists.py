#!/usr/bin/env python3
"""scripts/check_issue_checklists.py — комплексный issue показывает своё состояние.

Комплексный issue — тот, что несёт не одну задачу, а список находок: эпик,
подэпик, хвост аудита. Такой список легко написать скопом («семь находок,
разбирайтесь»), и тогда **состояние каждой строки не видно нигде**: закрыта ли
третья находка, отклонена ли пятая, осталась ли работа вообще — читатель узнаёт
это, только перечитав весь тред и сверив с кодом.

Прецедент, из которого выросла проверка: подэпик #987 перечислял семь
вернувшихся дефектов таблицей. Пять были закрыты, но узнать это удалось лишь
ревизией по коду через полторы недели; параллельно документ аудита вёл
собственный реестр закрытых находок, который отстал и начал числить закрытое
открытым. Два источника состояния — гарантированно один устаревший.

Проверяется каждый ОТКРЫТЫЙ issue, помеченный как комплексный (метки ``epic`` /
``audit`` или заголовок с «[Эпик]», «[Подэпик]», «[Хвост]»):

1. **Находки перечислены чек-листом**, а не абзацем или голой таблицей —
   ``- [ ]`` / ``- [x]`` на каждую. GitHub считает по ним прогресс, и он виден
   прямо в списке issue.
2. **Отмеченная строка называет исход** — номер PR (``#1182``) или явное
   «отклонено»/«дубликат» с причиной. Галочка без исхода — утверждение «сделано»
   без доказательства, ровно то, из-за чего пришлось перепроверять по коду.

**Проверка НЕ роняет CI** (кроме сетевого сбоя): это состояние трекера, а не
дефект кода — падение блокировало бы посторонние PR из-за незаполненного тела
чужого issue. Сигнал — ``::warning::`` в логе, как у
``check_good_first_issues_bilingual.py``, чей запрос здесь и переиспользуется.

Сеть — только stdlib ``urllib``; токен берётся из ``GITHUB_TOKEN`` (в CI он есть
по умолчанию). Без токена запрос уходит всё равно, просто лимит запросов ниже.

Запуск::

    python scripts/check_issue_checklists.py [--repo OWNER/NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "DEFAULT_REPO",
    "EXIT_BROKEN",
    "EXIT_FINDING",
    "EXIT_OK",
    "MIN_ITEMS_FOR_CHECKLIST",
    "ChecklistItem",
    "bulk_listed_ids",
    "check_issue",
    "checklist_items",
    "fetch_open_issues",
    "is_complex",
    "main",
]

DEFAULT_REPO = "ArtVsMark/Stepik-Python-Grader"

# Сколько находок делают issue комплексным. Две задачи в теле ещё читаются
# глазами; с третьей список уже нужно вести, а не пересказывать.
MIN_ITEMS_FOR_CHECKLIST = 3

#: Исходы читает ночной обход по коду возврата: 0 — чисто, 1 — находка,
#: 2 — проверка не отработала. Раньше находки здесь возвращали 0 и уезжали в
#: `::warning::`, который читает только тот, кто открыл лог прогона; отказ
#: чтения, наоборот, возвращал 1 и приезжал в задачу как находка. Коды были
#: перевёрнуты, и проверка молчала о настоящем.
#:
#: «Предупреждение, а не отказ» кодом 0 не выражается: обход и так не краснеет
#: на находке — он пишет её в задачу-адресата, а красным делает только третий
#: исход.
EXIT_OK = 0
EXIT_FINDING = 1
EXIT_BROKEN = 2


_API = "https://api.github.com/search/issues"

# Метки и заголовки, по которым issue считается комплексным. Заголовок нужен
# наравне с меткой: эпики проекта помечаются эмодзи-префиксом в названии, и
# метка иногда отстаёт от факта.
_COMPLEX_LABELS = frozenset({"epic", "audit"})
_COMPLEX_TITLE_RE = re.compile(r"\[(эпик|подэпик|хвост)\]", re.IGNORECASE)

_CHECKLIST_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$", re.MULTILINE)

# ID находки аудита: ЗОНА-ЦИФРА-ЦИФРА (RUN-4-02) либо ЗОНА-ЦИФРА (ADR-12).
# Именно ими находки перечисляются «скопом» в таблицах.
_FINDING_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7}(?:-\d{1,2}){1,2})\b")

# Исход отмеченной строки: ссылка на PR или явное отклонение.
_PR_REF_RE = re.compile(r"#\d{1,6}")
_REJECTED_RE = re.compile(r"отклон|дубликат|не воспроизвод|refuted|duplicate", re.IGNORECASE)


@dataclass(frozen=True)
class ChecklistItem:
    """Одна строка чек-листа: отмечена ли и что написано после галочки."""

    done: bool
    text: str


def fetch_open_issues(
    repo: str = DEFAULT_REPO,
    *,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> list[tuple[int, str, list[str], str]]:
    """Открытые issue четвёрками «номер, заголовок, метки, тело».

    Разбор ответа заканчивается здесь: дальше работают готовые четвёрки, и
    проверка не зависит от формы ответа GitHub. ``opener`` подменяется в
    тестах — сеть в наборе тестов не нужна и делала бы его флаки.

    Raises:
        urllib.error.URLError: сеть недоступна или GitHub ответил ошибкой.
    """
    query = f"repo:{repo} is:issue is:open"
    url = f"{_API}?{urllib.parse.urlencode({'q': query, 'per_page': 100})}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    open_url = opener or urllib.request.urlopen
    with open_url(request) as response:  # type: ignore[union-attr]
        payload = json.loads(response.read().decode("utf-8"))
    found: list[tuple[int, str, list[str], str]] = []
    for item in payload.get("items", []):
        number = item.get("number")
        if not isinstance(number, int):
            continue
        labels = [str(label.get("name", "")) for label in item.get("labels", [])]
        found.append((number, str(item.get("title") or ""), labels, str(item.get("body") or "")))
    return found


def is_complex(title: str, labels: list[str]) -> bool:
    """Комплексный ли issue — по метке ``epic``/``audit`` или префиксу заголовка."""
    if _COMPLEX_LABELS & {label.lower() for label in labels}:
        return True
    return bool(_COMPLEX_TITLE_RE.search(title or ""))


def checklist_items(body: str) -> list[ChecklistItem]:
    """Строки чек-листа из тела issue (пустой список — чек-листа нет)."""
    return [
        ChecklistItem(done=mark.lower() == "x", text=text.strip())
        for mark, text in _CHECKLIST_RE.findall(body or "")
    ]


def bulk_listed_ids(body: str) -> list[str]:
    """ID находок, перечисленных ВНЕ чек-листа — «скопом».

    Считаются строки таблиц и обычных списков: именно так находки и попадают в
    тело агрегата после аудита. Строки чек-листа исключаются — они и есть
    искомая форма.
    """
    found: list[str] = []
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") or stripped.startswith(("-", "*"))):
            continue
        if _CHECKLIST_RE.match(line):
            continue
        found.extend(_FINDING_ID_RE.findall(stripped))
    # Порядок сохраняем, повторы убираем: один ID часто встречается и в таблице,
    # и в тексте рядом.
    seen: dict[str, None] = {}
    for item in found:
        seen.setdefault(item, None)
    return list(seen)


def check_issue(number: int, title: str, labels: list[str], body: str) -> str | None:
    """Что не так с чек-листом комплексного issue; ``None`` — всё в порядке."""
    if not is_complex(title, labels):
        return None
    items = checklist_items(body)
    if not items:
        listed = bulk_listed_ids(body)
        if len(listed) < MIN_ITEMS_FOR_CHECKLIST:
            return None
        sample = ", ".join(listed[:5])
        return (
            f"#{number}: {len(listed)} находок перечислены скопом, чек-листа нет "
            f"({sample}…). Состояние каждой строки не видно ни в списке issue, ни "
            "в теле — сделайте их пунктами `- [ ]`."
        )
    silent = [
        item.text
        for item in items
        if item.done and not (_PR_REF_RE.search(item.text) or _REJECTED_RE.search(item.text))
    ]
    if silent:
        sample = silent[0][:70]
        return (
            f"#{number}: {len(silent)} отмеченн(ая/ых) строк(а/и) без исхода — «{sample}…». "
            "Галочка обязана называть номер PR или причину отклонения: иначе «сделано» "
            "приходится перепроверять по коду."
        )
    return None


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли.

    Сообщения русские, а консоль Windows по умолчанию cp1251/cp1252: без этого
    падает даже ``--help`` — ``UnicodeEncodeError`` вместо текста. Тот же приём,
    что в ``check_good_first_issues_bilingual.py``. No-op на потоках без
    ``reconfigure`` — например, перехваченных pytest.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0 всегда, кроме сетевого сбоя: состояние трекера — предупреждение."""
    # ДО разбора аргументов: справку печатает argparse, он же завершает процесс —
    # после ``parse_args()`` переключать кодировку уже некому.
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/check_issue_checklists.py",
        description="Проверка, что комплексные issue ведут чек-лист находок.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name репозитория")
    args = parser.parse_args(argv)

    try:
        issues = fetch_open_issues(args.repo)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"::warning::не удалось получить список issue: {exc}", file=sys.stderr)
        return EXIT_BROKEN

    complex_issues = [item for item in issues if is_complex(item[1], item[2])]
    problems = [
        problem
        for number, title, labels, body in complex_issues
        if (problem := check_issue(number, title, labels, body))
    ]
    print(f"комплексные issue: проверено {len(complex_issues)} из {len(issues)} открытых.")
    for problem in problems:
        print(f"::warning::{problem}")
    if not problems:
        print("OK: у каждого комплексного issue есть чек-лист, у каждой галочки — исход.")
        return EXIT_OK
    return EXIT_FINDING


if __name__ == "__main__":
    raise SystemExit(main())
