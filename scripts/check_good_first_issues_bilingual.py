#!/usr/bin/env python3
"""scripts/check_good_first_issues_bilingual.py — пул для внешних на двух языках.

У проекта есть намеренная англоязычная витрина (``README.en.md``, локаль ``en``
в CLI и вебе, двуязычные подписи в issue-формах), и метки ``good first issue`` и
``help wanted`` — те входы, на которые она приводит. Задача, написанная только
по-русски, отсекает ровно ту аудиторию, ради которой метка существует:
англоязычный читатель доходит до списка задач и упирается в стену текста,
которую не может прочесть.

Меток две, а не одна, потому что путь внешнего участника на первом вкладе не
кончается: сделавший ``good first issue`` идёт за вторым — и упирается в
``help wanted``, где английской половины прежде не требовалось. Барьер тогда
просто сдвигался на шаг вперёд.

Проверяется каждый ОТКРЫТЫЙ issue с любой из меток:

1. **Английская половина есть** — тело содержит якорь ``## In English``
   (или ``## English``).
2. **Она не пустая** — под якорем не меньше ``MIN_TRANSLATION_CHARS`` символов
   осмысленного текста. Пустой заголовок «переводом» не считается: он проходил
   бы проверку, ничего не давая читателю.
3. **Она действительно английская** — в ней нет русских слов вне кода. Иначе
   якорь поставили, а абзац скопировали как есть — и это ровно то состояние,
   которое проверка обязана отличать от перевода.

**Проверка НЕ роняет CI** (кроме сетевого сбоя): состояние трекера — не дефект
кода, и падение блокировало бы посторонние PR из-за не дописанного владельцем
перевода. Сигнал — ``::warning::`` в логе, как у бейджа пула задач
(``generate_good_first_issues_badge.py``, issue #835), чей запрос здесь и
переиспользуется.

Сеть — только stdlib ``urllib``; токен берётся из ``GITHUB_TOKEN`` (в CI он есть
по умолчанию). Без токена запрос уходит всё равно: публичный поиск issue не
требует авторизации, просто лимит запросов ниже.

Запуск::

    python scripts/check_good_first_issues_bilingual.py [--repo OWNER/NAME]
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

__all__ = [
    "DEFAULT_LABEL",
    "DEFAULT_LABELS",
    "DEFAULT_REPO",
    "MAX_RUSSIAN_WORDS",
    "MIN_TRANSLATION_CHARS",
    "check_issue",
    "english_part",
    "fetch_open_issues",
    "main",
    "russian_words",
]

DEFAULT_REPO = "ArtVsMark/Stepik-Python-Grader"

# Метки пула для внешних участников — обе двуязычны. Порядок значим только для
# вывода; ``DEFAULT_LABEL`` остаётся первой из них, потому что на неё смотрит
# бейдж пула задач и одноимённый аргумент ``fetch_open_issues``.
DEFAULT_LABELS = ("good first issue", "help wanted")

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

DEFAULT_LABEL = DEFAULT_LABELS[0]

# Ниже этого перевод — не перевод, а заголовок с парой слов под ним. Порог
# намеренно низкий: короткая задача бывает и правда короткой, а ловим мы
# «якорь поставили, текст не написали».
MIN_TRANSLATION_CHARS = 120

# Сколько русских слов в английской половине терпим. Ноль был бы неверен:
# перевод законно цитирует русские имена разделов («Подучить»), названия меток
# локали и куски вывода — а вот целый непереведённый абзац столько не даёт.
MAX_RUSSIAN_WORDS = 5

_API = "https://api.github.com/search/issues"

# Якорь английской половины. Оба варианта равноправны — важно, что он один на
# строку-заголовок и его видно глазами в теле issue.
_ANCHOR_RE = re.compile(r"^#{1,3}\s+(?:in\s+english|english)\s*$", re.IGNORECASE | re.MULTILINE)

# Блоки кода и inline-код: там русские слова законны в любой половине (вывод
# программы, имена ключей локали, текст ошибки, который и обсуждается).
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
# Русское СЛОВО, а не отдельная буква: длина от четырёх букв отсекает частицы
# и обрывки внутри путей, оставляя настоящий непереведённый текст.
_RUSSIAN_WORD_RE = re.compile(r"[а-яёА-ЯЁ]{4,}")


def fetch_open_issues(
    repo: str = DEFAULT_REPO,
    label: str = DEFAULT_LABEL,
    *,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> list[tuple[int, str]]:
    """Открытые issue с меткой ``label`` парами «номер, тело».

    Разбор ответа заканчивается здесь, а не расползается по вызывающему коду:
    дальше работают уже готовые пары, и проверка не зависит от формы ответа
    GitHub.

    ``opener`` подменяется в тестах — сеть в наборе тестов не нужна и делала бы
    его флаки (тот же приём, что в ``generate_good_first_issues_badge.py``).

    Raises:
        urllib.error.URLError: сеть недоступна или GitHub ответил ошибкой.
    """
    query = f'repo:{repo} is:issue is:open label:"{label}"'
    url = f"{_API}?{urllib.parse.urlencode({'q': query, 'per_page': 100})}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    open_url = opener or urllib.request.urlopen
    with open_url(request) as response:  # type: ignore[union-attr]
        payload = json.loads(response.read().decode("utf-8"))
    found: list[tuple[int, str]] = []
    for item in payload.get("items", []):
        number = item.get("number")
        if isinstance(number, int):
            found.append((number, str(item.get("body") or "")))
    return found


def english_part(body: str) -> str | None:
    """Текст после якоря ``## In English``; ``None`` — якоря нет вовсе."""
    match = _ANCHOR_RE.search(body or "")
    if match is None:
        return None
    return body[match.end() :]


