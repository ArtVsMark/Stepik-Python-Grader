#!/usr/bin/env python3
"""scripts/read_objections.py — замечания витрины к содержанию (issue #1450).

Поток содержания односторонний: истина глоссария живёт здесь, витрина
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) — копия
(инвариант №6 `CLAUDE.md`). У односторонности есть обратная сторона: витрина
**видит** дефекты, которые чинить может только грейдер, и до сих пор ей нечем
было их предъявить. Раньше она собирала отчёт в Markdown, а человек переносил
его в задачу руками — список из четырёхсот идентификаторов так не живёт:
перенесённый однажды, он устаревает следующей же выгрузкой.

Теперь издатель считает, а потребитель читает — тот же приём, каким витрина
берёт наш ``facts.json``. Она публикует замечания машиночитаемо в ветке
``badges``, эта команда их забирает.

**Читается, а не переносится.** Ничего не записывается ни в карточки, ни в
трекер: адресат у находок — ночной обход (`scripts/nightly_checks.py`), тело
задачи он ведёт сам. Реестра замечаний у нас нет намеренно — он был бы вторым
источником рядом с файлом витрины и устаревал бы ровно так же, как переносимый
руками Markdown.

**Храповик, а не гейт.** Замечаний в снятом снимке 739, и краснеть на них
каждую ночь значило бы приучить не смотреть. Поэтому известный долг объявлен в
:data:`BASELINE` по каждому правилу, и находкой считается **рост**, а не
наличие. Правило, которого в объявлении нет, красное сразу: новое замечание —
это событие, и незамеченным оно проходить не должно. Упавшее число печатается
предложением опустить порог: храповик держится тем, что число только
уменьшается (тот же приём, что у `check_glossary_examples.py`).

Отдельно проверяется **стык контрактов**: ``cards[]`` витрины — это наши
``GlossaryCard.id``, и идентификатор, которого у нас нет, означает, что
стороны разъехались. Такие называются поимённо и считаются находкой, а не
отбрасываются молча — молча отброшенный вернётся следующей выгрузкой.

Находка **без** ``cards`` (уровень глоссария целиком, например раздел из одной
карточки) не теряется: она печатается своим разделом. Строить работу только по
карточкам значило бы терять её молча.

**Одно число уже сошлось независимо, и это лучшая новость снимка.** Витрина
считает `example-compiles` — 90 карточек, чей пример не компилируется; ровно
столько же независимо насчитал наш `check_glossary_examples.py`, у которого
порог `BUDGET = 90`. Две реализации разного кода на разных сторонах пришли к
одному числу — сильнее подтверждения, что контракт сходится, изнутри не
получить. Дублирования при этом нет: у витрины это замечание, у нас — храповик,
не дающий числу расти.

**Пороги и образец переснимаются вместе.** `BASELINE` сверяется тестом со
снятым файлом (`tests/fixtures/glossary/objections.json`): храповик, чьи числа
никогда не сходились с источником, ничего не держит — он либо всегда красный,
либо всегда зелёный. Происхождение образца — в
`tests/fixtures/glossary/README.md`.

Три исхода (правило 039): 0 — прочитано, рост не обнаружен; 1 — находка
(рост, новое правило или неизвестный идентификатор); 2 — **не отработала**:
файла нет, ответ нечитаем, квота исчерпана. Третий отличается от первого
намеренно: «замечаний не прибавилось» и «спросить не удалось» — разные
состояния с разными действиями.

Запуск::

    python scripts/read_objections.py
    python scripts/read_objections.py --json
    python scripts/read_objections.py --file snapshot.json   # без сети
"""

from __future__ import annotations

import argparse
import base64
import binascii
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

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest  # noqa: E402  — путь к соседнему модулю добавлен строкой выше

__all__ = [
    "BASELINE",
    "DATA_DIR",
    "OBJECTIONS_PATH",
    "OBJECTIONS_REF",
    "SHOWCASE_REPO",
    "Finding",
    "card_ids",
    "fetch_objections",
    "glossary_level",
    "main",
    "over_budget",
    "parse_findings",
    "under_budget",
    "unknown_cards",
]

_ROOT = pathlib.Path(__file__).parent.parent

#: Витрина — издатель замечаний. Не эталон полноты (инвариант №6).
SHOWCASE_REPO = "ArtVsMark/Glossary-Python"

#: Файл замечаний и ветка, в которой он лежит. Ветка отдельная по той же
#: причине, по которой отдельная у нас: производное, пересобираемое чаще, чем
#: идут изменения, в общей ветке превращает каждое слияние в конфликт.
OBJECTIONS_PATH = ".github/badges/objections.json"
OBJECTIONS_REF = "badges"

#: Карточки — источник истины содержания.
DATA_DIR = _ROOT / "src" / "stepik_grader" / "glossary" / "data"

#: Известный долг по каждому правилу витрины: сколько находок мы уже видели.
#: Порог опускают починкой карточек, а не правкой числа; правила, которого
#: здесь нет, достаточно, чтобы обход покраснел.
BASELINE: dict[str, int] = {
    "translated": 618,
    "example-compiles": 90,
    "summary-length": 29,
    "section-size": 2,
}


