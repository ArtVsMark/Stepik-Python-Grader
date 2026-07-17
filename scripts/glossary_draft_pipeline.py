#!/usr/bin/env python
"""glossary_draft_pipeline.py — полуавтоматический конвейер черновиков (issue #438).

Следующий шаг после ``generate_draft_cards.py`` (голый черновик из интроспекции,
issue #328): собрать карточку по шаблону волны **B1** (RU+EN summary + примеры),
**автоматически проверить примеры прогоном** («# → результат», практика #371) и
выдать diff против базы для ручного ревью — ничего не мержится автоматически.

**Без реальной модели (пока).** Генерация текста/примеров — за сменным
провайдером ``DraftProvider``; дефолт ``OfflineDraftProvider`` работает
**офлайн, без LLM** (EN-summary из docstring, каркас B1). Реальный BYOK-провайдер
(облако/ollama на ``requests``, эпик AI #434/#435) — сменная точка расширения,
**не** подключается здесь и не добавляет runtime-зависимостей: сам конвейер —
dev-инструмент из ``scripts/`` на чистом stdlib. Ценность, не зависящая от
модели, — движок валидации примеров и diff-ревью: они работают уже сейчас.

Режимы:

    # аудит примеров существующих карточек (прогон + сверка «# → …»)
    python scripts/glossary_draft_pipeline.py check \
        --base src/stepik_grader/glossary/data

    # предложить B1-черновик qualname (офлайн-каркас или контент из файла),
    # проверить его примеры, показать review-diff против базы (dry-run):
    python scripts/glossary_draft_pipeline.py propose --qualname str.rjust
    python scripts/glossary_draft_pipeline.py propose --qualname str.rjust \
        --content-file draft.json --write review-drafts.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Скрипт в scripts/ (не на sys.path пакета) — добавим src/ и сам scripts/ для
# импортов (переиспользуем интроспекцию generate_draft_cards).
_SCRIPTS = Path(__file__).resolve().parent
_SRC = _SCRIPTS.parent / "src"
for _p in (_SRC, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import generate_draft_cards as _gen  # noqa: E402

from stepik_grader.glossary.json_provider import (  # noqa: E402
    BUNDLED_GLOSSARY_DIR,
    JsonGlossaryProvider,
)
from stepik_grader.glossary.models import GlossaryCard  # noqa: E402
from stepik_grader.glossary.stdlib_inventory import (  # noqa: E402
    StdlibItem,
    build_stdlib_inventory,
)

__all__ = [
    "DraftProvider",
    "ExampleReport",
    "OfflineDraftProvider",
    "ProposedContent",
    "build_b1_draft",
    "compare_expected_actual",
    "extract_expected",
    "main",
    "review_diff",
    "run_check",
    "run_propose",
    "split_code_and_expected",
    "validate_examples",
]

# Маркер ожидаемого результата в примере: "# → …" (стрелка U+2192) либо ASCII
# "# -> …". Практика #371: демонстрационный вывод рядом с кодом.
_EXPECT_RE = re.compile(r"#\s*(?:→|->)\s*(.*)$")
# Хвостовая человеко-пометка после значения: "… (список как элемент)".
_TRAILING_NOTE_RE = re.compile(r"^(?P<value>.*\S)\s+\([^()]*\)$")

# Секунд на прогон одного примера (dev-инструмент, снимок против зависаний).
_DEFAULT_TIMEOUT = 10.0


def split_code_and_expected(line: str) -> tuple[str, str | None]:
    """Разбить строку примера на ``(код, ожидаемый_вывод|None)``.

    Ожидаемый вывод — текст после ``# → `` / ``# -> ``; если маркера нет,
    возвращается ``None``. Код возвращается как есть (включая любой inline-хвост
    до маркера) — он остаётся исполнимым, т.к. ``#``-комментарий инертен.
    """
    match = _EXPECT_RE.search(line)
    if match is None:
        return line, None
    return line, match.group(1).strip()


def extract_expected(examples: list[str]) -> list[str]:
    """Список ожидаемых значений (в порядке появления ``# → …`` в примерах)."""
    expected: list[str] = []
    for line in examples:
        _, want = split_code_and_expected(line)
        if want is not None:
            expected.append(want)
    return expected


def compare_expected_actual(expected: str, actual: str) -> bool:
    """Сверить ожидаемый вывод с фактической строкой stdout (терпимо).

    Терпимость к трём легитимным формам записи (#371):
    - **аппроксимация** ``…...`` — префиксное совпадение до многоточия
      (``3.14159...`` ≈ ``3.141592653589793``);
    - **хвостовая пометка** ``значение (комментарий)`` — сравнивается только
      значение (``None (всегда None)`` ↔ ``None``);
    - обрамляющие пробелы.
    """
    want = expected.strip()
    got = actual.strip()
    if want == got:
        return True
    if "..." in want and got.startswith(want.split("...", 1)[0].rstrip()):
        return True
    note = _TRAILING_NOTE_RE.match(want)
    if note is not None:
        value = note.group("value").strip()
        if value == got:
            return True
        if "..." in value and got.startswith(value.split("...", 1)[0].rstrip()):
            return True
    return False


@dataclass
class ExampleReport:
    """Итог валидации набора примеров одной карточки.

    ``status``:
    - ``ok`` — все ожидаемые ``# → …`` совпали с фактическим выводом;
    - ``mismatch`` — хотя бы одно ожидание разошлось;
    - ``error`` — скрипт исполнился с ошибкой времени выполнения (raise/timeout);
    - ``unverifiable`` — нельзя сверить прогоном: нет ``# → …``; примеры не
      компилируются как единый скрипт (многострочный блок хранится построчно без
      отступов — типично для legacy-карточек); либо число ожиданий не совпало с
      числом строк вывода.
    """

    status: str
    detail: str = ""
    pairs: list[tuple[str, str]] = field(default_factory=list)  # (expected, actual)


def _run_snippet(script: str, timeout: float) -> tuple[bool, str, str]:
    """Исполнить сниппет в изолированном subprocess.

    Возвращает ``(ok, stdout, err)``: ``ok`` — код завершился 0; ``err`` —
    краткая причина сбоя (stderr/таймаут) для отчёта. Сеть не нужна примерам
    stdlib; ``-I`` — изолированный режим (без user-site/env).
    """
    with tempfile.TemporaryDirectory(prefix="glossary-ex-") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"timeout > {timeout:g}s"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return False, proc.stdout, tail[-1] if tail else f"exit code {proc.returncode}"
    return True, proc.stdout, ""


def validate_examples(examples: list[str], *, timeout: float = _DEFAULT_TIMEOUT) -> ExampleReport:
    """Проверить примеры прогоном и сверкой ``# → …`` с фактическим stdout.

    Примеры объединяются в один скрипт (общее пространство имён, как
    последовательность операторов) и исполняются. Ожидаемые значения
    сопоставляются со строками вывода **по порядку**; при расхождении числа —
    ``unverifiable`` (честно: нельзя однозначно выровнять), а не тихое смещение.
    """
    if not examples:
        return ExampleReport("unverifiable", "нет примеров")
    expected = extract_expected(examples)
    if not expected:
        return ExampleReport("unverifiable", "нет маркеров '# → …'")

    script = "\n".join(examples)
    try:
        compile(script, "<example>", "exec")
    except SyntaxError as exc:
        # Не единый исполнимый скрипт (обычно построчно хранимый блок без
        # отступов) — сверить прогоном нельзя, это не runtime-ошибка кода.
        return ExampleReport("unverifiable", f"не компилируется: {type(exc).__name__}")
    ok, stdout, err = _run_snippet(script, timeout)
    if not ok:
        return ExampleReport("error", err)

    actual_lines = stdout.splitlines()
    if len(actual_lines) != len(expected):
        return ExampleReport(
            "unverifiable",
            f"строк вывода {len(actual_lines)} ≠ ожиданий {len(expected)}",
        )

    pairs = list(zip(expected, actual_lines, strict=True))
    bad = [(e, a) for e, a in pairs if not compare_expected_actual(e, a)]
    if bad:
        detail = "; ".join(f"ждали {e!r}, получили {a!r}" for e, a in bad[:3])
        return ExampleReport("mismatch", detail, pairs)
    return ExampleReport("ok", f"сверено {len(pairs)}", pairs)


@dataclass
class ProposedContent:
    """Предложенный контент карточки (то, что заполнил бы человек/модель)."""

    summary: str = ""
    summary_en: str = ""
    examples: list[str] = field(default_factory=list)


class DraftProvider(Protocol):
    """Источник предложенного контента для B1-черновика (сменная точка).

    Дефолт — ``OfflineDraftProvider`` (без модели). Реальный BYOK-LLM
    (эпик AI #434/#435) реализует тот же протокол, подключается **opt-in** и не
    входит в этот dev-инструмент.
    """

    def propose(self, item: StdlibItem) -> ProposedContent:
        """Вернуть предложенные summary (RU/EN) и примеры для сущности."""
        ...


class OfflineDraftProvider:
    """Провайдер без LLM: EN-summary из docstring, RU-summary/примеры — слоты.

    Детерминирован и офлайн. Примеры **не** выдумываются (для этого нужна
    модель) — их вписывает человек/BYOK-провайдер; конвейер их затем валидирует.
    Опционально принимает ``overrides`` (напр. контент из ``--content-file``),
    имитирующие выход модели.
    """

    def __init__(self, overrides: ProposedContent | None = None) -> None:
        self._overrides = overrides

    def propose(self, item: StdlibItem) -> ProposedContent:
        """EN-summary из первого предложения docstring; RU — пусто (под ревью)."""
        if self._overrides is not None:
            return self._overrides
        obj = _gen.resolve_object(item)
        doc = ""
        if obj is not None:
            import inspect

            doc = (inspect.getdoc(obj) or "").split("\n\n", 1)[0].strip()
        summary_en = doc.split(". ", 1)[0].strip().rstrip(".") if doc else ""
        return ProposedContent(summary="", summary_en=summary_en, examples=[])


def build_b1_draft(item: StdlibItem, content: ProposedContent) -> GlossaryCard:
    """Собрать ``GlossaryCard(status="draft")`` по шаблону волны B1.

    Каркас (id/kind/syntax/body/docs_url/section) — из интроспекции
    ``generate_draft_cards`` (единый источник, без дубля правил); поверх — B1-поля
    ``summary``/``summary_en``/``examples`` из ``content``. Тег ``b1-pipeline``
    метит карточки, прошедшие этот конвейер, отдельно от голых ``autodraft``.
    """
    base = _gen.draft_card(item)
    base.summary = content.summary
    base.summary_en = content.summary_en
    base.examples = list(content.examples)
    base.tags = sorted({*base.tags, "b1-pipeline"})
    return base


def review_diff(new_card: GlossaryCard, base_dir: Path) -> str:
    """Unified-diff предложенной карточки против её версии в базе (или пустой).

    Для ручного ревью: показывает, что изменится, **ничего не записывая**. Если
    в базе карточки с таким ``id`` нет — diff от пустого (полностью новая).
    """
    provider = JsonGlossaryProvider.from_directory(base_dir)
    old = next((c for c in provider.all() if c.id == new_card.id), None)
    old_text = json.dumps(old.to_dict(), ensure_ascii=False, indent=2).splitlines() if old else []
    new_text = json.dumps(new_card.to_dict(), ensure_ascii=False, indent=2).splitlines()
    label = new_card.id
    diff = difflib.unified_diff(
        old_text,
        new_text,
        fromfile=f"base/{label}" if old else "base/<новая>",
        tofile=f"proposed/{label}",
        lineterm="",
    )
    return "\n".join(diff)


def _item_for_qualname(qualname: str) -> StdlibItem | None:
    """Найти ``StdlibItem`` инвентаря по полному qualname (или None)."""
    for item in build_stdlib_inventory():
        if item.qualname == qualname:
            return item
    return None


def run_check(base_dir: Path, *, only_ready: bool = True, timeout: float = _DEFAULT_TIMEOUT) -> int:
    """Аудит примеров карточек базы: прогон + сверка ``# → …``. Печать сводки.

    Возвращает число карточек со статусом ``mismatch``/``error`` (для CI/exit
    можно трактовать как «требуют внимания»). ``unverifiable`` и ``ok`` —
    не проблема (первое — нечего/нельзя сверить, второе — сошлось).
    """
    provider = JsonGlossaryProvider.from_directory(base_dir)
    cards = [c for c in provider.all() if c.examples and (not only_ready or c.status == "ready")]
    counts = {"ok": 0, "mismatch": 0, "error": 0, "unverifiable": 0}
    flagged: list[tuple[str, ExampleReport]] = []
    for card in sorted(cards, key=lambda c: c.id):
        report = validate_examples(card.examples, timeout=timeout)
        counts[report.status] += 1
        if report.status in ("mismatch", "error"):
            flagged.append((card.id, report))

    print(f"Проверено карточек с примерами: {len(cards)}")
    print(
        f"  ok={counts['ok']}  mismatch={counts['mismatch']}  "
        f"error={counts['error']}  unverifiable={counts['unverifiable']}"
    )
    for card_id, report in flagged:
        print(f"  [{report.status}] {card_id}: {report.detail}")
    return counts["mismatch"] + counts["error"]


def run_propose(
    qualname: str,
    *,
    base_dir: Path,
    content_file: Path | None = None,
    out_file: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> int:
    """Предложить B1-черновик для ``qualname``: валидация примеров + review-diff.

    ``content_file`` (JSON ``{summary, summary_en, examples}``) имитирует выход
    модели/человека; без него — офлайн-каркас (пустые RU-summary/примеры).
    Запись — только при ``out_file`` и **только** в отдельный draft-файл
    (``status=draft``), никогда в ready-базу и никогда автоматически.
    """
    item = _item_for_qualname(qualname)
    if item is None:
        print(f"В инвентаре stdlib нет '{qualname}'", file=sys.stderr)
        return 2

    overrides: ProposedContent | None = None
    if content_file is not None:
        data = json.loads(content_file.read_text(encoding="utf-8"))
        overrides = ProposedContent(
            summary=str(data.get("summary", "")),
            summary_en=str(data.get("summary_en", "")),
            examples=[str(x) for x in data.get("examples", [])],
        )
    provider: DraftProvider = OfflineDraftProvider(overrides)
    content = provider.propose(item)
    card = build_b1_draft(item, content)

    report = validate_examples(card.examples, timeout=timeout)
    print(f"Валидация примеров: [{report.status}] {report.detail}")
    print("--- review diff (ничего не записано) ---")
    print(review_diff(card, base_dir) or "(изменений нет)")

    if out_file is not None:
        if report.status in ("mismatch", "error"):
            print(
                f"\nНе записано в {out_file}: примеры не прошли валидацию "
                f"([{report.status}]). Почини примеры и повтори.",
                file=sys.stderr,
            )
            return 1
        _append_draft(card, out_file)
        print(f"\nЗаписан черновик (status=draft) в {out_file} — под ручное ревью.")
    return 0


def _append_draft(card: GlossaryCard, out_file: Path) -> None:
    """Добавить/обновить черновик в отдельном draft-файле (не ready-база)."""
    cards: dict[str, GlossaryCard] = {}
    if out_file.exists():
        for existing in JsonGlossaryProvider.from_file(out_file).all():
            cards[existing.id] = existing
    cards[card.id] = card
    ordered = sorted(cards.values(), key=lambda c: c.id)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps([c.to_dict() for c in ordered], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: подкоманды ``check`` (аудит примеров) и ``propose`` (B1-черновик)."""
    parser = argparse.ArgumentParser(
        description="Полуавтоматический конвейер черновиков глоссария (issue #438)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Аудит примеров карточек базы прогоном")
    p_check.add_argument("--base", type=Path, default=BUNDLED_GLOSSARY_DIR, help="Каталог базы")
    p_check.add_argument(
        "--all-statuses",
        action="store_true",
        help="Проверять карточки любого статуса (по умолчанию — только ready)",
    )
    p_check.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT, help="Секунд на пример")

    p_prop = sub.add_parser("propose", help="Предложить B1-черновик qualname")
    p_prop.add_argument("--qualname", required=True, help="Полный qualname, напр. str.rjust")
    p_prop.add_argument("--base", type=Path, default=BUNDLED_GLOSSARY_DIR, help="Каталог базы")
    p_prop.add_argument(
        "--content-file", type=Path, default=None, help="JSON с summary/summary_en/examples"
    )
    p_prop.add_argument(
        "--write", type=Path, default=None, dest="out_file", help="Файл черновиков (не ready-база)"
    )
    p_prop.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT, help="Секунд на пример")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        if not args.base.is_dir():
            parser.error(f"База не найдена: {args.base}")
        run_check(args.base, only_ready=not args.all_statuses, timeout=args.timeout)
        return 0
    if not args.base.is_dir():
        parser.error(f"База не найдена: {args.base}")
    if args.content_file is not None and not args.content_file.is_file():
        parser.error(f"Контент-файл не найден: {args.content_file}")
    return run_propose(
        args.qualname,
        base_dir=args.base,
        content_file=args.content_file,
        out_file=args.out_file,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
