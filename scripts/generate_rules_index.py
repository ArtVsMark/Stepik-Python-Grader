#!/usr/bin/env python3
"""scripts/generate_rules_index.py — указатель правил из следов (issue #1342).

Правила проекта записаны хорошо, но лежат там, где рождались: строка в
``CLAUDE.md``, абзац в ``docs/agent/*``, комментарий у кода, разбор в отчёте
аудита. Замер: чтобы собрать их в каталог, пришлось перебрать восемь
документов, двенадцать ADR, комментарии в ``src/`` и ``scripts/`` и архив
отчётов — то есть **перечислить собственные правила нельзя**, хотя все они
записаны.

Указатель поэтому **генерируется, а не ведётся**. Вести его руками означало бы
третий источник рядом с формулировкой (``CLAUDE.md``) и историей (каталог), и он
начал бы отставать с первого же нового правила — молча, как уже было с числами
в витрине и со списками открытых задач.

Источник — каталог [claude-code-playbook](https://github.com/ArtVsMark/claude-code-playbook):
у каждого правила есть раздел «След» со ссылкой на issue или файл. Отсюда три
свойства:

* новое правило со следом на этот проект появляется в указателе **само**;
* правило без такого следа не попадает — и это верно: здесь оно не действует;
* «какие правила у нас приняты» перестаёт быть списком, который поддерживают:
  **признак принятия — наличие следа**.

**Колонка «чем держится» — главная.** Указатель показывает не только, какие
правила есть, но и чем каждое обеспечено: гейт (падает в CI или
``preflight.py``), шаг процесса (проверяется человеком в названный момент) или
ничем. Третий уровень — не позор, а очередь на автоматизацию, но он обязан быть
**виден числом**, а не растворён в тексте.

Запуск::

    python scripts/generate_rules_index.py --catalogue ../claude-code-playbook
    python scripts/generate_rules_index.py --catalogue ../claude-code-playbook --check
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys

__all__ = [
    "GENERATED_HEADER",
    "MECHANISM_LEVELS",
    "Rule",
    "collect_rules",
    "main",
    "render_index",
    "rule_from_text",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Куда пишется указатель. Файл руками не правится — его перезаписывает скрипт.
INDEX_PATH = _ROOT / "docs" / "agent" / "rules" / "README.md"

#: Этот проект. След на чужой репозиторий означает «правило есть, но действует
#: не здесь» — такое в указатель не идёт.
PROJECT = "ArtVsMark/Stepik-Python-Grader"

GENERATED_HEADER = "<!-- СГЕНЕРИРОВАНО scripts/generate_rules_index.py — не править руками -->"

#: Три уровня обеспечения. Порядок важен: он же порядок убывания надёжности.
MECHANISM_LEVELS = ("гейт", "шаг процесса", "не объявлено")

_TITLE_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$", re.MULTILINE)
_ISSUE_RE = re.compile(rf"{re.escape(PROJECT)}#(?P<number>\d+)")
_EXTRA_ISSUE_RE = re.compile(r"(?<![\w/])#(?P<number>\d+)")
_PATH_RE = re.compile(r"`(?P<path>(?:scripts|src|tests|docs|\.github)/[^`\s]+)`")

#: Слова, по которым в тексте правила опознаётся объявленный механизм. Список
#: намеренно короткий: догадываться о механизме нельзя, иначе метрика «не
#: обеспечено ничем» начнёт врать в приятную сторону.
_GATE_WORDS = ("гейт", "preflight", "ci ", "падает в ci", "проверяет скрипт")
_PROCESS_WORDS = ("чек-лист", "чеклист", "перед pr", "разбор после", "вручную")


@dataclasses.dataclass(frozen=True)
class Rule:
    """Одно правило каталога глазами указателя."""

    slug: str
    title: str
    #: Issue этого проекта, названные в следе.
    issues: tuple[int, ...]
    #: Пути этого репозитория, названные в следе.
    paths: tuple[str, ...]
    #: Чем держится — одно из :data:`MECHANISM_LEVELS`.
    mechanism: str

    @property
    def number(self) -> str:
        """Номер правила: первый сегмент слага (`001-transport-…` → `001`)."""
        return self.slug.split("-", 1)[0]

    @property
    def applies_here(self) -> bool:
        """Действует ли правило в этом проекте — то есть есть ли след сюда."""
        return bool(self.issues or self.paths)


def _section(text: str, name: str) -> str:
    """Тело раздела ``## name`` — до следующего заголовка того же уровня."""
    for match in _SECTION_RE.finditer(text):
        if match.group("name").strip().lower() != name.lower():
            continue
        start = match.end()
        following = _SECTION_RE.search(text, start)
        return text[start : following.start() if following else len(text)].strip()
    return ""


#: Как статус из `.rules/bindings.json` читается в указателе.
_MECHANISM_LABELS = {"gate": "гейт", "process-step": "шаг процесса", "none": "не объявлено"}