def russian_words(text: str) -> list[str]:
    """Русские слова вне блоков кода и inline-кода."""
    without_code = _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub(" ", text))
    return _RUSSIAN_WORD_RE.findall(without_code)


def check_issue(number: int, body: str) -> str | None:
    """Что не так с двуязычностью issue; ``None`` — всё в порядке."""
    english = english_part(body or "")
    if english is None:
        return (
            f"#{number}: нет английской половины — в теле не найден заголовок "
            "'## In English'. Метка ведёт англоязычного читателя на текст, "
            "которого он не прочтёт."
        )
    stripped = english.strip()
    if len(stripped) < MIN_TRANSLATION_CHARS:
        return (
            f"#{number}: под '## In English' {len(stripped)} символов — заголовок есть, "
            f"перевода нет (нужно хотя бы {MIN_TRANSLATION_CHARS})."
        )
    leftovers = russian_words(stripped)
    if len(leftovers) > MAX_RUSSIAN_WORDS:
        sample = ", ".join(sorted(set(leftovers))[:5])
        return (
            f"#{number}: английская половина осталась русской — {len(leftovers)} русских "
            f"слов(а) вне кода ({sample}…). Якорь поставлен, текст не переведён."
        )
    return None


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли.

    Справка и сообщения русские, а консоль Windows по умолчанию cp1251/cp1252:
    без этого падал даже ``--help`` — ``UnicodeEncodeError`` вместо текста, и
    скрипт возвращал 1 «на ровном месте», подменяя настоящую причину отказа
    своей собственной. Тот же приём, что в ``check_docs_guardrails.py`` и
    ``cli/options._force_utf8_stdio``. No-op на потоках без ``reconfigure`` —
    например, перехваченных pytest.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0 всегда, кроме сетевого сбоя: непереведённый issue — предупреждение."""
    # ДО разбора аргументов: справку печатает argparse, он же завершает процесс —
    # после ``parse_args()`` переключать кодировку уже некому.
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/check_good_first_issues_bilingual.py",
        description="Проверка, что задачи с меткой good first issue двуязычны.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument(
        "--label",
        action="append",
        dest="labels",
        metavar="МЕТКА",
        help=(
            "метка пула; флаг повторяется. По умолчанию проверяются обе: "
            + " и ".join(repr(label) for label in DEFAULT_LABELS)
        ),
    )
    args = parser.parse_args(argv)
    labels = args.labels or list(DEFAULT_LABELS)

    # Одна задача бывает с обеими метками сразу — проверять её дважды значит
    # дважды и предупредить об одном и том же. Номер здесь ключ, а не тело:
    # тело у одного номера одно.
    bodies: dict[int, str] = {}
    for label in labels:
        try:
            issues = fetch_open_issues(args.repo, label)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            print(f"::warning::не удалось получить список {label!r}: {exc}", file=sys.stderr)
            return EXIT_BROKEN
        print(f"{label}: проверено {len(issues)} открытых issue.")
        bodies.update(dict(issues))

    problems = [
        problem for number, body in sorted(bodies.items()) if (problem := check_issue(number, body))
    ]
    for problem in problems:
        print(f"::warning::{problem}")
    if not problems and bodies:
        print("Все открытые задачи пула для внешних двуязычны.")
    return EXIT_FINDING if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
