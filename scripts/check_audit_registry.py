#!/usr/bin/env python3
"""scripts/check_audit_registry.py — реестр закрытых находок не отстаёт от ``main``.

Документ аудита объявляет собственное правило состояния: **находка открыта, если
её ID не значится в реестре PR**. Реестр ведётся одним списком, а не пометками в
таблицах, потому что одна находка попадает в несколько срезов.

Правило было, механизма не было. Замер 26.08.2026: реестр аудита 2026-08-10
содержал 57 записей при 209 фактически закрытых — 152 находки числились
открытыми, хотя закрывший их pull request давно смержен. Следствия не
косметические: следующий аудит переоткрывает закрытое, а правило «документ
переезжает в archive, когда закрыты все» не срабатывает никогда, потому что
«незакрытые» не кончаются.

Проверка сверяет три источника:

1. **ID находок** — из таблиц живых документов ``docs/audit/*.md``.
2. **Реестр** — строки вида ``| ID | что было | #PR |`` в тех же документах.
   Раздел ищется не по заголовку, а по форме строки: переименование секции не
   должно отключать проверку молча.
3. **Тела смерженных pull request** — упоминания ID через ``scripts/gh_rest.py``
   (REST, а не GraphQL).

Кандидат в реестр — ID, который упомянут в смерженном PR и в реестре
отсутствует. Формулировка вокруг упоминания разбирается: «закрывает» против
«остаётся». Контекст берётся **абзацем, а не предложением**: точки живут в путях
(``SECURITY.md``) и режут окно так, что «из подэпика #986 остаются: …»
читается как подтверждение закрытия.

**Предупреждение, а не отказ.** Разбор эвристический: PR мог упомянуть находку
как соседнюю, а мог закрыть половину. Красный прогон здесь означал бы «почини
документ», а чинить может быть нечего — только что смерженный PR законно ещё не
дописан. Сигнал уходит в summary ночного прогона.

Коды возврата: ``0`` — реестр совпал; ``2`` — прочитать PR нечем (нет токена,
исчерпана квота). Единицы нет намеренно: см. абзац выше.

Запуск::

    python scripts/check_audit_registry.py [--repo OWNER/NAME] [--limit N]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Импорт после правки sys.path: `scripts/` не пакет, а гард обязан ходить тем же
# транспортом, что и остальной конвейер.
import contextlib

import gh_rest

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "AUDIT_DIR",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "FINDING_ID",
    "audit_documents",
    "closing_mentions",
    "main",
    "mention_verdict",
    "parse_document",
]

EXIT_OK = 0
#: Прочитать состояние нечем — не то же самое, что «состояние плохое».
EXIT_UNKNOWN = 2

AUDIT_DIR = pathlib.Path(__file__).parent.parent / "docs" / "audit"

#: ID находки: ``RUN-2-08``, ``JRN-4A-01``, ``READER-4-04``.
FINDING_ID = re.compile(r"\b([A-Z]{2,7}-\d+[A-Z]?-\d{2})\b")

_REGISTRY_ROW = re.compile(r"^\|\s*([A-Z]{2,7}-\d+[A-Z]?-\d{2})\s*\|.*\|\s*#(\d+)\s*\|\s*$")
_VERDICT_ROW = re.compile(r"\b(REFUTED|DUPLICATE)\b")

_CLOSES = re.compile(r"закрывае|закрыт|closes|fixes|чинит|исправл|починен", re.I)
# Корни, а не словоформы: «оставшиеся» и «оставшимися» — одно и то же для
# разбора, а перечислить все падежи не выйдет.
_REMAINS = re.compile(
    r"остал|оставш|остаётся|остаются|не входит|не входят|не закрыв|вынесен|"
    r"следующим заходом|тот же класс|за рамками|ждёт решения|в реестр не",
    re.I,
)

#: PR, которые находки ЗАВОДЯТ, переписывают в чек-листы или ведут сам реестр,
#: а не чинят код: их упоминания не считаются закрытием. Список короткий и растёт
#: медленно — оформительских PR в разы меньше, чем чинящих.
_PAPERWORK_MARKERS = re.compile(
    r"оформление в трекере|переписаны в чек-лист|заводит(ся)? issue|"
    r"находки получены чтением кода|правило для issue",
    re.I,
)

#: Тип PR по Conventional Commits, который по определению ничего не чинит в коде:
#: он ведёт сам документ аудита и перечисляет ID десятками — включая те, что
#: остались открытыми. Первое же ложное срабатывание пришло именно отсюда.
_PAPERWORK_TITLE = re.compile(r"^docs\(audit\)", re.I)


def audit_documents(directory: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Живые документы аудита (``README.md`` — не документ, а индекс)."""
    root = AUDIT_DIR if directory is None else directory
    if not root.exists():
        return []
    return sorted(path for path in root.glob("*.md") if path.name != "README.md")


def parse_document(text: str) -> tuple[set[str], dict[str, int], set[str]]:
    """Разобрать документ аудита на ID находок, реестр и отклонённые.

    Args:
        text: содержимое документа аудита.

    Returns:
        Тройка ``(все ID, {ID: номер PR}, отклонённые ID)``. Реестр ищется по
        форме строки, а не по заголовку раздела: переименование секции не должно
        отключать проверку молча.
    """
    all_ids: set[str] = set()
    registry: dict[str, int] = {}
    rejected: set[str] = set()

    for line in text.splitlines():
        found = FINDING_ID.findall(line)
        all_ids.update(found)
        row = _REGISTRY_ROW.match(line.strip())
        if row is not None:
            registry[row.group(1)] = int(row.group(2))
        elif found and _VERDICT_ROW.search(line):
            rejected.add(found[0])
    return all_ids, registry, rejected