def _bindings_mechanisms(path: pathlib.Path | None = None) -> dict[str, str]:
    """Чем держится каждое правило — по ответу ЭТОГО проекта каталогу.

    Источник поля сменился намеренно (issue #1351). Раньше уровень брался из
    раздела «Механизм» самого правила, то есть из **каталога**, — а это поле
    потребителя: одно и то же правило в проекте с полным конвейером держится
    гейтом, в витрине — шагом сборки, в статическом сайте ничем. Отсюда и
    прежние «88 не объявлено» из 89: поле пустовало не потому, что у нас нет
    гейтов, а потому что заведено не в том репозитории.

    Ключ — номер правила, значение — метка уровня. Нет ответа по правилу —
    оно и не попадает в словарь: вызывающая сторона покажет «не объявлено».
    """
    target = path if path is not None else _ROOT / ".rules" / "bindings.json"
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Битый ответ — не повод молча зазеленить метрику: пусть всё будет
        # «не объявлено», это честнее приятной ошибки.
        return {}
    result: dict[str, str] = {}
    for rule_id, raw in (data.get("rules") or {}).items():
        if not isinstance(raw, dict) or raw.get("status") != "active":
            continue
        result[str(rule_id)] = _MECHANISM_LABELS.get(str(raw.get("mechanism")), "не объявлено")
    return result


#: Любой репозиторий владельца в тексте следа. Нужен, чтобы отличить «наш путь»
#: от «пути соседа»: правило может родиться в каталоге и там же держаться.
_ANY_REPO_RE = re.compile(r"ArtVsMark/[A-Za-z0-9._-]+")


def _our_paths(trace: str) -> list[str]:
    """Файловые следы, ведущие в ЭТОТ репозиторий, а не в соседний.

    Контекст берётся абзацем — от пустой строки до пустой строки, — а не
    предложением: в технической прозе точка стоит внутри имён (`ci.yml`,
    `check_gates.py`), и резать по ней значит отрезать половину смысла
    (правило 144). Владельца абзаца задаёт первый названный в нём репозиторий;
    не названо ни одного — абзац считается нашим, потому что правило уже
    отобрано как действующее здесь.

    Прецедент: правила 049, 140 и 142 родились в самом каталоге и держатся его
    файлами. Указатель считал их пути своими и падал с «след ведёт в никуда» —
    то есть отказывался пересобираться из-за чужого механизма.
    """
    found: list[str] = []
    for paragraph in trace.split("\n\n"):
        owner = _ANY_REPO_RE.search(paragraph)
        if owner is not None and owner.group(0) != PROJECT:
            continue
        found.extend(match.group("path") for match in _PATH_RE.finditer(paragraph))
    return found


def rule_from_text(slug: str, text: str) -> Rule:
    """Разобрать файл правила: заголовок, след, объявленный механизм."""
    title_match = _TITLE_RE.search(text)
    title = title_match.group("title").strip() if title_match else slug
    trace = _section(text, "След")

    issues: list[int] = []
    # Первый номер задаёт репозиторий: `Owner/Repo#1296, #1329` — вторая
    # ссылка относится к тому же проекту, что и первая.
    mentions_project = bool(_ISSUE_RE.search(trace))
    for match in _ISSUE_RE.finditer(trace):
        issues.append(int(match.group("number")))
    if mentions_project:
        head = trace[: trace.find("\n\n")] if "\n\n" in trace else trace
        for match in _EXTRA_ISSUE_RE.finditer(head):
            number = int(match.group("number"))
            if number not in issues:
                issues.append(number)

    paths = tuple(dict.fromkeys(_our_paths(trace)))
    return Rule(
        slug=slug,
        title=title,
        issues=tuple(sorted(set(issues))),
        paths=paths,
        # Уровень проставляется в `collect_rules` из ответа проекта: раздел
        # «Механизм» каталога описывает чужие механизмы, а не наши.
        mechanism="не объявлено",
    )


def collect_rules(catalogue: pathlib.Path, *, repo_root: pathlib.Path | None = None) -> list[Rule]:
    """Правила каталога, действующие здесь — отсортированные по слагу.

    Raises:
        FileNotFoundError: каталога нет либо в нём нет правил — это отказ, а не
            пустой указатель: гейт, не нашедший предмета, обязан падать.
        ValueError: след ссылается на файл, которого в репозитории больше нет.
            Значит предмет правила изменился, а правило осталось.
    """
    rules_dir = catalogue / "rules" / "ru"
    files = sorted(rules_dir.glob("*.md")) if rules_dir.is_dir() else []
    if not files:
        raise FileNotFoundError(
            f"{rules_dir}: правил не найдено — указатель строить не из чего. "
            "Клонируйте каталог: git clone https://github.com/ArtVsMark/claude-code-playbook"
        )

    root = repo_root if repo_root is not None else _ROOT
    # Уровень «чем держится» — поле ПОТРЕБИТЕЛЯ (issue #1351): его знает этот
    # проект, а не каталог. Ответа нет — остаётся «не объявлено», как и было.
    held = _bindings_mechanisms(root / ".rules" / "bindings.json")
    collected: list[Rule] = []
    broken: list[str] = []
    for path in files:
        rule = rule_from_text(path.stem, path.read_text(encoding="utf-8"))
        if not rule.applies_here:
            continue
        for named in rule.paths:
            if not (root / named).exists():
                broken.append(f"{rule.slug}: след ведёт в никуда — {named}")
        declared = held.get(rule.number)
        if declared is not None:
            rule = dataclasses.replace(rule, mechanism=declared)
        collected.append(rule)

    if broken:
        raise ValueError(
            "След указывает на исчезнувший предмет правила:\n  "
            + "\n  ".join(broken)
            + "\nЭто сигнал, что предмет изменился, а правило осталось."
        )
    return collected


