"""error_glossary.py — единая RE-подсказка из общей базы карточек (issue #356).

Архитектурный слой: Application-facing helper поверх двух источников карточек.

Единый резолвер подсказки по тексту ошибки (вердикт RE) для CLI
(``core/reporter.py``) и web (``web/viewmodels.py``): по имени исключения из
трейсбека ищет карточку в комплектной JSON-базе (``glossary/data/``, богатый
контент, ~140 исключений) и добирает пустые поля из компактной карты
(``core/glossary.py``, ~28). Так CLI и web показывают одни и те же карточки для
покрытых исключений, а раздел «Глоссарий» (та же bundled-база) остаётся
консистентным по deep-link ``#/glossary/<anchor>``.

Инварианты (issue #356):
- Компактный ``core/glossary.py`` остаётся leaf-модулем (без project-импортов);
  этот модуль — не leaf, он и связывает два источника.
- Провайдер JSON-базы грузится ЛЕНИВО (импорт внутри функции) и кешируется по
  mtime — RE в CLI не платит за парсинг ~1.2 МБ, пока подсказка не понадобится.
- Любая ошибка чтения базы — graceful: тихий откат на компактную карту.
- ``glossary/`` не импортирует ``core/`` — ребро ``core/error_glossary →
  glossary/`` ацикличное.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stepik_grader.core.glossary import (
    exception_name_from_error,
    lookup,
)

if TYPE_CHECKING:
    from stepik_grader.glossary.models import GlossaryCard

__all__ = ["ErrorHint", "resolve_error_hint"]


@dataclass(frozen=True)
class ErrorHint:
    """Унифицированная RE-подсказка независимо от источника карточки.

    Ссылки наружу подсказка не несёт (issue #684): единственный адрес карточки —
    ``anchor`` своего глоссария (deep-link ``#/glossary/<anchor>``).
    """

    exception: str  # имя класса / заголовок карточки
    hint: str  # однострочное пояснение (summary/hint)
    anchor: str  # id/anchor для deep-link «#/glossary/<anchor>»


# Ленивый кеш индекса bundled-карточек {id -> GlossaryCard} по каталогу,
# инвалидируемый по mtime его файлов (тот же приём, что web/glossary_adapter).
_INDEX_CACHE: dict[str, tuple[float, dict[str, GlossaryCard]]] = {}


def _bundled_index() -> dict[str, GlossaryCard]:
    """Индекс комплектной базы ``{card.id -> GlossaryCard}`` (лениво, с кешем).

    Пустой dict — если каталога нет/он пуст/битый: вызывающий тихо откатится на
    компактную карту.
    """
    from stepik_grader.glossary.json_provider import (
        BUNDLED_GLOSSARY_DIR,
        GlossaryError,
        JsonGlossaryProvider,
    )

    if not BUNDLED_GLOSSARY_DIR.is_dir():
        return {}
    files = sorted(BUNDLED_GLOSSARY_DIR.glob("*.json"))
    if not files:
        return {}
    try:
        sig = max(f.stat().st_mtime for f in files)
    except OSError:
        return {}
    key = str(BUNDLED_GLOSSARY_DIR)
    hit = _INDEX_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        cards = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()
    except (GlossaryError, OSError):
        return {}
    index = {card.id: card for card in cards}
    _INDEX_CACHE[key] = (sig, index)
    return index


# Карточки, у которых вид не проставлен, судим по имени: у исключений оно
# кончается на error/exception (issue #965).
_EXCEPTION_ID_SUFFIXES = ("error", "exception")


def _is_exception_card(card: GlossaryCard) -> bool:
    """Карточка описывает ИСКЛЮЧЕНИЕ, а не термин или функцию (issue #965).

    Индекс строится из всех ~1350 карточек каталога, а имя берётся из последней
    строки stderr до двоеточия — то есть из строки, которую пишет проверяемый
    код. ``sys.exit("input: неверный формат")`` печатает её без трейсбека, и
    резолвер выдавал студенту карточку функции ``input()`` как «объяснение
    падения». Вид карточки такие совпадения отсекает.

    ``kind`` по умолчанию — ``"term"`` (см. ``glossary.models``), поэтому
    непроставленный вид неотличим от настоящего термина; для него остаётся
    запасное правило по имени, чтобы карточка исключения не потерялась из-за
    незаполненного поля. Явные ``function``/``construct`` отвергаются всегда.
    """
    if card.kind == "exception":
        return True
    return card.kind == "term" and card.id.endswith(_EXCEPTION_ID_SUFFIXES)


def resolve_error_hint(error_text: str) -> ErrorHint | None:
    """RE-подсказка по тексту ошибки из общей базы, или ``None``.

    Приоритет — богатая карточка комплектной базы (по ``id == имя исключения в
    нижнем регистре``); пустой её ``summary`` (некоторые bundled-карточки
    исключений — заглушки) добирается из компактной карты, чтобы подсказка не
    деградировала. ``None``, если исключение не выделилось из трейсбека или его
    нет ни в одном источнике.

    Карточка не-исключения игнорируется (issue #965, см.
    :func:`_is_exception_card`) — тогда работает откат на компактную карту, в
    которой только исключения и есть.
    """
    name = exception_name_from_error(error_text)
    if not name:
        return None
    card = _bundled_index().get(name.lower())
    if card is not None and not _is_exception_card(card):
        card = None
    entry = lookup(name)  # компактная карта (может быть None)
    if card is None and entry is None:
        return None
    if card is not None:
        exception = card.title or name
        hint = card.summary or (entry.hint if entry is not None else "")
        return ErrorHint(exception=exception, hint=hint, anchor=card.id)
    # Только компактная карта.
    assert entry is not None
    return ErrorHint(exception=entry.exception, hint=entry.hint, anchor=entry.anchor)
