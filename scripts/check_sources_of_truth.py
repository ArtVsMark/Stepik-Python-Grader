#!/usr/bin/env python3
"""scripts/check_sources_of_truth.py — контракт не называет двух источников одного (issue #1438).

`CLAUDE.md` читают как исполняемый документ, и читают **по частям**: агент
приходит в тот раздел, куда его привела задача, и соседних не открывает. Поэтому
противоречие между разделами не замечает никто — оба абзаца по отдельности
верны, выстраданы своим инцидентом и прошли ревью.

Живой случай, из которого выросла проверка. Контракт говорил дважды, что
состояние работы читают из трекера, и один раз — что из реестра в документе
аудита; каждое из трёх утверждений объявляло себя исключительным. За вторым
стоял механизм (`check_audit_registry.py`), то есть проверка держала ровно ту
половину, которую первая запрещает. Цена: 493 находки из 883 разошлись между
двумя «единственными» источниками, а очередь мержа сериализовалась на общем
файле — семь изменений подряд дописывали строку в одно место, и каждое
смерженное делало следующее конфликтным.

**Почему объявление, а не разбор прозы.** Искать противоречия в тексте —
шумно и недоказуемо, а гейт, краснеющий на верном ответе, снимают первой же
правкой. Здесь предмет узкий: заявления об **исключительности** перечислимы,
их в контракте меньше десятка. Незаявленное краснеет, объявление, пережившее
текст, краснеет тоже, а два предмета с одним именем и разными адресами —
отказ. Ровно то, чем был дефект.

Три исхода (правило 039): 0 — сходится, 1 — расхождение, 2 — проверка не
отработала (нет файла объявления или контракта).

Запуск::

    python scripts/check_sources_of_truth.py
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import re
import sys
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "CONTRACT",
    "DECLARATION",
    "MARKERS",
    "collisions",
    "exclusivity_paragraphs",
    "main",
    "undeclared",
    "unused_claims",
]

_ROOT = pathlib.Path(__file__).parent.parent

#: Документ, который читают как исполняемый.
CONTRACT = _ROOT / "CLAUDE.md"

#: Объявление предметов и адресов.
DECLARATION = _ROOT / ".rules" / "sources_of_truth.json"

#: Обороты, которыми в контракте объявляют исключительность. Список закрытый и
#: короткий намеренно: он задаёт ПРЕДМЕТ проверки, а не ловит все формулировки
#: русского языка. Новый оборот добавляется вместе с разбором, зачем он нужен.
MARKERS = (
    "только в самом",
    "только оттуда",
    "только из самой",
    "единственный источник",
    "единственное место",
    "источник истины",
    "только из окна",
)


def _paragraphs(text: str) -> list[str]:
    """Абзацы документа одной строкой каждый.

    Единица — абзац, а не строка: утверждение переносится, и построчный разбор
    рвал бы его пополам.
    """
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]


def exclusivity_paragraphs(text: str) -> list[str]:
    """Абзацы, объявляющие единственность источника."""
    return [para for para in _paragraphs(text) if any(marker in para for marker in MARKERS)]


def undeclared(paragraphs: list[str], subjects: list[dict[str, Any]]) -> list[str]:
    """Заявления, которым не сопоставлен ни один объявленный предмет."""
    claims = [str(claim) for subject in subjects for claim in subject.get("claims") or []]
    return [para for para in paragraphs if not any(claim in para for claim in claims)]


def unused_claims(paragraphs: list[str], subjects: list[dict[str, Any]]) -> list[str]:
    """Объявленные фразы, которых в контракте больше нет.

    Обратная половина: объявление, пережившее текст, разрешает будущему
    заявлению проехать под чужим, уже недействительным предметом.
    """
    joined = "\n".join(paragraphs)
    return [
        str(claim)
        for subject in subjects
        for claim in subject.get("claims") or []
        if str(claim) not in joined
    ]


def collisions(subjects: list[dict[str, Any]]) -> list[str]:
    """Предметы, названные больше чем одним адресом.

    Это и есть дефект: два места контракта отвечают за одно и то же и
    показывают в разные стороны.
    """
    seen: dict[str, set[str]] = {}
    for subject in subjects:
        name = str(subject.get("subject") or "")
        seen.setdefault(name, set()).add(str(subject.get("address") or ""))
    return [
        f"{name}: {', '.join(sorted(addresses))}"
        for name, addresses in sorted(seen.items())
        if len(addresses) > 1
    ]


def main(argv: list[str] | None = None) -> int:
    """0 — сходится, 1 — расхождение, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=pathlib.Path, default=CONTRACT)
    parser.add_argument("--declaration", type=pathlib.Path, default=DECLARATION)
    args = parser.parse_args(argv)

    try:
        text = args.contract.read_text(encoding="utf-8")
        declared = json.loads(args.declaration.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"проверка не отработала: {error}")
        return 2

    subjects = declared.get("subjects") or []
    if not isinstance(subjects, list) or not subjects:
        print("проверка не отработала: в объявлении нет ни одного предмета")
        return 2

    paragraphs = exclusivity_paragraphs(text)
    # Правило 165, вторая половина: охват называется числом. Молчание означает и
    # «сходится», и «ничего не смотрели».
    print(
        f"Источники истины: заявлений об исключительности — {len(paragraphs)}, "
        f"объявленных предметов — {len(subjects)}."
    )

    problems: list[str] = []
    if new := undeclared(paragraphs, subjects):
        problems += [
            "заявление об исключительности не объявлено — назовите предмет и адрес "
            f"в {args.declaration.name}:\n  " + "\n  ".join(item[:160] for item in new)
        ]
    if stale := unused_claims(paragraphs, subjects):
        problems += [
            "объявление пережило текст контракта — уберите строку:\n  " + "\n  ".join(stale)
        ]
    if clash := collisions(subjects):
        problems += ["один предмет назван разными адресами:\n  " + "\n  ".join(clash)]

    if problems:
        print("FAIL: контракт называет источники несогласованно:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("У каждого предмета один адрес, и каждое заявление объявлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