def render_index(rules: list[Rule], *, catalogue_url: str = "") -> str:
    """Собрать текст указателя — с колонкой «чем держится» и итоговым числом."""
    url = catalogue_url or "https://github.com/ArtVsMark/claude-code-playbook"
    unmechanised = [rule for rule in rules if rule.mechanism == "не объявлено"]

    lines = [
        GENERATED_HEADER,
        "",
        "# Указатель правил",
        "",
        "> **Что это.** Правила, действующие в этом проекте, — собранные из следов",
        f"> каталога [claude-code-playbook]({url}). Указатель **генерируется**",
        "> (`python scripts/generate_rules_index.py`), а не ведётся руками: список,",
        "> который поддерживают вручную, начинает отставать с первого же нового",
        "> правила — молча.",
        ">",
        "> **Признак принятия — наличие следа.** Правило без ссылки на этот проект",
        "> сюда не попадает: здесь оно не действует.",
        ">",
        "> **Второй рубеж — [дайджест](DIGEST.md).** Там утверждение каждого правила",
        "> одной строкой, и его читает окно на старте: указатель отвечает «какие",
        "> правила есть и чем держатся», дайджест — «что именно они требуют».",
        ">",
        "> **Указатель — для ревизии, а не для работы.** В работе правило действует,",
        "> только если попало в `CLAUDE.md`, в стартовое сообщение окна или в",
        "> задание исполнителя.",
        "",
        "## Чем держатся правила",
        "",
        f"Всего правил, действующих здесь: **{len(rules)}**.",
        "",
        "| Уровень | Что это | Сколько |",
        "|---|---|---|",
    ]
    for level in MECHANISM_LEVELS:
        count = sum(1 for rule in rules if rule.mechanism == level)
        explanation = {
            "гейт": "падает в CI или в `preflight.py`",
            "шаг процесса": "проверяется человеком в названный момент",
            "не объявлено": "механизм не назван в каталоге — очередь на автоматизацию",
        }[level]
        lines.append(f"| **{level}** | {explanation} | {count} |")

    lines += [
        "",
        f"**Не объявлено: {len(unmechanised)}.** Это метрика, и она обязана уменьшаться.",
        "Уровень берётся из раздела «Механизм» самого правила — догадываться по тексту",
        "нельзя: правило, где слово «гейт» встретилось в описании инцидента, не",
        "становится от этого обеспеченным, а метрика начала бы врать в приятную сторону.",
        "",
        "## Правила",
        "",
        "| Правило | След | Чем держится |",
        "|---|---|---|",
    ]
    for rule in rules:
        trace_parts = [f"#{number}" for number in rule.issues]
        trace_parts += [f"`{path}`" for path in rule.paths]
        link = f"[{rule.title}]({url}/blob/main/rules/ru/{rule.slug}.md)"
        lines.append(f"| {link} | {', '.join(trace_parts)} | {rule.mechanism} |")

    lines.append("")
    return "\n".join(lines)


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — указатель сгенерирован (или совпал при ``--check``); 1 — отказ."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/generate_rules_index.py",
        description="Собрать указатель правил из следов каталога claude-code-playbook.",
    )
    parser.add_argument(
        "--catalogue",
        type=pathlib.Path,
        default=_ROOT.parent / "claude-code-playbook",
        help="путь к клону каталога правил",
    )
    parser.add_argument(
        "--output", type=pathlib.Path, default=INDEX_PATH, help="куда писать указатель"
    )
    parser.add_argument(
        "--check", action="store_true", help="не писать, а сверить: разошлось — отказ"
    )
    args = parser.parse_args(argv)

    try:
        rules = collect_rules(args.catalogue)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    text = render_index(rules)
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current == text:
            print(f"указатель актуален: правил {len(rules)}")
            return 0
        print(
            f"{args.output}: указатель разошёлся с каталогом — "
            "перегенерируйте (python scripts/generate_rules_index.py)",
            file=sys.stderr,
        )
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    unmechanised = sum(1 for rule in rules if rule.mechanism == "не объявлено")
    print(f"{args.output}: правил {len(rules)}, без объявленного механизма {unmechanised}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
