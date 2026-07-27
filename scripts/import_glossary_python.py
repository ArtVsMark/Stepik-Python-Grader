#!/usr/bin/env python
"""import_glossary_python.py — одноразовый импорт карточек из Glossary-Python.

Читает встроенный JSON (``<script id="glossary-data">``) из
``python_glossary.html`` внешнего проекта Glossary-Python и конвертирует его
карточки в ``GlossaryCard`` локальной базы (issue #326, эпик #316).

Одноразовая инициализация: после импорта источник истины — локальная база;
внешний проект отсюда не редактируется (CLAUDE.md § Связанный проект). Поток
контента дальше односторонний grader → витрина.

Запуск (сетевых обращений нет — путь к HTML передаётся аргументом):

    python scripts/import_glossary_python.py \
        --html /path/to/Glossary-Python/python_glossary.html \
        --out src/stepik_grader/glossary/data

Идемпотентно: один и тот же вход даёт побайтово одинаковый набор файлов
(детерминированная сортировка по ``id``; дедуп по ``id``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Скрипт лежит в scripts/ (не на sys.path пакета) — добавим src/ для импорта.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stepik_grader.glossary.models import GlossaryCard  # noqa: E402

__all__ = [
    "external_to_card",
    "extract_external_cards",
    "main",
    "run_import",
]

_DATA_RE = re.compile(r'<script[^>]*id="glossary-data"[^>]*>(.*?)</script>', re.DOTALL)

# Группы-конструкции языка → kind="construct". Остальное определяется по
# признакам: исключение (cg=exc/группа «Исключения»), вызов (name кончается
# на ")") → function, иначе → term. Эвристика best-effort: важные kind
# (exception/function) детектятся надёжно, синтаксис — приблизительно.
_CONSTRUCT_GROUPS = frozenset({"Циклы", "Условный оператор"})


def extract_external_cards(html: str) -> list[dict[str, Any]]:
    """Достать список карточек из embedded ``<script id="glossary-data">``."""
    match = _DATA_RE.search(html)
    if match is None:
        raise ValueError('В HTML нет <script id="glossary-data">')
    data = json.loads(match.group(1))
    if not isinstance(data, list):
        raise ValueError("glossary-data: ожидался JSON-список карточек")
    return data


def _infer_kind(ext: dict[str, Any]) -> str:
    """Определить ``kind`` карточки из группы/имени/цветовой группы (best-effort)."""
    group = str(ext.get("group", ""))
    name = str(ext.get("name", "")).strip()
    if str(ext.get("cg", "")) == "exc" or group == "Исключения":
        return "exception"
    if name.endswith(")"):  # input(), len(), list.append() — вызовы
        return "function"
    if group in _CONSTRUCT_GROUPS:
        return "construct"
    return "term"


def _split_examples(raw: Any) -> list[str]:
    """Строку примеров (строки через ``\\n``) разбить в список непустых строк."""
    if not raw:
        return []
    text = raw if isinstance(raw, str) else "\n".join(str(item) for item in raw)
    return [line for line in (ln.strip() for ln in text.split("\n")) if line]


def _norm_version(raw: Any) -> str:
    """``null`` (нет привязки к версии) → ``""``; иначе строка как есть."""
    return "" if raw is None else str(raw).strip()


def external_to_card(ext: dict[str, Any]) -> GlossaryCard:
    """Сконвертировать одну внешнюю карточку в ``GlossaryCard``.

    Маппинг схем: ``name→title``, ``group→section``, ``subcat→subcat``,
    ``description→summary``, ``syntax→syntax``, ``examples→examples`` (split по
    ``\\n``), ``version→version`` (null→``""``), ``docs→docs_url``. ``cg`` —
    только для kind-эвристики (сохраняется тегом для фильтров/поиска). ``id``
    исключений приводится к нижнему регистру: это конвенция анкоров
    ``core/glossary.py`` (сохраняет связь ошибка→карточка при deep-link).
    Обратной ссылки на витрину карточка не несёт (issue #684): поток
    односторонний, а DOM-анкор витрины устаревает вместе с её копией контента.
    """
    ext_id = str(ext.get("id", "")).strip()
    kind = _infer_kind(ext)
    card_id = ext_id.lower() if kind == "exception" else ext_id
    cg = str(ext.get("cg", "")).strip()
    return GlossaryCard.from_dict(
        {
            "id": card_id,
            "title": str(ext.get("name", ext_id)),
            "kind": kind,
            "summary": str(ext.get("description", "")),
            "syntax": str(ext.get("syntax", "")),
            "status": "ready",
            "docs_url": str(ext.get("docs", "")),
            "version": _norm_version(ext.get("version")),
            "section": str(ext.get("group", "")),
            "subcat": str(ext.get("subcat", "")),
            "examples": _split_examples(ext.get("examples")),
            "tags": [cg] if cg else [],
        }
    )


def run_import(html_path: Path, out_dir: Path) -> dict[str, int]:
    """Импортировать карточки из ``html_path`` в ``out_dir`` (по файлу на ``cg``).

    Возвращает ``{cg: количество}``. Дедуп по ``id`` (первое вхождение
    выигрывает). Файлы пишутся детерминированно (сортировка по ``id``).
    """
    externals = extract_external_cards(html_path.read_text(encoding="utf-8"))
    buckets: dict[str, list[GlossaryCard]] = {}
    seen: set[str] = set()
    for ext in externals:
        card = external_to_card(ext)
        if card.id in seen:
            continue
        seen.add(card.id)
        cg = str(ext.get("cg") or "misc")
        buckets.setdefault(cg, []).append(card)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for cg, cards in sorted(buckets.items()):
        cards.sort(key=lambda c: c.id)
        payload = [c.to_dict() for c in cards]
        (out_dir / f"{cg}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        counts[cg] = len(cards)
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--html <path> --out <dir>`` → импорт карточек, печать сводки."""
    parser = argparse.ArgumentParser(
        description="Одноразовый импорт карточек из Glossary-Python в локальную базу."
    )
    parser.add_argument("--html", required=True, type=Path, help="Путь к python_glossary.html")
    parser.add_argument(
        "--out",
        type=Path,
        default=_SRC / "stepik_grader" / "glossary" / "data",
        help="Каталог назначения (по умолчанию — комплектная база пакета)",
    )
    args = parser.parse_args(argv)
    if not args.html.is_file():
        parser.error(f"HTML не найден: {args.html}")

    counts = run_import(args.html, args.out)
    total = sum(counts.values())
    print(f"Импортировано {total} карточек в {args.out}:")
    for cg, num in sorted(counts.items()):
        print(f"  {cg}: {num}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
