#!/usr/bin/env python3
"""scripts/audit_glossary_cards.py — аудит двуязычности карточек глоссария (issue #684).

Проблема: 525 карточек локальной базы (``src/stepik_grader/glossary/data/*.json``)
— весь старый импорт из Glossary-Python (у всех заполнен ``url``) — имеют
непустой RU ``summary`` и пустой ``summary_en``. Из-за пустого EN метод
``GlossaryCard.localized`` молча откатывается на RU (graceful degradation
``?lang=``, issue #363/#387), и в web карточка «не переключается» на английский.
Снаружи это неотличимо от «перевод есть, просто совпал» — нужен машинный счётчик.

Скрипт — **ratchet** («храповик») EN-покрытия: считает карточки без
``summary_en`` и падает, если их стало больше ``MAX_CARDS_WITHOUT_EN``. Планка
опускается по мере перевода (цель волны — 0) и никогда не поднимается обратно;
тест ``tests/test_glossary_card_audit.py`` держит её в CI.

``body``/``body_en`` осознанно НЕ проверяются: в базе 0 карточек с непустым
``body`` (расширенное описание нигде не заполнено), поэтому «пробел» там —
фикция, а не долг перевода.

Запуск::

    python scripts/audit_glossary_cards.py           # отчёт + exit 1 при нарушении
    python scripts/audit_glossary_cards.py --list    # плюс id карточек без EN
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Iterable

from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider
from stepik_grader.glossary.models import GlossaryCard

__all__ = [
    "MAX_CARDS_WITHOUT_EN",
    "cards_missing_en",
    "load_cards",
    "load_cards_by_file",
    "main",
]

# Планка EN-ratchet: сколько карточек с RU-summary ещё допустимо иметь без
# summary_en. СНИЖАТЬ тем же PR, которым добавляются переводы (issue #684);
# поднимать — нельзя. Исходное значение волны — 525 (весь импорт из
# Glossary-Python), цель — 0.
MAX_CARDS_WITHOUT_EN = 421


def load_cards_by_file(
    data_dir: pathlib.Path = BUNDLED_GLOSSARY_DIR,
) -> dict[str, list[GlossaryCard]]:
    """Карточки базы, сгруппированные по имени JSON-файла (отсортировано)."""
    return {
        json_file.name: JsonGlossaryProvider.from_file(json_file).all()
        for json_file in sorted(data_dir.glob("*.json"))
    }


def load_cards(data_dir: pathlib.Path = BUNDLED_GLOSSARY_DIR) -> list[GlossaryCard]:
    """Все карточки базы глоссария одним списком."""
    return JsonGlossaryProvider.load(data_dir).all()


def cards_missing_en(cards: Iterable[GlossaryCard]) -> list[GlossaryCard]:
    """Карточки с непустым RU ``summary``, но пустым ``summary_en``.

    Карточки без RU-текста вообще не считаются долгом перевода: переводить
    там нечего, ``localized`` в обеих локалях вернёт пустую строку.
    """
    return [card for card in cards if card.summary.strip() and not card.summary_en.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Аудит EN-покрытия карточек глоссария")
    parser.add_argument(
        "--cards",
        type=pathlib.Path,
        default=BUNDLED_GLOSSARY_DIR,
        help="директория с *.json карточками (по умолчанию — комплектная база)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_ids",
        help="перечислить id карточек без summary_en",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Напечатать отчёт EN-покрытия; 1 — планка ratchet превышена, иначе 0."""
    args = _build_arg_parser().parse_args(argv)
    by_file = load_cards_by_file(args.cards)

    total_missing = 0
    for name, cards in by_file.items():
        missing = cards_missing_en(cards)
        total_missing += len(missing)
        if missing:
            print(f"{name:15} без summary_en: {len(missing):4} из {len(cards):4}")
            if args.list_ids:
                for card in missing:
                    print(f"    {card.id}")

    print(f"\nВсего карточек без summary_en: {total_missing} (планка {MAX_CARDS_WITHOUT_EN})")
    if total_missing > MAX_CARDS_WITHOUT_EN:
        print(
            f"ОШИБКА: EN-покрытие деградировало — {total_missing} > {MAX_CARDS_WITHOUT_EN}. "
            "Заполни summary_en у новых карточек (issue #684).",
            file=sys.stderr,
        )
        return 1
    if total_missing < MAX_CARDS_WITHOUT_EN:
        print(
            f"Планка устарела: снизь MAX_CARDS_WITHOUT_EN до {total_missing} "
            f"в {pathlib.Path(__file__).name}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
