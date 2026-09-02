#!/usr/bin/env python3
"""scripts/check_rule_bindings.py — ответ проекта каталогу правил (issue #1351).

Каталог [Engineering-Incidents-Playbook](https://github.com/ArtVsMark/Engineering-Incidents-Playbook)
отдаёт правила машиночитаемо, а проект-потребитель отвечает, что он с каждым
сделал: статус, чем держится и где. Контракт — `export/README.md` каталога,
схема ``1.1``; заготовка — `templates/bindings.json` там же.

**Почему файл живёт здесь, а не в каталоге.** Здесь живёт механизм: одно и то
же правило в проекте с полным конвейером держится гейтом, в витрине — шагом
сборки, в статическом сайте ничем. Каталог про чужие гейты не знает и проверять
их не может — «у нас держится гейтом» это утверждение проекта, и отвечает за
него его же конвейер, то есть вот этот скрипт.

Отсюда же дефект, из которого выросла задача: уровень «чем держится» указатель
`docs/agent/rules/README.md` брал из раздела «Механизм» **каталога**, поэтому у
88 правил из 89 читалось «не объявлено». Поле пустовало не потому, что у нас
нет гейтов, а потому что заведено не в том репозитории.

Проверяется:

1. **Схема и поля по статусу.** ``active`` требует ``mechanism`` и ``where``;
   ``rejected`` и ``not-applicable`` — ``why``. Отрицательное решение без
   причины через полгода неотличимо от «не дошли руки».
2. **Заявленное существует.** ``where`` с путём к файлу проверяется на диске:
   ответ проекта — это декларация, а декларация обязана сходиться с фактом.
3. **Полнота против каталога** (только с ``--catalogue``): ответ нужен по
   КАЖДОМУ правилу, а не по тем, до которых дошли руки. Правило без записи
   попадает в метрику нерассмотренных, а не исчезает.
4. **Каждый номер версии называет свой предмет** (правило 164). Ключ
   ``schema`` стоит в обоих наших файлах `.rules/` и ещё в двух выгрузках
   каталога, а предметов у него четыре: формат выгрузки правил (1.2), ответа
   потребителя (1.1), предложения (1.0) и сводки потребителей (1.0). Сам ключ
   переименовать нельзя — его имя задаёт чужой контракт, — поэтому предмет
   называет соседний ``schema_of``, в точке чтения.

Метрика — **сколько правил не обеспечено ничем**: ``unreviewed`` плюс
``active`` с ``mechanism: none``. Она не просто «должна уменьшаться» — её держит
храповик :data:`UNHELD_BUDGET`: правило, принятое на словах, обязано быть либо
закрыто гейтом, либо замечено конвейером, либо **записано документом** — с
разрешимым адресом в любом случае. ``none`` означает, что нарушение не заметит
никто; бюджет опускается починкой, а не правкой числа. На пустом входе гейт
падает, а не зеленеет.

Запуск::

    python scripts/check_rule_bindings.py                        # сверка формата
    python scripts/check_rule_bindings.py --catalogue <клон>      # + полнота
    python scripts/check_rule_bindings.py --catalogue <клон> --sync  # обновить
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "BINDINGS",
    "GATE_DEBT",
    "MECHANISMS",
    "STATUSES",
    "UNHELD_BUDGET",
    "binding_violations",
    "catalogue_schema",
    "contract_drift",
    "main",
    "named_paths",
    "neighbour_holds",
    "proposal_drift",
    "reachable_gates",
    "unheld_count",
    "version_subjects",
]

#: Как этот проект назван в сводке потребителей каталога.
PROJECT = "ArtVsMark/Stepik-Python-Grader"

_ROOT = Path(__file__).resolve().parent.parent
BINDINGS = _ROOT / ".rules" / "bindings.json"

#: Ключ, который называет ПРЕДМЕТ версии рядом с самой версией (правило 164).
#: Имя `schema` задано чужим контрактом и переименованию не подлежит, а
#: означает в четырёх местах четыре разных формата — поэтому предмет
#: называется соседним ключом, а не оговоркой в отдельном документе.
SUBJECT_KEY = "schema_of"

#: Наши файлы ответа каталогу: путь относительно корня, заготовка каталога, с
#: которой сверяется номер, и версия контракта на сегодня. Номера РАЗНЫЕ и
#: двигаются независимо: ответ — 1.1, предложение — 1.0. Один ключ на оба и
#: есть та ошибка, ради которой заведён `SUBJECT_KEY`.
CONTRACT_FILES: tuple[tuple[str, str, str], ...] = (
    (".rules/bindings.json", "bindings.json", "1.1"),
    (".rules/proposals.json", "proposals.json", "1.0"),
)

STATUSES = ("active", "rejected", "not-applicable", "unreviewed")
#: Четыре уровня контракта 1.1. Граница между ними — один вопрос: что
#: случится, если правило нарушить. `gate` отвергает до слияния, `pipeline`
#: замечает прогоном не блокируя, `document` замечается человеком, если он
#: читал, `none` не замечается ничем.
#:
#: Прежнего `process-step` здесь нет намеренно. Одно слово называло сразу три
#: последних уровня, и каталог в своих отчётах его не сводит ни к одному —
#: подмена была бы догадкой за потребителя. Цена склейки измерена на нашем же
#: ответе: 52 записи `process-step` разошлись на 24 конвейера, 26 документов и
#: 2 «ничем» (issue #1400).
MECHANISMS = ("gate", "pipeline", "document", "none")

#: Сколько правил ещё не закреплено ничем. Не «столько допустимо», а «столько
#: осталось»: каждое такое правило действует ровно до тех пор, пока о нём помнит
#: окно. Число опускается починкой — гейтом или записью решения в документ.
UNHELD_BUDGET = 0

#: Расширения, по которым `where` считается путём, а не описанием шага.
_PATH_SUFFIXES = (".py", ".yml", ".yaml", ".json", ".md", ".txt")

#: Каталоги репозитория, с которых начинается путь в `where`. Список закрыт:
#: угадывать путь по одному слэшу нельзя, иначе `merge=union` из прозы поедет
#: в проверку как имя файла.
_PATH_ROOTS = ("scripts", "src", "tests", "docs", ".github", ".claude", ".rules", "changelog.d")

_PATH_RE = re.compile(
    r"(?:{roots})/[\w./-]+(?:{suffixes})".format(
        roots="|".join(re.escape(root) for root in _PATH_ROOTS),
        suffixes="|".join(re.escape(suffix) for suffix in _PATH_SUFFIXES),
    )
)

#: Откуда вообще что-то запускается: прогоны CI, pre-commit и предпушевой гейт.
#: Всё остальное достижимо только через них.
_ENTRY_POINTS = (".github/workflows/*.yml", ".pre-commit-config.yaml", "scripts/preflight.py")

#: Гейты, объявленные в ответе, но пока никем не запускаемые. Причина и адрес
#: обязательны: молча внесённое исключение — это отключённая проверка, а не
#: объявленный долг. Список — храповик, он может только уменьшаться.
GATE_DEBT: dict[str, str] = {
    "check_pr_ready.py": (
        "issue #1400: вердикт перед мержем запускает окно руками — в прогонах он "
        "встречается только в комментариях. Отказ настоящий, но держится памятью "
        "окна, а не падением: это шаг процесса, названный гейтом"
    ),
    "check_attribution.py": (
        "issue #1400: зовётся из check_pr_ready.py, поэтому наследует его долг — "
        "цепочка начинается там, где её запускает человек"
    ),
    "check_work_overlap.py": (
        "issue #1400: не вызывается ни из workflow, ни из pre-commit, ни из preflight; "
        "шаг preflight «кто ссылается на правку» объявлен неблокирующим намеренно"
    ),
    "skip_inventory.py": (
        "issue #1400: инвентарь пропусков набора — отчёт, а не проверка; "
        "вне собственного теста его никто не зовёт"
    ),
    "version.py": (
        "issue #1400: считает версию, а не проверяет её; дрейф ловит "
        "check_version_consistency.py, и он в прогоне"
    ),
}


def named_paths(where: str) -> list[str]:
    """ВСЕ пути репозитория, названные в `where`, — а не только первый.

    Раньше проверялось первое слово строки, и этого хватало ровно до записи,
    где путей два. Из 153 ответов таких 34, то есть у каждой пятой второе и
    дальнейшие утверждения не проверял никто: правило 119 называло креплением
    `tests/test_test_loader.py`, которого нет, и гейт молчал, потому что первым
    в строке стоял существующий модуль (issue #1400).
    """
    return list(dict.fromkeys(_PATH_RE.findall(where)))


#: Корневые документы, которые считаются адресом по имени.
_ROOT_DOCS = (
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    "HISTORY.md",
    "CHANGELOG.md",
)


def _has_address(where: str) -> bool:
    """Есть ли в `where` разрешимый адрес механизма, а не одна проза."""
    return bool(named_paths(where)) or any(doc in where for doc in _ROOT_DOCS)


def _invocations(text: str, *, yaml: bool) -> set[str]:
    """Имена скриптов, которые этот текст ЗАПУСКАЕТ, а не упоминает.

    Различать обязательно, и оба направления ошибки уже случились на этом самом
    файле. Упоминание в прозе выдаёт отчёт за подключённый гейт:
    `check_docs_guardrails.py` называет ``scripts/skip_inventory.py`` в
    docstring. Комментарий в workflow — то же самое: `check_pr_ready.py`
    встречается в трёх прогонах, и **везде** это `#`-комментарий, а не строка
    запуска, из-за чего гейт по одному `grep` выглядел подключённым.

    Поэтому в YAML сначала снимаются комментарии, а в Python засчитываются
    только формы настоящего вызова: элемент argv строкой, сборка пути через
    ``"scripts" / "X.py"`` и импорт соседнего модуля.
    """
    if yaml:
        body = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        return set(re.findall(r"python[^\n]*?\bscripts/([\w]+\.py)", body))

    found: set[str] = set()
    for pattern in (
        r"[\"']scripts/([\w]+\.py)[\"']",
        r"[\"']scripts[\"']\s*/\s*[\"']([\w]+\.py)[\"']",
    ):
        found.update(re.findall(pattern, text))
    # Импорт соседнего скрипта — тоже вызов: `check_pr_ready` ходит в GitHub
    # через `import gh_rest`, а не запуском отдельного процесса.
    found.update(f"{name}.py" for name in re.findall(r"^\s*import\s+([\w]+)$", text, re.M))
    return found


def reachable_gates(base: Path) -> set[str]:
    """Скрипты, до которых дотягивается хоть один вход — с пересадками.

    Пересадки нужны, потому что цепочка обычно длиннее одного звена:
    `check_attribution.py` зовёт `check_pr_ready.py`, а его — `ci.yml`. Считать
    достижимым только названное в workflow значило бы записать в долг рабочий
    гейт.
    """
    seeds: list[Path] = []
    for pattern in _ENTRY_POINTS:
        seeds.extend(sorted(base.glob(pattern)) if "*" in pattern else [base / pattern])

    reached: set[str] = set()
    frontier = [path for path in seeds if path.exists()]
    while frontier:
        path = frontier.pop()
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in _invocations(text, yaml=path.suffix in {".yml", ".yaml"}) - reached:
            reached.add(name)
            script = base / "scripts" / name
            if script.exists():
                frontier.append(script)
    return reached


def binding_violations(data: dict[str, Any], *, root: Path | None = None) -> list[str]:
    """Нарушения контракта в ответе проекта (пустой список — чисто)."""
    base = root if root is not None else _ROOT
    problems: list[str] = []

    if data.get("schema") != "1.1":
        problems.append(
            f"schema={data.get('schema')!r} — контракт потребителя сегодня 1.1; "
            "читатель обязан игнорировать незнакомые поля, но не версию"
        )

    rules = data.get("rules")
    if not isinstance(rules, dict) or not rules:
        # Гейт без предмета проверки обязан падать, а не зеленеть на пустоте.
        return [*problems, ".rules/bindings.json: раздел `rules` пуст — отвечать не о чем"]

    for rule_id, raw in sorted(rules.items()):
        if not isinstance(raw, dict):
            problems.append(f"правило {rule_id}: запись не объект")
            continue

        status = raw.get("status")
        if status not in STATUSES:
            problems.append(f"правило {rule_id}: статус {status!r} не из {', '.join(STATUSES)}")
            continue

        if status == "active":
            mechanism = raw.get("mechanism")
            if mechanism not in MECHANISMS:
                problems.append(
                    f"правило {rule_id}: active без механизма из {', '.join(MECHANISMS)} — "
                    "«принято» без ответа на вопрос «чем держится» и есть фикция"
                )
            where = str(raw.get("where") or "")
            if not where and mechanism != "none":
                problems.append(f"правило {rule_id}: active без `where` — где именно держится?")
            elif where:
                if mechanism != "none" and not _has_address(where):
                    problems.append(
                        f"правило {rule_id}: в `where` нет разрешимого адреса — путь к файлу, "
                        "образец вида .github/workflows/*.yml или корневой документ по имени. "
                        "Проза рядом законна, вместо адреса — нет: гейт, чей адрес нельзя "
                        "назвать, обычно и не гейт"
                    )
                for named in named_paths(where):
                    if not (base / named).exists():
                        problems.append(
                            f"правило {rule_id}: `where` указывает на {named}, которого нет — "
                            "предмет правила изменился, а запись осталась"
                        )
                if mechanism == "gate":
                    problems.extend(_unreachable(rule_id, where, base=base))
        elif status in {"rejected", "not-applicable"} and not str(raw.get("why") or "").strip():
            problems.append(
                f"правило {rule_id}: {status} без причины — через полгода это "
                "неотличимо от «не дошли руки»"
            )

    return problems


def _unreachable(rule_id: str, where: str, *, base: Path) -> list[str]:
    """Отказ, если гейт заявлен, но ни один названный скрипт никем не запускается.

    Строка ``"mechanism": "gate", "where": "scripts/что_угодно.py"`` зеленела,
    пока файл существует, — то есть «держится гейтом» подтверждалось наличием
    файла, а не тем, что он где-то падает (issue #1400).

    Достаточно **одного** достижимого скрипта из названных: запись законно
    называет и генератор, и сторожа при нём (правило 120 — `generate_rules_
    index.py` рядом с `check_rules_digest.py`), и держит её второй. Требовать
    достижимости от каждого значило бы запретить называть предмет вместе с
    механизмом.
    """
    scripts = [
        named.split("/", 1)[1]
        for named in named_paths(where)
        if named.startswith("scripts/") and (base / named).exists()
    ]
    if not scripts:
        return []

    reached = reachable_gates(base)
    if any(script in reached for script in scripts):
        return []
    declared = [script for script in scripts if script in GATE_DEBT]
    if declared:
        return []
    return [
        f"правило {rule_id}: ни один из {', '.join(scripts)} не запускается ни "
        "workflow, ни pre-commit, ни preflight — «держится гейтом» подтверждается "
        "падением, а не существованием файла. Подключите скрипт либо объявите "
        "долг в GATE_DEBT с причиной"
    ]


def unheld_count(data: dict[str, Any]) -> tuple[int, int]:
    """Сколько правил не обеспечено ничем и сколько всего отвечено."""
    rules = data.get("rules") or {}
    unheld = sum(
        1
        for raw in rules.values()
        if isinstance(raw, dict)
        and (
            raw.get("status") == "unreviewed"
            or (raw.get("status") == "active" and raw.get("mechanism") == "none")
        )
    )
    return unheld, len(rules)


def _export_ids(catalogue: Path) -> set[str]:
    """Номера правил из машинного экспорта каталога."""
    export = catalogue / "export" / "rules.json"
    if not export.exists():
        raise FileNotFoundError(
            f"{export}: экспорта каталога нет — клонируйте "
            "https://github.com/ArtVsMark/Engineering-Incidents-Playbook"
        )
    data = json.loads(export.read_text(encoding="utf-8"))
    return {str(rule["id"]) for rule in data.get("rules", []) if rule.get("id")}


def catalogue_schema(catalogue: Path, template_name: str = "bindings.json") -> str | None:
    """Версия названного контракта, объявленная САМИМ каталогом.

    Берётся из заготовки ``templates/<имя>``: это единственное место, где
    контракт публикует свою версию машиночитаемо. Нет файла или версии —
    ``None``: «прочитать нечем» и «прочитали плохое» — разные исходы.

    Args:
        catalogue: Клон каталога правил.
        template_name: Имя заготовки: ``bindings.json`` — формат ответа
            потребителя, ``proposals.json`` — формат предложения. Умолчание
            историческое: до правила 164 контракт здесь знали ровно один.
    """
    template = catalogue / "templates" / template_name
    if not template.exists():
        return None
    try:
        version = json.loads(template.read_text(encoding="utf-8")).get("schema")
    except json.JSONDecodeError:
        return None
    return str(version) if version else None


def neighbour_holds(catalogue: Path, rule_ids: list[str]) -> dict[str, list[tuple[str, str, str]]]:
    """Чем названные правила держатся у СОСЕДЕЙ по тому же своду.

    Правило 162 каталога: прежде чем строить механизм правилу, у которого его
    нет, смотрят, чем оно держится у тех, кто отвечает по тому же каталогу.
    Ответ соседа не приказ — он называет того, кто уже платил за этот механизм.

    Список собирается машиной из ``export/where.json``, то есть из уже собранных
    ответов, а не походом по чужим репозиториям: у окна нет ни прав, ни причин
    туда ходить.

    Returns:
        Номер правила → список (репозиторий, механизм, адрес). Пусто —
        законный ответ: сосед мог не отвечать или держать так же ничем.
    """
    export = catalogue / "export" / "where.json"
    if not export.exists():
        return {}
    try:
        consumers = json.loads(export.read_text(encoding="utf-8")).get("consumers") or []
    except json.JSONDecodeError:
        return {}

    found: dict[str, list[tuple[str, str, str]]] = {}
    for rule_id in rule_ids:
        for consumer in consumers:
            if not isinstance(consumer, dict) or consumer.get("repo") == PROJECT:
                continue
            held = (consumer.get("holds") or {}).get(rule_id)
            if not isinstance(held, dict):
                continue
            mechanism = str(held.get("mechanism") or "")
            if mechanism in {"", "none"}:
                continue
            found.setdefault(rule_id, []).append(
                (str(consumer.get("repo")), mechanism, str(held.get("where") or ""))
            )
    return found


def contract_drift(data: dict[str, Any], catalogue: Path) -> list[str]:
    """Разошлась ли наша версия контракта с той, что публикует каталог.

    Прежняя проверка сравнивала ``schema`` из нашего файла с нашей же
    константой: обе стороны принадлежали потребителю, поэтому подъём версии у
    издателя она не могла заметить в принципе, а текст отказа при этом
    утверждал «контракт каталога сегодня 1.0», ни разу в каталог не заглянув.

    Цена измерена: контракт стал 1.1, и это была не косметика — слово
    ``process-step`` раскололось на ``pipeline``/``document``/``none``, а к
    ``where`` добавилось требование разрешимого адреса. Записи остались
    формально валидными и продолжали проходить гейт, хотя 52 ответа из 153
    были сформулированы словом, которое каталог больше не сводит ни к одному
    уровню. Заметили это не проверкой, а вопросом владельца (issue #1400).

    Отсюда и текст находки: смена версии — повод перечитать **ответы**, а не
    только формат.
    """
    published = catalogue_schema(catalogue)
    if published is None:
        return []
    ours = str(data.get("schema") or "")
    if ours == published:
        return []
    return [
        f"контракт потребителя у каталога — {published}, у нас — {ours or 'не объявлен'}. "
        "Это не переименование поля: вместе с версией меняется ЗНАЧЕНИЕ ответов, "
        "и формальная валидность это переживает. Перечитать нужно ответы по всем "
        "правилам, а не только схему файла"
    ]


def version_subjects(*, root: Path | None = None) -> list[str]:
    """Каждый наш номер версии говорит, ЧЕГО он версия (правило 164).

    Ключ ``schema`` стоит в обоих файлах `.rules/` и ещё в двух выгрузках
    каталога, а предметов у него четыре: выгрузка правил (1.2), ответ
    потребителя (1.1), предложение (1.0), сводка потребителей (1.0). Один ключ
    на четыре предмета означает, что рано или поздно номер одного окажется
    вписан в поле другого, — и обе стороны останутся валидными: ошибка не
    ломается, а меняет смысл.

    Проверяется два факта на файл: номер тот, которого требует контракт, и
    рядом стоит :data:`SUBJECT_KEY`, называющий предмет. Отсутствующий файл
    находкой не считается — у `.rules/proposals.json` это законное «канал не
    подключён», а не порча формата.

    Args:
        root: Корень репозитория; ``None`` — свой собственный.

    Returns:
        Список нарушений; пустой — чисто.
    """
    base = root if root is not None else _ROOT
    problems: list[str] = []

    for relative, template_name, expected in CONTRACT_FILES:
        path = base / relative
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{relative}: не разбирается ({exc})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{relative}: не объект")
            continue

        declared = str(data.get("schema") or "")
        if declared != expected:
            problems.append(
                f"{relative}: schema={declared or 'не объявлена'!r} — контракт "
                f"templates/{template_name} сегодня {expected}. Номера здесь разные и "
                "двигаются независимо: номер одного контракта в поле другого остаётся "
                "валидным и потому незаметен"
            )

        subject = str(data.get(SUBJECT_KEY) or "").strip()
        if not subject:
            problems.append(
                f"{relative}: рядом с `schema` нет `{SUBJECT_KEY}` — номер не говорит, "
                "чего он версия. Оговорка в отдельном документе не помогает тому, кто "
                "копирует строку: предмет называется в точке чтения"
            )

    return problems


def proposal_drift(catalogue: Path, *, root: Path | None = None) -> list[str]:
    """Разошлась ли версия контракта ПРЕДЛОЖЕНИЯ с той, что публикует каталог.

    Ответу потребителя такую сверку дал :func:`contract_drift`, а предложению
    не давал никто: `.rules/proposals.json` нёс номер, который не сверялся ни с
    чем. Это ровно тот случай, из которого выросло правило 164 — второй
    независимо версионируемый артефакт, оставленный без адресата.

    Args:
        catalogue: Клон каталога правил.
        root: Корень репозитория; ``None`` — свой собственный.

    Returns:
        Список находок; пустой — сошлось либо читать нечем.
    """
    base = root if root is not None else _ROOT
    path = base / ".rules" / "proposals.json"
    if not path.exists():
        return []

    published = catalogue_schema(catalogue, "proposals.json")
    if published is None:
        return []

    try:
        ours = str(json.loads(path.read_text(encoding="utf-8")).get("schema") or "")
    except json.JSONDecodeError:
        return []

    if ours == published:
        return []
    return [
        f"контракт предложения у каталога — {published}, у нас — {ours or 'не объявлен'}. "
        "Предложение и ответ версионируются независимо, поэтому подъём одного о другом "
        "ничего не говорит: перечитать нужно поля предложения"
    ]


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если ответ проекта сходится с контрактом; иначе 1."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, help="клон каталога: проверить полноту ответа")
    args = parser.parse_args(argv)

    if not BINDINGS.exists():
        print(f"FAIL: {BINDINGS} не найден — проект каталогу не отвечает вовсе.")
        return 1

    try:
        data = json.loads(BINDINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: .rules/bindings.json не разбирается ({exc}).")
        return 1

    problems = binding_violations(data)
    problems.extend(version_subjects())

    if args.catalogue is not None:
        try:
            expected = _export_ids(args.catalogue)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"FAIL: {exc}")
            return 1
        problems.extend(contract_drift(data, args.catalogue))
        problems.extend(proposal_drift(args.catalogue))
        answered = set(data.get("rules") or {})
        missing = sorted(expected - answered, key=int)
        if missing:
            problems.append(
                f"нет ответа по {len(missing)} правил(ам): {', '.join(missing[:10])}"
                + ("…" if len(missing) > 10 else "")
                + " — ответ нужен по каждому, иначе нерассмотренное просто исчезает"
            )
        stale = sorted(answered - expected, key=lambda item: int(item) if item.isdigit() else 0)
        if stale:
            problems.append(f"ответ на несуществующие правила: {', '.join(stale)}")

    if args.catalogue is not None:
        unheld_ids = [
            rule_id
            for rule_id, raw in sorted((data.get("rules") or {}).items())
            if isinstance(raw, dict)
            and raw.get("status") == "active"
            and raw.get("mechanism") == "none"
        ]
        for rule_id, neighbours in neighbour_holds(args.catalogue, unheld_ids).items():
            print(f"\nправило {rule_id} не держится ничем — у соседей по своду оно закрыто:")
            for repo, mechanism, where in neighbours:
                print(f"  {repo} — {mechanism}: {where[:160]}")
            print("  Приём переносится — повторите его; нет — причина остаётся в ответе.")

    unheld, total = unheld_count(data)
    if problems:
        print("FAIL: ответ каталогу правил разошёлся с контрактом:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if unheld > UNHELD_BUDGET:
        print(
            f"FAIL: не обеспечено ничем — {unheld} правил(а) при бюджете {UNHELD_BUDGET}.\n"
            "Правило без механизма обязано быть закрыто гейтом, замечено конвейером "
            "либо записано документом (mechanism + разрешимый адрес в where), иначе оно "
            "действует ровно до тех пор, пока о нём помнит окно. Бюджет опускают "
            "починкой, а не правкой числа."
        )
        return 1

    print(f"Ответ каталогу: {total} правил(а), не обеспечено ничем — {unheld}.")
    print(f"Бюджет — {UNHELD_BUDGET}: правило без механизма записывается документом.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
