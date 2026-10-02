#!/usr/bin/env python3
"""scripts/generate_good_first_issues_badge.py — живой бейдж пула задач для новичка.

Проблема (issue #835, находка COMM-01/GROW-03 аудита 2026-07-30): README и
CONTRIBUTING зовут новичка «взять issue с меткой ``good first issue``», а
открытых задач с этой меткой было **ноль** — все шесть закрыты. Воронка
контрибьютора упиралась в пустую страницу, и узнавал об этом только сам новичок,
уже кликнув по ссылке. Владелец репозитория пустого пула не видел вовсе.

Скрипт-зеркало ``generate_glossary_badge.py``: спрашивает у GitHub число ОТКРЫТЫХ
issue с меткой и пишет ``.github/badges/good-first-issues.json`` в формате
shields.io "endpoint badge". Цвет — сигнал владельцу: пустой пул красный, тонкий
жёлтый, здоровый зелёный. Плюс ``::warning::`` в лог CI, когда пул иссяк.

**Пустой пул НЕ роняет CI.** Это состояние трекера, а не дефект кода: падение
блокировало бы посторонние PR из-за того, что владелец не успел завести задачу.
Сигнал — цвет бейджа и предупреждение в логе, ровно как просил аудит.

Сеть — только stdlib ``urllib``; токен берётся из ``GITHUB_TOKEN`` (в CI он есть
по умолчанию). Без токена запрос всё равно уходит: публичный поиск issue не
требует авторизации, просто лимит запросов ниже.

Запуск::

    python scripts/generate_good_first_issues_badge.py [--out PATH] [--repo OWNER/NAME]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "DEFAULT_LABEL",
    "DEFAULT_OUT",
    "DEFAULT_REPO",
    "HEALTHY_POOL",
    "THIN_POOL",
    "badge_payload",
    "fetch_open_count",
    "main",
]

_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_REPO = "ArtVsMark/Stepik-Python-Grader"
DEFAULT_LABEL = "good first issue"
DEFAULT_OUT = _ROOT / ".github" / "badges" / "good-first-issues.json"

# Границы «здоровья» пула. Аудит просил держать 5–8 живых задач: ниже пяти пул
# начинает пустеть быстрее, чем пополняется, а ноль — это уже сломанный онрамп.
HEALTHY_POOL = 5
THIN_POOL = 1

_API = "https://api.github.com/search/issues"


def fetch_open_count(
    repo: str = DEFAULT_REPO,
    label: str = DEFAULT_LABEL,
    *,
    opener: Callable[[urllib.request.Request], object] | None = None,
) -> int:
    """Число ОТКРЫТЫХ issue с меткой ``label`` в ``repo``.

    ``opener`` подменяется в тестах — сеть в наборе тестов не нужна и делала бы
    его флаки. По умолчанию — ``urllib.request.urlopen``.

    Raises:
        urllib.error.URLError: сеть недоступна или GitHub ответил ошибкой.
    """
    query = f'repo:{repo} is:issue is:open label:"{label}"'
    url = f"{_API}?{urllib.parse.urlencode({'q': query, 'per_page': 1})}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    open_url = opener or urllib.request.urlopen
    with open_url(request) as response:  # type: ignore[union-attr]
        payload = json.loads(response.read().decode("utf-8"))
    return int(payload["total_count"])


def badge_payload(count: int) -> dict[str, object]:
    """Тело shields-бейджа: число задач и цвет-сигнал (issue #835)."""
    if count <= 0:
        color = "red"
    elif count < HEALTHY_POOL:
        color = "yellow"
    else:
        color = "brightgreen"
    return {
        "schemaVersion": 1,
        "label": "good first issues",
        "message": str(count),
        "color": color,
    }


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0 всегда, кроме сетевого сбоя: пустой пул — предупреждение, не отказ."""
    parser = argparse.ArgumentParser(
        prog="python scripts/generate_good_first_issues_badge.py",
        description="Бейдж числа открытых задач с меткой good first issue.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name репозитория")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="метка пула")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда писать JSON бейджа")
    args = parser.parse_args(argv)

    try:
        count = fetch_open_count(args.repo, args.label)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        # Прошлый честный бейдж остаётся в силе — тот же принцип, что у
        # coverage-бейджа при деградации: лучше устаревшее число, чем выдуманное.
        print(f"::warning::не удалось получить число {args.label!r}: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(badge_payload(count), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"good first issues: {count} -> {args.out}")
    if count <= 0:
        print(
            "::warning::открытых задач с меткой 'good first issue' нет — ссылка из README "
            "и CONTRIBUTING ведёт новичка на пустую страницу.",
        )
    elif count < HEALTHY_POOL:
        print(
            f"::warning::в пуле 'good first issue' осталось {count} — аудит просил держать "
            f"не меньше {HEALTHY_POOL}.",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
