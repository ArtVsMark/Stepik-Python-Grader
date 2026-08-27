#!/usr/bin/env python3
"""scripts/generate_rules_digest.py — второй рубеж: правила читаются окном на старте.

Первый рубеж — механизм: гейт краснеет, и правило соблюдается независимо от
того, помнит ли его кто-нибудь. Но механизмом покрыто не всё, и часть правил
держится только тем, что окно про них знает. Знать оно может лишь то, что
прочитало: `CLAUDE.md` формулирует немногие, указатель
(`docs/agent/rules/README.md`) перечисляет заголовки и следы, а **утверждения**
правил живут в каталоге, до которого в работе никто не доходит.

Дайджест закрывает этот зазор: одна строка на правило — что оно требует, — и
вся сотня с лишним умещается в стартовый контекст окна. Печатает его
``.claude/hooks/session_start.py`` при открытии сессии.

Три решения, которые здесь важнее кода:

1. **Порядок групп — по тому, чем правило держится, и слабое идёт первым.**
   Правило с гейтом окну помнить не обязательно: забудет — покраснеет CI.
   Правило без механизма не поймает никто, поэтому оно стоит выше и читается,
   пока внимание свежее.
2. **Утверждение обрезается с маркером** (правило 016): «…» и полный текст по
   ссылке. Молча урезанное утверждение выглядит полным и врёт про границы.
3. **Дайджест генерируется, а не ведётся.** Источник — клон каталога; свежесть
   сверяет ``scripts/check_rules_digest.py``. Ведомый руками список отстаёт с
   первого же нового правила, причём молча.

Запуск::

    python scripts/generate_rules_digest.py --catalogue /tmp/playbook
    python scripts/generate_rules_digest.py --catalogue /tmp/playbook --check
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

__all__ = [
    "CLAIM_LIMIT",
    "DIGEST",
    "Rule",
    "clip",
    "load_rules",
    "main",
    "render",
]

_ROOT = pathlib.Path(__file__).parent.parent
DIGEST = _ROOT / "docs" / "agent" / "rules" / "DIGEST.md"
_BINDINGS = _ROOT / ".rules" / "bindings.json"
_CATALOGUE_URL = "https://github.com/ArtVsMark/claude-code-playbook/blob/main"

#: Предел длины утверждения в строке дайджеста. Не косметика: сто сорок правил
#: по абзацу каждое — это уже не стартовый контекст, а документ, который окно
#: пролистает.
CLAIM_LIMIT = 200

_CLAIM_RE = re.compile(r"\*\*Правило\.\*\*(.+?)\n\n", re.S)

#: Как называются группы, в каком порядке идут и несут ли утверждение целиком.
#: Слабое — первым: правило с гейтом окну помнить не обязательно, а правило без
#: механизма не поймает никто. У группы с гейтом утверждения нет намеренно —
#: строка заголовка стоит дёшево, а место в стартовом контексте не бесконечно.
_GROUPS: tuple[tuple[str, str, str, bool], ...] = (
    (
        "none",
        "Не держится ничем — только вниманием окна",
        "Механизма нет. Если окно забудет, не заметит никто: ни CI, ни ревью.",
        True,
    ),
    (
        "unreviewed",
        "Ответа по правилу ещё нет",
        "Проект не решил, действует ли правило здесь. До решения — читать как действующее.",
        True,
    ),
    (
        "process-step",
        "Держится шагом процесса",
        "Проверяется человеком в названный момент — то есть тоже вниманием, "
        "но с местом и временем.",
        True,
    ),
    (
        "gate",
        "Держится гейтом",
        "Забудете — покраснеет CI или preflight. Здесь только заголовки: "
        "помнить их наизусть незачем.",
        False,
    ),
)


class Rule:
    """Правило в том виде, в каком его читает окно.

    Attributes:
        rule_id: номер в каталоге.
        title: заголовок по-русски.
        claim: утверждение — что делать и чего не делать.
        status: ответ проекта (`active`, `unreviewed`, …).
        mechanism: чем держится, если действует.
        where: где именно держится.
        path: путь к файлу правила внутри каталога.
    """

    __slots__ = ("claim", "mechanism", "path", "rule_id", "status", "title", "where")

    def __init__(
        self,
        rule_id: str,
        title: str,
        claim: str,
        status: str,
        mechanism: str,
        where: str,
        path: str,
    ) -> None:
        self.rule_id = rule_id
        self.title = title
        self.claim = claim
        self.status = status
        self.mechanism = mechanism
        self.where = where
        self.path = path

    @property
    def group(self) -> str:
        """К какой группе дайджеста относится правило."""
        if self.status == "unreviewed":
            return "unreviewed"
        if self.status != "active":
            return ""  # not-applicable и rejected окну помнить не нужно
        return self.mechanism or "none"


def clip(text: str, limit: int = CLAIM_LIMIT) -> str:
    """Обрезать утверждение по границе слова, обозначив обрыв (правило 016)."""
    single = " ".join(text.split())
    if len(single) <= limit:
        return single
    cut = single[:limit].rsplit(" ", 1)[0]
    return f"{cut} …"


def load_rules(catalogue: pathlib.Path) -> list[Rule]:
    """Собрать правила каталога вместе с ответом проекта по каждому.

    Args:
        catalogue: корень клона каталога правил.

    Returns:
        Правила по возрастанию номера.

    Raises:
        FileNotFoundError: экспорта каталога нет — «пусто» и «не прочитали»
            обязаны различаться (правило 039).
    """
    export = catalogue / "export" / "rules.json"
    data = json.loads(export.read_text(encoding="utf-8"))
    answers = json.loads(_BINDINGS.read_text(encoding="utf-8"))["rules"]

    rules: list[Rule] = []
    for item in sorted(data["rules"], key=lambda entry: entry["id"]):
        relative = item["files"]["ru"]
        text = (catalogue / relative).read_text(encoding="utf-8")
        match = _CLAIM_RE.search(text)
        answer = answers.get(item["id"], {})
        rules.append(
            Rule(
                rule_id=item["id"],
                title=item["title"]["ru"],
                claim=" ".join(match.group(1).split()) if match else "",
                status=answer.get("status", "unreviewed"),
                mechanism=answer.get("mechanism", ""),
                where=answer.get("where", ""),
                path=relative,
            )
        )
    return rules


def render(rules: list[Rule]) -> str:
    """Собрать текст дайджеста: группы по силе механизма, слабое первым."""
    grouped: dict[str, list[Rule]] = {key: [] for key, _, _, _ in _GROUPS}
    for rule in rules:
        if rule.group in grouped:
            grouped[rule.group].append(rule)
    counts = {key: len(value) for key, value in grouped.items()}
    shown = sum(counts.values())

    lines = [
        "<!-- СГЕНЕРИРОВАНО scripts/generate_rules_digest.py — не править руками -->",
        "",
        "# Правила: утверждения одной строкой",
        "",
        "> **Второй рубеж.** Первый — механизм: гейт краснеет, и правило действует",
        "> независимо от памяти окна. Но механизмом покрыто не всё, и остальное",
        "> держится тем, что окно про правило знает. Здесь оно и написано —",
        "> утверждение каждого правила одной строкой, чтобы это можно было прочесть",
        "> на старте, а не когда правило уже нарушено.",
        ">",
        "> Порядок групп не алфавитный и не по номеру: **сначала то, что не поймает",
        "> машина**. Правило с гейтом окну помнить не обязательно.",
        ">",
        "> Полный текст правила — в каталоге, файл `rules/ru/<номер>-*.md`; чем оно",
        "> держится здесь — в [`.rules/bindings.json`](../../../.rules/bindings.json).",
        "",
        f"Правил в каталоге: **{len(rules)}**, окну важны **{shown}** — "
        + ", ".join(f"{title.lower()}: {counts[key]}" for key, title, _, _ in _GROUPS)
        + ".",
        "",
        "Остальные — `not-applicable` и `rejected`: предмета здесь нет либо решение",
        "иное, и ответ записан в `.rules/bindings.json` вместе с причиной.",
        "",
    ]

    for key, title, about, with_claim in _GROUPS:
        block = grouped[key]
        lines += [f"## {title} — {len(block)}", "", about, ""]
        if not block:
            # Пустое состояние объявляется словами: пустой раздел читался бы как
            # «не собрали», а не как «здесь ничего нет» (правило 027).
            lines += ["_Сейчас пусто._", ""]
            continue
        for rule in block:
            claim = f" {clip(rule.claim)}" if with_claim else ""
            lines.append(f"- **{rule.rule_id}** {rule.title}.{claim}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Собрать дайджест; с ``--check`` — только сверить, что он не отстал."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogue", type=pathlib.Path, required=True, help="клон каталога правил"
    )
    parser.add_argument("--check", action="store_true", help="не писать, а сверить")
    args = parser.parse_args(argv)

    try:
        rules = load_rules(args.catalogue)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"каталог не прочитан: {exc}", file=sys.stderr)
        return 2

    text = render(rules)
    if args.check:
        current = DIGEST.read_text(encoding="utf-8") if DIGEST.exists() else ""
        if current == text:
            print(f"дайджест актуален: правил {len(rules)}")
            return 0
        print(
            "дайджест отстал от каталога — пересоберите: "
            "python scripts/generate_rules_digest.py --catalogue <клон>",
            file=sys.stderr,
        )
        return 1

    DIGEST.parent.mkdir(parents=True, exist_ok=True)
    DIGEST.write_text(text, encoding="utf-8")
    print(f"дайджест собран: правил {len(rules)}, файл {DIGEST.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
