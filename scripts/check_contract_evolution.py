#!/usr/bin/env python3
"""scripts/check_contract_evolution.py — контракт описывает правила своей эволюции.

Правило 113 каталога. Перечислить поля мало: потребитель обязан знать, **что в
контракте стабильно, что расширяемо и как в него добавляют новое**. Без этого
каждое изменение превращается в переговоры с нуля, а потребители расходятся —
один считает незнакомый ключ ошибкой, другой молча его теряет, и оба правы,
потому что документ не сказал.

У нас это уже написано ровно в одном контракте — ``result-contract.md``
(«Ожидания стабильности»), — и остальные разошлись бы с ним незаметно: раздела
нет, значит и претензии не к чему предъявить.

Что считается ответом. Раздел с правилами эволюции обязан сказать три вещи, и
гейт ищет признак каждой:

- **стабильность** — какие имена и смыслы не меняются без миграции;
- **расширяемость** — что происходит с незнакомым полем у потребителя;
- **как добавляют** — каким путём в контракт входит новое.

Это признаки, а не формулировки: текст пишет автор, гейт лишь не даёт разделу
исчезнуть или выродиться в одну фразу. Список самих контрактов закрыт
(:data:`CONTRACTS`) — «документ, похожий на контракт» определять эвристикой
значило бы ловить не то и пропускать нужное.

Запуск::

    python scripts/check_contract_evolution.py
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

__all__ = ["ASPECTS", "CONTRACTS", "contracts_without_evolution", "evolution_section", "main"]

# issue #1095: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_ROOT = pathlib.Path(__file__).parent.parent

#: Документы, которые называют себя контрактом и имеют внешнего потребителя.
#: Закрытый список: контракт — это роль документа, а не слово в заголовке.
CONTRACTS: tuple[str, ...] = (
    "docs/dev/result-contract.md",
    "docs/dev/api.md",
    "docs/dev/web-contracts.md",
    "docs/dev/usage-export.md",
)

#: Заголовки, под которыми у нас живут правила эволюции. Первый — тот, что
#: появился раньше гейта: правило не переименовывает уже написанное.
_HEADINGS: tuple[str, ...] = ("Ожидания стабильности", "Правила эволюции")

#: Три вопроса и признаки ответа на каждый.
ASPECTS: dict[str, tuple[str, ...]] = {
    "что стабильно": ("стабильн", "не мен", "ломающ"),
    "что расширяемо": ("расшир", "аддитив", "незнаком", "опциональн"),
    "как добавляют новое": ("добавля", "добавляет", "добавлен", "новое поле", "новый ключ"),
}


def evolution_section(text: str) -> str | None:
    """Тело раздела с правилами эволюции, если он есть.

    Args:
        text: содержимое документа-контракта.

    Returns:
        Текст раздела до следующего заголовка того же уровня, иначе ``None``.
    """
    lines = text.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            title = line[3:].strip()
            inside = any(title.startswith(heading) for heading in _HEADINGS)
            continue
        if inside:
            collected.append(line)
    return "\n".join(collected) if collected else None


def contracts_without_evolution(root: pathlib.Path | None = None) -> list[str]:
    """Контракты без раздела эволюции или с разделом, отвечающим не на всё."""
    base = root or _ROOT
    found: list[str] = []
    for name in CONTRACTS:
        path = base / name
        if not path.is_file():
            found.append(f"{name}: файла нет — список контрактов разошёлся с деревом")
            continue
        body = evolution_section(path.read_text(encoding="utf-8"))
        if body is None or not body.strip():
            found.append(
                f"{name}: нет раздела «{_HEADINGS[1]}» "
                f"(или «{_HEADINGS[0]}») — потребителю не сказано, что стабильно"
            )
            continue
        lowered = body.lower()
        unanswered = [
            question
            for question, markers in ASPECTS.items()
            if not any(marker in lowered for marker in markers)
        ]
        if unanswered:
            found.append(f"{name}: раздел не отвечает — {', '.join(unanswered)}")
    return found


def main() -> int:
    """0 — каждый контракт объявил свою эволюцию; 1 — находка; 2 — нечего читать."""
    if not (_ROOT / "docs" / "dev").is_dir():
        print("каталога docs/dev нет — проверять нечего", file=sys.stderr)
        return 2

    found = contracts_without_evolution()
    if found:
        print("контракт не описывает правила собственной эволюции:", file=sys.stderr)
        for place in found:
            print(f"  • {place}", file=sys.stderr)
        print(
            "\nОбразец — docs/dev/result-contract.md § «Ожидания стабильности»: что "
            "стабильно, что расширяемо (и что потребитель делает с незнакомым полем), "
            "как в контракт добавляют новое.",
            file=sys.stderr,
        )
        return 1

    print(f"контрактов: {len(CONTRACTS)}; каждый говорит, что в нём стабильно и как его расширяют")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