def mention_verdict(body: str, match: re.Match[str]) -> str:
    """Что PR говорит про находку в месте упоминания.

    Args:
        body: тело pull request.
        match: попадание :data:`FINDING_ID` в этом теле.

    Returns:
        ``"closes"``, ``"remains"`` или ``"unclear"``.
    """
    # rfind без совпадения даёт -1, и наивное «+2» съедало первый символ тела —
    # абзац «Закрывает …» превращался в «акрывает …» и переставал распознаваться.
    split = body.rfind("\n\n", 0, match.start())
    start = 0 if split == -1 else split + 2
    end = body.find("\n\n", match.end())
    paragraph = body[start : end if end != -1 else len(body)]

    line_end = body.find("\n", match.end())
    line = body[body.rfind("\n", 0, match.start()) + 1 : line_end if line_end != -1 else len(body)]

    # Заголовок раздела сильнее формы строки: под «## Что осталось в файле» лежит
    # ровно тот же список «`ID` — что не так», что и под «## Что сделано».
    heading = ""
    for candidate in body[:start].splitlines()[::-1]:
        if candidate.lstrip().startswith("#"):
            heading = candidate
            break
    if _REMAINS.search(heading):
        return "remains"

    # «(часть про URL)» сразу за ID: закрыта половина находки, а половина —
    # не закрытие. Реестр держит только закрытые целиком.
    if re.match(r"\s*\**\s*\(част", body[match.end() : match.end() + 12]):
        return "remains"

    # «- **ID** — что было»: находка сама подлежащее разбора, и «остаётся» в том
    # же абзаце относится к чему-то другому («модуль остаётся библиотекой»).
    if re.match(rf"^\s*(?:[-*]\s*)?(?:#+\s*)?\**`?{re.escape(match.group(1))}`?\**\s*[—-]", line):
        return "closes"
    if _REMAINS.search(paragraph):
        return "remains"
    if _CLOSES.search(paragraph):
        return "closes"
    return "unclear"


def closing_mentions(pulls: list[dict[str, object]], known: set[str]) -> dict[str, int]:
    """Найти находки, которые смерженные PR называют закрытыми.

    Args:
        pulls: смерженные pull request (``number`` и ``body``).
        known: ID, состояние которых ещё не зафиксировано в реестре.

    Returns:
        ``{ID: номер PR}`` — кандидаты в реестр. При нескольких PR побеждает
        поздний: фикс обычно приходит после упоминания по соседству.
    """
    found: dict[str, int] = {}
    for pull in sorted(pulls, key=lambda item: int(str(item.get("number", 0)))):
        body = str(pull.get("body") or "")
        title = str(pull.get("title") or "")
        if _PAPERWORK_TITLE.match(title) or _PAPERWORK_MARKERS.search(body):
            continue
        number = int(str(pull.get("number", 0)))
        for match in FINDING_ID.finditer(body):
            fid = match.group(1)
            if fid not in known:
                continue
            if mention_verdict(body, match) == "closes":
                found[fid] = number
    return found


def _merged_pulls(repo: str, limit: int) -> list[dict[str, object]]:
    """Смерженные PR, свежие сверху."""
    collected: list[dict[str, object]] = []
    page = 1
    while len(collected) < limit:
        batch = gh_rest.request(
            "GET",
            f"/repos/{repo}/pulls?state=closed&per_page=100&page={page}"
            "&sort=updated&direction=desc",
        ).data
        if not isinstance(batch, list) or not batch:
            break
        collected.extend(item for item in batch if isinstance(item, dict) and item.get("merged_at"))
        if len(batch) < 100:
            break
        page += 1
    return collected[:limit]


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=400, help="сколько смерженных PR смотреть")
    args = parser.parse_args(argv)

    documents = audit_documents()
    if not documents:
        print("Живых аудитов нет: docs/audit/ пуста — сверять нечего.")
        return EXIT_OK

    try:
        pulls = _merged_pulls(args.repo, args.limit)
    except gh_rest.RateLimited as exc:
        print(f"Квота GitHub исчерпана: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN
    except gh_rest.GitHubError as exc:
        print(f"Прочитать pull request не удалось: {exc}", file=sys.stderr)
        return EXIT_UNKNOWN

    drift = 0
    for document in documents:
        all_ids, registry, rejected = parse_document(document.read_text(encoding="utf-8"))
        unknown = all_ids - set(registry) - rejected
        candidates = closing_mentions(pulls, unknown)
        print(
            f"{document.relative_to(AUDIT_DIR.parent.parent)}: находок {len(all_ids)}, "
            f"в реестре {len(registry)}, отклонено {len(rejected)}"
        )
        if not candidates:
            continue
        drift += len(candidates)
        print(f"  Закрыты смерженным PR, но реестра не получили — {len(candidates)}:")
        for fid, number in sorted(candidates.items(), key=lambda kv: (kv[1], kv[0])):
            print(f"    {fid} — PR #{number}")

    if drift:
        print(
            f"\n::warning::реестр отстал на {drift} запис(ей). Состояние находки читают "
            "только отсюда, поэтому отставший реестр числит закрытое открытым — "
            "и документ никогда не переедет в docs/archive/."
        )
    else:
        print("\nРеестр совпадает с историей мержей.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
