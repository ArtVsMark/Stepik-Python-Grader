"""ai_grounding.py — retrieval-заземление AI-подсказки из офлайн-глоссария (issue #544).

Эпик E3. По концептам из кода решения (``scan_code_concepts`` — AST-скан без
исполнения) достаёт top-k релевантных карточек комплектного глоссария и склеивает
их ``title``/``summary`` в текст для AI-промпта (``FailureContext.grounding``,
рендерится в ``ai_hints._build_user_prompt``). Это поднимает потолок качества и
снижает галлюцинации на логических WA: LLM опирается на официальные описания того,
что студент реально использовал, а не только на голый код.

Офлайн и без внешних эмбеддингов (инвариант «3 runtime-зависимости»): матч —
точный по ``id`` карточки (или хвосту qualname), поиск идёт по индексу комплектной
базы. Заземление ОПЦИОНАЛЬНО и деградирует к плоскому промпту: пустой результат
(нет концептов / нет карточек / битая база) → ``""`` и промпт без секции.

Ребро ``core → glossary`` разрывается ЛЕНИВЫМ импортом внутри функций (как в
``core/error_glossary._bundled_index``): на load-time рёбер к ``glossary/`` нет,
DAG остаётся ацикличным (``glossary/`` не импортирует ``core/``, ADR-0011).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stepik_grader.glossary.models import GlossaryCard

__all__ = ["retrieve_grounding"]

_DEFAULT_K = 3

# issue #812 (VIS-03): имя исключения из последней строки трейсбека. Карточка
# самого исключения — самый релевантный материал для объяснения RE, но в
# заземление она не попадала вовсе: концепты берутся из AST, а `IndexError` в
# коде не написан.
_EXC_LINE_RE = re.compile(
    r"^(?:[\w.]+\.)?(\w*(?:Error|Exception|Warning))\b",
    re.MULTILINE,
)

# Ленивый кеш индекса комплектной базы {id -> GlossaryCard}, инвалидируемый по
# mtime её файлов — тот же приём, что core/error_glossary._INDEX_CACHE.
_INDEX_CACHE: dict[str, tuple[float, dict[str, GlossaryCard]]] = {}


def _index_from_cards(cards: list[GlossaryCard]) -> dict[str, GlossaryCard]:
    """Индекс ``{ключ -> card}``: полный ``id`` (приоритет) + хвост qualname.

    Детектор кода (``scan_code_concepts``) даёт ГОЛЫЕ имена — ``append`` для метода
    ``list.append``, ``sorted`` для функции; карточки методов лежат под
    qualname-``id`` (``list.append``). Индексируем и по хвосту qualname, чтобы
    голый концепт нашёл карточку. Полный ``id`` имеет приоритет (``setdefault`` не
    даёт хвосту другой карточки затереть точный ``id``).
    """
    index: dict[str, GlossaryCard] = {}
    for card in cards:
        index.setdefault(card.id, card)
    for card in cards:
        index.setdefault(card.id.rsplit(".", 1)[-1], card)
    return index


def _bundled_index() -> dict[str, GlossaryCard]:
    """Индекс комплектной базы ``{id -> GlossaryCard}`` (лениво, с mtime-кешем).

    Пустой dict — если каталога нет/он пуст/битый (вызывающий тихо деградирует к
    плоскому промпту). Ленивый импорт ``glossary/`` — чтобы на load-time не было
    ребра ``core → glossary`` (DAG ацикличен).
    """
    try:
        from stepik_grader.glossary.json_provider import (
            BUNDLED_GLOSSARY_DIR,
            GlossaryError,
            JsonGlossaryProvider,
        )
    except ImportError:
        return {}

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
    index = _index_from_cards(cards)
    _INDEX_CACHE[key] = (sig, index)
    return index


def _ranked_concepts(code: str, error: str, scan: object) -> list[str]:
    """Концепты кода в порядке близости к падению (issue #812, ``VIS-03``).

    Три уровня, внутри уровня — порядок обхода AST:

    1. имя исключения из трейсбека (в коде его нет, а карточка по нему —
       самое прямое объяснение RE);
    2. концепты, чьё имя встречается в тексте ошибки (``sorted`` в строке
       падения важнее, чем ``input`` из первой строки решения);
    3. остальные.

    ``scan`` передаётся параметром, а не импортируется здесь: ленивый импорт
    ``glossary/`` уже сделан вызывающим, и повторять его незачем.
    """
    concepts = list(scan(code))  # type: ignore[operator]
    if not error:
        return concepts

    ranked: list[str] = []
    match = _EXC_LINE_RE.search(error)
    if match:
        ranked.append(match.group(1))

    lowered = error.lower()
    mentioned = [c for c in concepts if c.lower() in lowered]
    rest = [c for c in concepts if c not in mentioned]
    return ranked + mentioned + rest


def retrieve_grounding(
    code: str,
    *,
    lang: str = "ru",
    k: int | None = None,
    cards: list[GlossaryCard] | None = None,
    error: str = "",
) -> str:
    """Карточки глоссария, релевантные ПАДЕНИЮ, склеенные для промпта.

    Извлекает концепты из ``code`` (``scan_code_concepts`` — AST, без исполнения),
    матчит их на карточки комплектной базы (точный ``id``/хвост), берёт ``k``
    уникальных ``ready``-карточек и склеивает ``title — summary`` (в ``lang``).
    Пустая строка — если концептов/совпадений нет или база недоступна (промпт
    деградирует к плоскому). Никогда не бросает.

    issue #812 (``VIS-03``): порядок — по близости к падению, а не по обходу AST.
    Прежняя реализация брала первые ``k`` совпавших концептов, поэтому на
    решении, которое начинается с ``input()``/``print()``, а падает на срезе, в
    промпт уезжали карточки ввода-вывода. Проверено прогоном: заземление
    состояло из ``int``, ``input()`` и ``re.split()`` при ``IndexError`` на
    индексации. Теперь первым идёт само исключение из трейсбека (карточка
    ``IndexError`` — самый релевантный материал для RE, а в коде это имя не
    написано вовсе), затем концепты, упомянутые в тексте ошибки, затем
    остальные в порядке AST.

    ``k`` по умолчанию — из ``CONFIG.ai_grounding_k``: сколько карточек влезает
    в промпт, зависит от модели и лимита токенов, поэтому это настройка, а не
    константа модуля.

    ``cards`` — явный список карточек вместо комплектной базы (для тестов/переопределения).
    """
    try:
        from stepik_grader.glossary.detector import scan_code_concepts
    except ImportError:
        return ""

    index = _index_from_cards(cards) if cards is not None else _bundled_index()
    if not index:
        return ""

    if k is None:
        from stepik_grader.config import CONFIG

        k = max(1, int(getattr(CONFIG, "ai_grounding_k", _DEFAULT_K)))

    picked: list[GlossaryCard] = []
    seen: set[str] = set()
    for concept in _ranked_concepts(code, error, scan_code_concepts):
        card = index.get(concept) or index.get(concept.lower())
        if card is None or card.id in seen or card.status != "ready":
            continue
        seen.add(card.id)
        picked.append(card)
        if len(picked) >= k:
            break

    if not picked:
        return ""
    lines: list[str] = []
    for card in picked:
        summary = card.localized("summary", lang) or ""
        lines.append(f"{card.title} — {summary}" if summary else card.title)
    return "\n".join(lines)