class Finding:
    """Одно замечание витрины.

    Attributes:
        rule: идентификатор правила витрины (``example-indent``).
        severity: ``error`` или ``warning`` — оценка издателя, не наша.
        message: человеческая формулировка, как её дала витрина.
        count: сколько находок этого правила.
        cards: наши ``GlossaryCard.id``; пусто — уровень глоссария целиком.
    """

    __slots__ = ("cards", "count", "message", "rule", "severity")

    def __init__(
        self, rule: str, severity: str, message: str, count: int, cards: list[str]
    ) -> None:
        self.rule = rule
        self.severity = severity
        self.message = message
        self.count = count
        self.cards = cards

    def __repr__(self) -> str:  # pragma: no cover — только для отладки
        return f"Finding({self.rule!r}, count={self.count}, cards={len(self.cards)})"


def fetch_objections(
    *,
    repo: str = SHOWCASE_REPO,
    path: str = OBJECTIONS_PATH,
    ref: str = OBJECTIONS_REF,
    **kwargs: Any,
) -> dict[str, Any]:
    """Забрать ``objections.json`` из ветки ``badges`` витрины.

    Через contents-API, а не ``raw.githubusercontent.com``: тем же способом,
    каким соседи читают наши бейджи и факты, — один транспорт, один токен, один
    кэш по ``ETag``.

    Args:
        repo: ``owner/name`` витрины.
        path: путь к файлу внутри репозитория.
        ref: ветка, в которой он лежит.
        **kwargs: пробрасывается в :func:`gh_rest.request` (``opener`` в тестах).

    Returns:
        Разобранный объект замечаний.

    Raises:
        gh_rest.GitHubError: файла нет, прав нет, сеть не ответила.
        ValueError: ответ пришёл, но содержимого в нём нет или оно не JSON.
    """
    data = gh_rest.request("GET", f"repos/{repo}/contents/{path}?ref={ref}", **kwargs).data
    if not isinstance(data, dict):
        raise ValueError("contents-API ответил не объектом")
    raw = str(data.get("content") or "")
    if not raw.strip():
        # Файл больше мегабайта contents-API отдаёт с пустым `content` и
        # ссылкой. Молча вернуть «замечаний нет» здесь было бы худшим исходом:
        # пустота читалась бы как чистота.
        raise ValueError("в ответе нет содержимого — вероятно, файл слишком велик для contents-API")
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"содержимое не разобралось: {error}") from error
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("объект замечаний ожидался словарём")
    return payload


def parse_findings(payload: dict[str, Any]) -> list[Finding]:
    """Разобрать ``findings`` объекта замечаний.

    Записи неизвестной формы отбрасываются молча, а вот **отсутствие** ключа
    ``findings`` — ошибка: пустой список и отсутствующий ключ читаются
    одинаково, хотя означают «замечаний нет» и «схема разъехалась».

    Raises:
        ValueError: ключа ``findings`` в объекте нет.
    """
    if "findings" not in payload:
        raise ValueError("в объекте замечаний нет ключа findings")
    found: list[Finding] = []
    for entry in payload.get("findings") or []:
        if not isinstance(entry, dict):
            continue
        cards = [str(card) for card in entry.get("cards") or []]
        found.append(
            Finding(
                rule=str(entry.get("rule") or "?"),
                severity=str(entry.get("severity") or "warning"),
                message=str(entry.get("message") or ""),
                count=int(entry.get("count") or len(cards)),
                cards=cards,
            )
        )
    return found


