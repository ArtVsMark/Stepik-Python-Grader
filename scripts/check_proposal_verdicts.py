"""scripts/check_proposal_verdicts.py — что каталог ответил на наши правила.

Правило 080: правило, родившееся здесь, записывается в общий каталог. До сих
пор оно держалось словами, и `CLAUDE.md` называл причину — «проверить запись в
чужой репозиторий нечем». Премиса устарела и была неверной уже тогда: канал
двусторонний, и каталог публикует **вердикт** по каждому предложению в своём
``.rules/proposals.json`` — обычным HTTPS, без токена и прав.

Отсюда предмет проверки. Наш ``.rules/proposals.json`` перечисляет, что мы
предложили; каталог отвечает по ключу ``владелец/репозиторий:слаг``. Расхождение
одностороннее и потому проверяемое: **предложение с вынесенным вердиктом
предложением быть перестало**. Оставленное, оно врёт о нашем состоянии —
выглядит ждущим ответа, хотя ответ получен.

Что вердикт означает:

* ``admitted`` — принято, каталог присвоил номер. Правило теперь общее, и
  ответить по нему нужно наравне с чужими (``.rules/bindings.json``).
* ``rejected`` — предмет есть, решение иное; причина названа.
* ``merged-into`` — предмет тот же, что у существующей записи.

Чего скрипт НЕ делает: не судит о качестве вердикта и не пишет в каталог.
Номера у предложения нет и быть не может — его присваивает каталог при приёме.

Запуск::

    python scripts/check_proposal_verdicts.py --catalogue <клон каталога>
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
from typing import Any

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["PROJECT", "main", "settled_proposals"]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Как этот проект назван в ключе вердикта.
PROJECT = "ArtVsMark/Stepik-Python-Grader"

#: Наши предложения и ответ каталога лежат по одному и тому же пути — у каждого
#: в своём репозитории. Это не совпадение: файл один, стороны разные.
PROPOSALS = ".rules/proposals.json"

#: Код «проверка не отработала» — отдельный исход, а не находка.
EXIT_BROKEN = 2


def _load(path: pathlib.Path) -> dict[str, Any]:
    """Прочитать JSON, назвав ПРЕДМЕТ отказа, а не только его причину.

    Правило 158: «не отработала» обязана сказать, что именно не отработало.
    Скрипт читает два файла в разных репозиториях, и без адреса «файл не
    разбирается» не отвечает на единственный нужный вопрос — чей это отказ.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{path}: файла нет") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: не разбирается ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался объект")
    return data


def settled_proposals(ours: dict[str, Any], theirs: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Наши предложения, по которым каталог уже вынес вердикт.

    Returns:
        Тройки (слаг, статус, пояснение) — в порядке нашего файла.
    """
    verdicts = theirs.get("verdicts")
    if not isinstance(verdicts, dict):
        return []

    settled: list[tuple[str, str, str]] = []
    for item in ours.get("proposals") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "")
        verdict = verdicts.get(f"{PROJECT}:{slug}")
        if not isinstance(verdict, dict):
            continue
        status = str(verdict.get("status") or "")
        number = str(verdict.get("rule") or "")
        why = str(verdict.get("why") or "").strip()
        detail = f"правило {number}" if number else why[:120] or "пояснения нет"
        settled.append((slug, status, detail))
    # Вердикт выносится ПОСЛЕ последнего случая (правило 159): ранний выход
    # здесь оставил бы непроверенными предложения, идущие следом.
    return settled


def main(argv: list[str] | None = None) -> int:
    """0 — расхождений нет, 1 — есть, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=pathlib.Path, required=True, help="клон каталога")
    parser.add_argument("--root", type=pathlib.Path, default=_ROOT, help="корень проекта")
    args = parser.parse_args(argv)

    try:
        ours = _load(args.root / PROPOSALS)
        theirs = _load(args.catalogue / PROPOSALS)
    except (FileNotFoundError, ValueError) as exc:
        print(f"проверка не отработала: {exc}", file=sys.stderr)
        return EXIT_BROKEN

    offered = [item for item in (ours.get("proposals") or []) if isinstance(item, dict)]
    settled = settled_proposals(ours, theirs)
    if not settled:
        print(f"предложений в работе: {len(offered)}; вердиктов по ним каталог ещё не выносил.")
        return 0

    print("каталог ответил на наши предложения — они предложениями быть перестали:")
    for slug, status, detail in settled:
        print(f"  {slug} — {status}: {detail}")
    print(
        "\nУберите их из .rules/proposals.json. Принятое означает ещё и то, что "
        "правило стало общим: ответьте по его номеру в .rules/bindings.json."
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
