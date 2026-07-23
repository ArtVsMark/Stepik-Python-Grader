#!/usr/bin/env python3
"""scripts/audit_glossary_cards.py — ревизия карточек глоссария (issue #684).

Машинная сверка комплектной базы ``glossary/data/*.json`` по трём осям задачи
#684, «Как измерить» → отчёт-чеклист + жёсткие инварианты для CI/тестов:

1. **Структура (hard).** У каждой ``ready``-карточки есть минимально обязательный
   набор полей (``REQUIRED_READY_FIELDS``) — de-facto-набор, который уже держат
   все 1333 карточки; фиксируем его инвариантом, чтобы база не разъехалась.
2. **Матч детектором (hard).** Карточка-вызов должна быть «matcher-safe»: её
   чистое имя достижимо детектором пробелов (``detector._is_known``) — иначе
   ``«Функции в коде»`` и coverage ложно считают имя непокрытым. Проверяется в
   двух видах: мультифункциональные карточки, чей ``title`` перечисляет
   несколько имён через `` / `` (напр. ``os.getcwd() / os.chdir()``), — по
   КАЖДОМУ вызову (issue #684, гибридное решение: часть бандлов разбита на
   1-концепт-карточки, остальным добит ``keywords``), и одиночные
   ``kind="function"`` карточки, чей ``title`` — один вызов
   (``logging.debug()``, ``.iterdir()``) — по нему одному (PR #702).
3. **RU/EN (hard, ratchet).** Число карточек без ``summary_en`` не должно расти
   выше ``MAX_CARDS_WITHOUT_EN``. Отдельная волна #684 перевела все 525 карточек
   старого импорта из Glossary-Python, поэтому планка сведена к 0: теперь это
   жёсткий гейт — новая карточка с RU-``summary`` обязана нести и EN.

Загрузка — через штатный ``JsonGlossaryProvider`` (та же валидация, что в
рантайме), без самопального ``json.load``. Никаких внешних зависимостей.

Запуск::

    python scripts/audit_glossary_cards.py            # отчёт + exit 0/1
    python scripts/audit_glossary_cards.py --report   # только отчёт, exit 0
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider
from stepik_grader.glossary.models import GlossaryCard

__all__ = [
    "MAX_CARDS_WITHOUT_EN",
    "REQUIRED_READY_FIELDS",
    "cards_missing_en",
    "cards_missing_required_fields",
    "is_matcher_safe",
    "main",
    "multifunction_titles",
    "part_to_concept",
    "single_function_titles",
    "unsafe_multifunction_cards",
    "unsafe_single_function_cards",
]

# Минимально обязательный набор полей для status=ready (issue #684). Это de-facto
# набор, который уже на 100% держат все карточки базы; список фиксирует его как
# инвариант. ``body``/``aliases``/``keywords``/``version``/``related``/``url`` —
# осознанно опциональны (заполнены у меньшинства), поэтому не входят.
REQUIRED_READY_FIELDS: tuple[str, ...] = (
    "summary",  # summary.ru
    "syntax",
    "docs_url",
    "section",
    "subcat",
    "tags",  # >= 1
    "examples",  # >= 1
)

# Ratchet EN-полноты (issue #684). EN-волна переведена: все 525 карточек старого
# импорта из Glossary-Python получили summary_en, поэтому планка — 0. Значение
# только УМЕНЬШАЕТСЯ; расти ему нельзя (новые карточки — двуязычны by design),
# так что теперь ratchet работает как жёсткий гейт на каждую новую карточку.
MAX_CARDS_WITHOUT_EN = 0


def _is_detector_part(part: str) -> bool:
    """True, если часть title — вызов/dotted-путь, который эмитит детектор кода.

    Требуем matcher-safety только для того, что ``_CodeScanner`` реально
    производит как concept (issue #684): явный вызов ``name()`` ИЛИ dotted-путь
    ``mod.attr``. Голые ключевые слова/операторы (``try``, ``in``, ``await``,
    ``is``, ``match``) и одиночные имена классов (``IntEnum``, ``Flag``) детектор
    как function/method-concept не даёт — их не требуем (добивать им keyword
    незачем). ``Optional[X]``, ``X | None``, ``[::2]`` тоже отсекаются.
    """
    part = part.strip()
    if part.endswith("()"):
        return True
    # «Настоящая» точка (не только ведущая ``.method``): ``os.sep``, ``date.today``.
    return "." in part.lstrip(".")


def load_cards() -> list[GlossaryCard]:
    """Загрузить все карточки комплектной базы штатным провайдером."""
    return JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()


def _field_present(card: GlossaryCard, field: str) -> bool:
    """True, если поле карточки непусто (summary — по RU-ветке; списки — len>0)."""
    value = getattr(card, field)
    if isinstance(value, list):
        return len(value) > 0
    return bool(str(value or "").strip())


def cards_missing_required_fields(cards: list[GlossaryCard]) -> list[tuple[str, list[str]]]:
    """Список ``(card_id, [пропущенные поля])`` для ready-карточек с пробелами."""
    out: list[tuple[str, list[str]]] = []
    for card in cards:
        if card.status != "ready":
            continue
        missing = [f for f in REQUIRED_READY_FIELDS if not _field_present(card, f)]
        if missing:
            out.append((card.id, missing))
    return out


def cards_missing_en(cards: list[GlossaryCard]) -> list[str]:
    """id карточек с непустым RU-summary, но пустым ``summary_en`` (issue #363)."""
    return [c.id for c in cards if c.summary.strip() and not c.summary_en.strip()]


def multifunction_titles(cards: list[GlossaryCard]) -> list[GlossaryCard]:
    """Карточки, чей ``title`` перечисляет несколько имён через `` / ``."""
    return [c for c in cards if " / " in c.title]


def part_to_concept(part: str) -> str:
    """Свести часть title к concept'у детектора: снять ведущую ``.`` и хвостовые ``()``.

    ``.exists()`` → ``exists``; ``os.getcwd()`` → ``os.getcwd`` (dotted-путь
    детектор сохраняет); ``functools.lru_cache`` → ``functools.lru_cache``.
    """
    return part.strip().lstrip(".").removesuffix("()")


def is_matcher_safe(card: GlossaryCard, concept: str) -> bool:
    """True, если ``concept`` достижим для карточки логикой ``detector._is_known``.

    Держать в синхроне с ``glossary/detector.py::_is_known`` (issue #684): точное
    равенство concept'а любому search-терму карточки ИЛИ совпадение по «хвосту»
    после последней точки (``os.getcwd`` → ``getcwd``).
    """
    terms = set(card.search_terms)  # уже lower-case
    concept_lc = concept.lower()
    if concept_lc in terms:
        return True
    return concept_lc.rsplit(".", 1)[-1] in terms


def unsafe_multifunction_cards(cards: list[GlossaryCard]) -> list[tuple[str, list[str]]]:
    """``(card_id, [недостижимые concept'ы])`` для мультифункц. карточек без матча.

    Проверяются только callable-подобные части title (см. ``_is_detector_part``);
    не-callable части (``Optional[X]``, ``[::2]`` …) детектор не производит.
    """
    out: list[tuple[str, list[str]]] = []
    for card in multifunction_titles(cards):
        bad: list[str] = []
        for part in card.title.split(" / "):
            if not _is_detector_part(part):
                continue
            concept = part_to_concept(part)
            if not is_matcher_safe(card, concept):
                bad.append(concept)
        if bad:
            out.append((card.id, bad))
    return out


def single_function_titles(cards: list[GlossaryCard]) -> list[GlossaryCard]:
    """Одиночные ``kind="function"`` карточки, чей ``title`` — ровно один вызов.

    Бандлы (`` / ``) остаются за ``unsafe_multifunction_cards``. Title с пробелом
    внутри (``repr() vs str()``, ``for ... in reversed()``, «Очередь (queue)»)
    исключается намеренно: детектор такой concept не производит, а сама карточка
    описывает не одну функцию — это сигнал неверного ``kind`` (лечится
    ``term``/``construct``, PR #702), а не пробела в ``keywords``.
    """
    return [
        c
        for c in cards
        if c.kind == "function"
        and " / " not in c.title
        and " " not in c.title.strip()
        and _is_detector_part(c.title)
    ]


def unsafe_single_function_cards(cards: list[GlossaryCard]) -> list[tuple[str, str]]:
    """``(card_id, недостижимый concept)`` для одиночных карточек-функций (PR #702).

    Тот же баг matcher-safety, что у бандлов, но у карточки с одним вызовом:
    ``title`` ``logging.debug()``/``.iterdir()`` не даёт search-терма, равного
    concept'у детектора (``logging.debug``/``iterdir``), а ``id`` — слаг с дефисом.
    Лечится чистым именем в ``keywords``: dotted-форма для модульных функций,
    голое имя для методов.
    """
    out: list[tuple[str, str]] = []
    for card in single_function_titles(cards):
        concept = part_to_concept(card.title)
        if not is_matcher_safe(card, concept):
            out.append((card.id, concept))
    return out


def _print_report(cards: list[GlossaryCard]) -> None:
    """Печать отчёта-чеклиста (issue #684)."""
    n = len(cards)
    by_status = Counter(c.status for c in cards)
    by_kind = Counter(c.kind for c in cards)
    print(f"Карточек: {n}  |  статус: {dict(by_status)}  |  kind: {dict(by_kind)}\n")

    # Присутствие полей.
    print("Присутствие полей (non-empty), % от всех:")
    all_fields = (
        "summary",
        "body",
        "syntax",
        "docs_url",
        "url",
        "version",
        "section",
        "subcat",
        "aliases",
        "keywords",
        "tags",
        "examples",
    )
    for field in all_fields:
        present = sum(_field_present(c, field) for c in cards)
        mark = "*" if field in REQUIRED_READY_FIELDS else " "
        print(f"  {mark} {field:<12} {present:>5} {100 * present / n:>4.0f}%")
    print("  (* — обязательное для ready)\n")

    missing_en = cards_missing_en(cards)
    print(f"RU/EN: без summary_en — {len(missing_en)} (ratchet ≤ {MAX_CARDS_WITHOUT_EN})")
    multi = multifunction_titles(cards)
    unsafe = unsafe_multifunction_cards(cards)
    print(f"Мультифункц. (title c ' / '): {len(multi)}; из них matcher-unsafe: {len(unsafe)}")
    single = single_function_titles(cards)
    unsafe_single = unsafe_single_function_cards(cards)
    print(
        f"Одиночные карточки-вызовы: {len(single)}; из них matcher-unsafe: {len(unsafe_single)}\n"
    )


def _check_invariants(cards: list[GlossaryCard]) -> list[str]:
    """Собрать нарушения жёстких инвариантов (пустой список — всё чисто)."""
    errors: list[str] = []

    missing_fields = cards_missing_required_fields(cards)
    if missing_fields:
        errors.append(f"{len(missing_fields)} ready-карточек без обязательных полей:")
        for cid, fields in missing_fields[:20]:
            errors.append(f"    {cid}: нет {', '.join(fields)}")

    unsafe = unsafe_multifunction_cards(cards)
    if unsafe:
        errors.append(
            f"{len(unsafe)} мультифункц. карточек matcher-unsafe "
            "(добавь keywords с чистыми именами или разбей на 1-концепт-карточки):"
        )
        for cid, concepts in unsafe[:30]:
            errors.append(f"    {cid}: недостижимы {', '.join(concepts)}")

    unsafe_single = unsafe_single_function_cards(cards)
    if unsafe_single:
        errors.append(
            f"{len(unsafe_single)} одиночных карточек-вызовов matcher-unsafe "
            "(добавь чистое имя concept'а в keywords или поправь kind):"
        )
        for cid, concept in unsafe_single[:30]:
            errors.append(f"    {cid}: недостижим {concept}")

    missing_en = cards_missing_en(cards)
    if len(missing_en) > MAX_CARDS_WITHOUT_EN:
        errors.append(
            f"summary_en пуст у {len(missing_en)} карточек > ratchet "
            f"{MAX_CARDS_WITHOUT_EN} (issue #684): новые карточки должны быть двуязычными."
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    """Печать отчёта; при ``--report`` — только отчёт (exit 0), иначе + инварианты."""
    parser = argparse.ArgumentParser(description="Ревизия карточек глоссария (issue #684).")
    parser.add_argument(
        "--report", action="store_true", help="только отчёт, без проверки инвариантов (exit 0)"
    )
    args = parser.parse_args(argv)

    cards = load_cards()
    _print_report(cards)

    if args.report:
        return 0

    errors = _check_invariants(cards)
    if errors:
        print("FAIL: инварианты карточек глоссария нарушены (issue #684):")
        for e in errors:
            print(f"  - {e}" if not e.startswith("    ") else e)
        return 1

    print("OK: структура, matcher-safety и EN-ratchet — в норме.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