def card_ids(data_dir: pathlib.Path = DATA_DIR) -> set[str]:
    """Идентификаторы карточек нашей базы — то, с чем сопоставляются ``cards``.

    Читается напрямую JSON, а не через ``JsonGlossaryProvider``: скрипту нужен
    только набор ``id``, и тянуть ради него пакет значило бы завести
    зависимость `scripts → src` там, где хватает двух строк.
    """
    known: set[str] = set()
    for path in sorted(data_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cards = payload if isinstance(payload, list) else payload.get("cards") or []
        known |= {str(card["id"]) for card in cards if isinstance(card, dict) and card.get("id")}
    return known


def unknown_cards(findings: list[Finding], known: set[str]) -> dict[str, list[str]]:
    """Идентификаторы витрины, которых нет у нас: правило → список.

    Это стык контрактов, а не дефект содержания: ``cards[]`` объявлены как наши
    ``GlossaryCard.id``, и расхождение означает, что стороны разъехались —
    переименовали карточку здесь, отстала выгрузка там.
    """
    return {
        finding.rule: missing
        for finding in findings
        if (missing := sorted(set(finding.cards) - known))
    }


def glossary_level(findings: list[Finding]) -> list[Finding]:
    """Находки без ``cards`` — про глоссарий целиком, а не про карточку.

    Отдельная функция, потому что именно они выпадают у любого потребителя,
    который строит работу по списку карточек.
    """
    return [finding for finding in findings if not finding.cards]


def over_budget(
    findings: list[Finding], budgets: dict[str, int] | None = None
) -> list[tuple[str, int, int]]:
    """Правила, где находок стало больше объявленного: (правило, стало, порог).

    Правило, отсутствующее в объявлении, получает порог ``0`` — новое
    замечание обязано быть заметным сразу, а не после того, как кто-то
    заметит незнакомую строку в отчёте.
    """
    limits = BASELINE if budgets is None else budgets
    return [
        (finding.rule, finding.count, limits.get(finding.rule, 0))
        for finding in findings
        if finding.count > limits.get(finding.rule, 0)
    ]


def under_budget(
    findings: list[Finding], budgets: dict[str, int] | None = None
) -> list[tuple[str, int, int]]:
    """Правила, где находок стало меньше объявленного — порог пора опустить.

    Обратная половина храповика: объявление, пережившее починку, разрешает
    дефекту вернуться до прежнего числа, ничем не покраснев.
    """
    limits = BASELINE if budgets is None else budgets
    return [
        (finding.rule, finding.count, limits[finding.rule])
        for finding in findings
        if finding.rule in limits and finding.count < limits[finding.rule]
    ]


def _report(findings: list[Finding], known: set[str], payload: dict[str, Any]) -> int:
    """Напечатать разбор и вернуть код возврата (0 или 1)."""
    snapshot = payload.get("snapshot") or {}
    totals = payload.get("totals") or {}
    # Правило 165: охват называется числом. Молчание означает и «сходится», и
    # «ничего не смотрели».
    print(
        f"Замечания витрины {SHOWCASE_REPO}: правил — {len(findings)}, "
        f"находок — {sum(f.count for f in findings)}, "
        f"карточек затронуто — {totals.get('cards_affected', '?')}; "
        f"снимок — {snapshot.get('cards', '?')} карточек, у нас — {len(known)}."
    )

    for finding in findings:
        mark = "!" if finding.severity == "error" else "·"
        scope = f"{len(finding.cards)} карточек" if finding.cards else "уровень глоссария"
        print(f"  [{mark}] {finding.rule}: {finding.count} ({scope}) — {finding.message}")

    whole = glossary_level(findings)
    if whole:
        print(
            "\nНаходки уровня глоссария (списка карточек у них нет — "
            "по `cards` они бы выпали молча):"
        )
        for finding in whole:
            print(f"  - {finding.rule}: {finding.message}")

    problems: list[str] = []
    if grown := over_budget(findings):
        problems += [
            "замечаний стало больше объявленного:\n    "
            + "\n    ".join(
                f"{rule}: {now} при пороге {limit}"
                + (" — правило новое, в объявлении его нет" if limit == 0 else "")
                for rule, now, limit in grown
            )
        ]
    if stranger := unknown_cards(findings, known):
        problems += [
            "витрина называет карточки, которых у нас нет — контракты разъехались:\n    "
            + "\n    ".join(
                f"{rule}: {', '.join(ids[:10])}"
                + (f" … и ещё {len(ids) - 10}" if len(ids) > 10 else "")
                for rule, ids in stranger.items()
            )
        ]

    if problems:
        print("\nFAIL: замечания витрины требуют работы:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nПорог в BASELINE опускают починкой карточек, а не правкой числа: "
            "храповик держится тем, что число только уменьшается.",
            file=sys.stderr,
        )
        return 1

    if lowered := under_budget(findings):
        print("\nПороги пора опустить — стало меньше:")
        for rule, now, limit in lowered:
            print(f"  - {rule}: {limit} → {now}")
    print("\nРост не обнаружен: известный долг не увеличился, незнакомых карточек нет.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """0 — рост не обнаружен, 1 — находка, 2 — прочитать не удалось."""
    parser = argparse.ArgumentParser(
        prog="python scripts/read_objections.py", description="Замечания витрины к содержанию."
    )
    parser.add_argument("--repo", default=SHOWCASE_REPO, help="owner/name витрины")
    parser.add_argument(
        "--file",
        type=pathlib.Path,
        help="читать снимок из локального файла вместо сети (офлайн, тесты)",
    )
    parser.add_argument("--json", action="store_true", help="машинный вывод разбора")
    args = parser.parse_args(argv)

    try:
        if args.file is not None:
            payload = json.loads(args.file.read_text(encoding="utf-8"))
        else:
            payload = fetch_objections(repo=args.repo)
        findings = parse_findings(payload)
        known = card_ids()
    except gh_rest.RateLimited as error:
        print(f"прочитать не удалось: квота исчерпана — {error}", file=sys.stderr)
        return gh_rest.EXIT_WAIT
    except (gh_rest.GitHubError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"прочитать не удалось: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [
                        {
                            "rule": f.rule,
                            "severity": f.severity,
                            "count": f.count,
                            "cards": f.cards,
                            "budget": BASELINE.get(f.rule, 0),
                        }
                        for f in findings
                    ],
                    "unknown_cards": unknown_cards(findings, known),
                    "glossary_level": [f.rule for f in glossary_level(findings)],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    return _report(findings, known, payload)


if __name__ == "__main__":
    sys.exit(main())
