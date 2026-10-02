#!/usr/bin/env python3
"""scripts/capture_github_fixtures.py — снять образцы ответов площадки (issue #1422).

Правило 170 каталога: **зелёное на подделке — тоже гипотеза**. Набор, гоняемый
на значениях, которые придумал автор, доказывает согласованность кода с его же
представлением о чужой стороне и ничего не говорит о самой стороне. Ошибка в
модели изнутри набора невидима по построению: и код, и тест исходят из одного
неверного представления, поэтому чем подробнее набор, тем увереннее он
подтверждает ошибку.

Асимметрия здесь обратная привычной. Ложное красное на подделке дёшево — его
разбирают и находят либо дефект, либо неточность модели. Ложное **зелёное**
стоит недель тишины, а отказ обнаруживается на живой стороне и чужими глазами.

Отсюда единственное лекарство, доступное изнутри: у модели должен быть
**источник**. Не «я думаю, площадка вернёт вот это», а снятый ответ, приложенный
к набору. Источник не делает модель верной навсегда — чужая сторона меняется
(правило 157), — но делает её **сверяемой**.

**Что снимается и чего не снимается.** Только чтения по нашему же публичному
репозиторию: список изменений, одно изменение, задача, проверки на голове,
остаток квоты. Ответы Stepik API отсюда снять нечем — у облачного окна нет ни
``secrets.json``, ни сети до Stepik, — и притворяться, что источник есть, хуже
отсутствия источника: такие подделки помечаются несверенными.

**Секретов в образцах нет по построению:** это ответы на чтение публичных
данных, токен в них не отражается. На всякий случай тело всё равно проходит
через ``diag_log.redact`` — цена нулевая, а новая точка дампа иначе повторила бы
находку ``OPS-1-02``.

Запуск::

    python scripts/capture_github_fixtures.py            # снять заново
    python scripts/capture_github_fixtures.py --check    # только сказать, что устарело
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _datetime
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "CAPTURED_KEY",
    "FIXTURES",
    "FIXTURE_DIR",
    "STALE_AFTER_DAYS",
    "capture",
    "main",
    "stale",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Куда кладутся образцы. Рядом с набором намеренно: источник должен быть виден
#: тому, кто читает подделку, а не лежать в чужом каталоге.
FIXTURE_DIR = _ROOT / "tests" / "fixtures" / "github"

#: Блок происхождения внутри образца. Без него образец неотличим от сочинённого
#: — то есть от того, против чего правило и заведено.
CAPTURED_KEY = "_captured"

#: Через сколько дней образец считается требующим пересъёмки. Не «испорченным»:
#: чужая сторона меняется молча, и срок — повод перечитать, а не отказ.
STALE_AFTER_DAYS = 180

#: Имя образца → путь запроса. Только чтения и только по своему репозиторию.
FIXTURES: dict[str, str] = {
    "pulls_closed": "repos/{repo}/pulls?state=closed&per_page=3",
    "pull": "repos/{repo}/pulls/1415",
    "issue": "repos/{repo}/issues/982",
    "rate_limit": "rate_limit",
}


def _now() -> str:
    """Сегодняшняя дата в ISO — она уезжает в блок происхождения."""
    return _datetime.datetime.now(tz=_datetime.UTC).date().isoformat()


def _redact(payload: Any) -> Any:
    """Прогнать образец через общую редакцию секретов.

    Импорт ленивый: скрипт обязан работать и тогда, когда пакет не установлен,
    — а редакция тогда просто не применяется, и об этом говорится вслух.
    """
    try:
        from stepik_grader.core.diag_log import redact
    except ImportError:
        print("· пакет не установлен: редакция не применена", file=sys.stderr)
        return payload
    return json.loads(redact(json.dumps(payload, ensure_ascii=False)))


def capture(name: str, path: str, *, repo: str = gh_rest.DEFAULT_REPO) -> dict[str, Any]:
    """Снять один ответ площадки и обернуть его блоком происхождения.

    Args:
        name: Имя образца.
        path: Путь запроса с ``{repo}``.
        repo: Владелец/репозиторий.

    Returns:
        Образец: происхождение плюс сам ответ.
    """
    resolved = path.format(repo=repo)
    data = gh_rest.request("GET", resolved).data
    return {
        CAPTURED_KEY: {
            "endpoint": resolved,
            "on": _now(),
            "why": "правило 170: у подделки чужого интерфейса обязан быть источник",
        },
        "response": _redact(data),
    }


def stale(*, today: str | None = None) -> list[str]:
    """Образцы, которых нет или которые пора переснять.

    Args:
        today: Дата в ISO для сверки; ``None`` — сегодняшняя.

    Returns:
        Строки-находки; пустой список — все образцы свежи.
    """
    current = _datetime.date.fromisoformat(today or _now())
    problems: list[str] = []
    for name in sorted(FIXTURES):
        path = FIXTURE_DIR / f"{name}.json"
        if not path.exists():
            problems.append(f"{name}: образца нет — подделка не сверена ни с чем")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{name}: образец не читается ({error})")
            continue
        origin = payload.get(CAPTURED_KEY)
        if not isinstance(origin, dict) or not origin.get("endpoint") or not origin.get("on"):
            problems.append(
                f"{name}: у образца нет блока происхождения — он неотличим от сочинённого"
            )
            continue
        try:
            taken = _datetime.date.fromisoformat(str(origin["on"]))
        except ValueError:
            problems.append(f"{name}: дата съёмки не разбирается: {origin['on']!r}")
            continue
        age = (current - taken).days
        if age > STALE_AFTER_DAYS:
            problems.append(f"{name}: снят {age} дней назад — переснять, а не править по памяти")
    return problems


def main(argv: list[str] | None = None) -> int:
    """0 — образцы на месте и свежи, 1 — находка, 2 — снять не удалось."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="не снимать, только сверить")
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    args = parser.parse_args(argv)

    if args.check:
        problems = stale()
        print(f"Образцы ответов площадки: объявлено — {len(FIXTURES)}.")
        if problems:
            print("FAIL: подделка без сверяемого источника:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("У каждой подделки есть снятый источник, и он не просрочен.")
        return 0

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, path in sorted(FIXTURES.items()):
        try:
            payload = capture(name, path, repo=args.repo)
        except gh_rest.RateLimited as error:
            print(f"снять не удалось: {error}")
            return 2
        except (gh_rest.GitHubError, gh_rest.MissingToken, OSError) as error:
            print(f"снять не удалось ({name}): {error}")
            return 2
        target = FIXTURE_DIR / f"{name}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"снят {name}: {target.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
