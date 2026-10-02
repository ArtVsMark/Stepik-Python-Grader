"""glossary_adapter.py — тонкий web-адаптер над локальной базой глоссария.

Архитектурный слой: Application/UI (web-адаптер), как и ``viewmodels.py``.
Никакой новой бизнес-логики поиска/хранения — только пас-through над уже
готовым ``JsonGlossaryProvider``/``load_missing_queue`` (issue #126) и
fallback на компактный ``core/glossary.py`` (~28 записей), когда локальная
JSON-база не настроена (``CONFIG.glossary_store is None``), — так раздел
«Глоссарий» не пустует на свежей установке (issue #125).

Что здесь есть и чего нет (issue #831, ARCH-06): доменные правила базы —
классификация разделов, EN-подписи, сортировки, приватность карточки
(``glossary/taxonomy.py``), индексы «концепция из кода → карточка»
(``glossary/lookup.py``) и наборы имён для сканера
(``glossary/stdlib_inventory.scanner_name_sets``) — живут в домене и доступны
любому потребителю, не только HTTP. Здесь остаётся web-специфичное: разрешение
источника с кешем по mtime (``mtime_cache.py``), сборка JSON-словарей ответа и
zero-config fallback на ``core/glossary`` — ребро на ``core/*``, которого в
``glossary/`` быть не должно (ADR-0011).
"""

from __future__ import annotations

import pathlib
from typing import Any, NamedTuple

from stepik_grader.config import CONFIG, get_config
from stepik_grader.core.glossary import all_entries
from stepik_grader.core.history import record_glossary_hit as record_hit
from stepik_grader.glossary.detector import MissingConceptDetector, scan_code_concepts
from stepik_grader.glossary.json_provider import (
    BUNDLED_GLOSSARY_DIR,
    GlossaryError,
    JsonGlossaryProvider,
    append_missing_entries,
    load_missing_queue,
)
from stepik_grader.glossary.lookup import (
    card_index,
    match_card,
    method_names_from_cards,
    name_concepts_from_cards,
)
from stepik_grader.glossary.models import GlossaryCard
from stepik_grader.glossary.stdlib_inventory import scanner_name_sets
from stepik_grader.glossary.taxonomy import (
    GROUPS,
    card_group,
    is_private_name,
    section_label,
    sort_cards,
)
from stepik_grader.mtime_cache import MtimeCache

__all__ = [
    "code_terms",
    "glossary_get",
    "glossary_missing",
    "glossary_search",
    "missing_queue_path",
    "queue_code_gaps",
    "record_glossary_hit",
]


def record_glossary_hit(
    card_id: str,
    *,
    db_path: pathlib.Path,
    failure_kind: str | None = None,
    error_class: str | None = None,
) -> bool:
    """Записать переход в карточку ИЗ ОШИБКИ прогона (issue #1220).

    Пас-through в ``core/history``: гейт согласия отработал выше, в роутере, —
    сюда путь к базе приходит уже разрешённым. Пишется только deep-link из
    разбора ошибки; открытие раздела «Глоссарий» руками не пишется, иначе
    метрика превратилась бы в «просмотрел N карточек» и перестала отвечать на
    вопрос, ради которого заведена: работает ли связка «упал → понял».

    Возвращает, легла ли запись: ``core/history`` best-effort и на битой базе
    молча вернёт ``None``.
    """
    return (
        record_hit(card_id, db_path=db_path, failure_kind=failure_kind, error_class=error_class)
        is not None
    )


def _fallback_cards() -> list[GlossaryCard]:
    """Компактный глоссарий (core/glossary.py) как GlossaryCard — zero-config."""
    return [
        GlossaryCard(
            id=entry.anchor,
            title=entry.exception,
            kind="exception",
            summary=entry.hint,
            status="ready",
        )
        for entry in all_entries()
    ]


# Кеш распарсенных карточек по источнику (issue #339): раньше каждый запрос к
# /api/glossary*, /api/code-terms заново читал и парсил всю бандл-базу (1.2 МБ
# JSON, ~1400 карточек). Теперь парсим один раз и держим в памяти, инвалидируя
# по mtime через общий mtime_cache.py.MtimeCache (issue #345 — тот же
# механизм переиспользует провайдер правил, не копипастя его; правка store
# подхватывается, read-only бандл-база после первой загрузки стабильна).
# Потребители карточки не мутируют (glossary_search/card_index строят новые
# списки), поэтому общий список безопасно шарить между запросами/потоками
# ThreadingHTTPServer — гонка на пересчёт идемпотентна.
_CARDS_CACHE: MtimeCache[list[GlossaryCard]] = MtimeCache()


class _GlossaryIndex(NamedTuple):
    """Производные индексы источника (issue #404) — считаются один раз на источник.

    ``by_id`` — ``id -> карточка`` для O(1) ``glossary_get`` (был O(n)-скан
    ``next(...)`` на каждый ``/api/glossary/{id}``); ``by_concept`` — индекс
    ``lookup.card_index`` (id/alias/«хвост» → карточка) для ``code_terms`` (был пересбор
    словаря со внутренним ``sorted()`` по ~1400 карточкам на каждый
    ``/api/code-terms``); ``method_names`` (issue #686) — имена методов, которые
    знает сама база (``lookup.method_names_from_cards``): ими сканер дополняет
    инвентарь встроенных типов, иначе ``Path.exists()`` остаётся незамеченным;
    ``name_concepts`` (issue #686) — bare-имена всех карточек
    (``lookup.name_concepts_from_cards``): по ним сканер ловит голые ссылки
    (``isinstance(x, int)``, ``x: Counter``), а не только вызовы.
    """

    by_id: dict[str, GlossaryCard]
    by_concept: dict[str, GlossaryCard]
    method_names: frozenset[str]
    name_concepts: frozenset[str]


# Производные индексы кешируются по ТОЙ ЖЕ сигнатуре источника (ключ + mtime
# файлов), что и _CARDS_CACHE, поэтому инвалидируются синхронно с карточками:
# правка store перечитывает и карточки, и индексы. Read-only, гонка на пересчёт
# идемпотентна — как и у _CARDS_CACHE (общий MtimeCache без лока).
_INDEX_CACHE: MtimeCache[_GlossaryIndex] = MtimeCache()


def _resolve_source(
    store_path: pathlib.Path | None,
) -> tuple[str, list[pathlib.Path], list[GlossaryCard]]:
    """``(cache_key, files, cards)`` для источника по приоритету: явный store →
    ``CONFIG.glossary_store`` → комплектная база (issue #326) → компактный
    fallback (``core/glossary.py``).

    ``cache_key``/``files`` — сигнатура источника (issue #404): по ней и
    ``_CARDS_CACHE``, и ``_INDEX_CACHE`` инвалидируются вместе. Для fallback —
    ``("fallback", [])``: ``mtime_signature([]) == (0, 0.0)`` стабильна, а статические
    карточки ``core/glossary.py`` не меняются, поэтому производный индекс тоже
    кешируется корректно.
    """
    path = store_path if store_path is not None else CONFIG.glossary_store
    if path:
        p = pathlib.Path(path)
        cards = _CARDS_CACHE.get_or_load(
            f"store:{p}", [p], lambda: JsonGlossaryProvider.load(p).all(), on_error=GlossaryError
        )
        if cards is not None:
            return f"store:{p}", [p], cards
    if BUNDLED_GLOSSARY_DIR.is_dir() and any(BUNDLED_GLOSSARY_DIR.glob("*.json")):
        files = sorted(BUNDLED_GLOSSARY_DIR.glob("*.json"))
        cards = _CARDS_CACHE.get_or_load(
            "bundled",
            files,
            lambda: JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all(),
            on_error=GlossaryError,
        )
        if cards is not None:
            return "bundled", files, cards
    return "fallback", [], _fallback_cards()


def _all_cards(store_path: pathlib.Path | None) -> list[GlossaryCard]:
    """Карточки источника (комплектная база ~1400 карточек делает раздел
    «Глоссарий» полноценным на свежей установке; fallback на ~28 исключений —
    если каталог отсутствует/пуст/битый). Кешируется по mtime источника (#339)."""
    return _resolve_source(store_path)[2]


def _build_index(cards: list[GlossaryCard]) -> _GlossaryIndex:
    """Собрать оба производных индекса из карточек (id-first-wins, как прежний
    ``next(...)``-скан в ``glossary_get``)."""
    by_id: dict[str, GlossaryCard] = {}
    for card in cards:
        by_id.setdefault(card.id, card)
    return _GlossaryIndex(
        by_id=by_id,
        by_concept=card_index(cards),
        method_names=method_names_from_cards(cards),
        name_concepts=name_concepts_from_cards(cards),
    )


def _glossary_index(store_path: pathlib.Path | None) -> _GlossaryIndex:
    """Производные индексы источника с кешем по его mtime-сигнатуре (issue #404)."""
    key, files, cards = _resolve_source(store_path)
    index = _INDEX_CACHE.get_or_load(key, files, lambda: _build_index(cards))
    return index if index is not None else _build_index(cards)


def glossary_search(
    query: str,
    *,
    section: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    sort: str | None = None,
    group: str | None = None,
    lang: str = "ru",
    store_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Карточки, отфильтрованные по ``query`` и опциональным граням, отсортированные.

    - ``query`` — подстрока по search-терминам (пустой = без текстового фильтра);
    - ``section``/``kind`` — точное совпадение соответствующего поля (issue #329);
      разделы НЕ объединяются — «Списки (list)» и «Кортежи (tuple)» раздельно;
    - ``group`` (issue #685) — семейство разделов: ``modules`` (все разделы
      «Модуль X»), ``types`` (встроенные типы), ``syntax`` (конструкции языка),
      ``builtins`` (встроенные функции и исключения), ``io`` (ввод-вывод и
      файлы), ``algorithms``, ``other`` (не классифицированные разделы).
      Считается из ``section`` (``_card_group``), нового поля в карточках не
      требует; неизвестное значение игнорируется, как и неизвестный ``sort``;
    - ``status`` (issue #436) — по умолчанию (``None``) выдача ограничена
      ``ready`` (черновики автогенерации не шумят); ``"all"`` — показать все
      статусы; иное значение — точное совпадение (``draft``/``new``/…);
    - ``sort`` — ``az``/``section``/``version`` (иначе порядок источника);
    - ``lang`` — локаль ``?lang=`` (issue #363): ``summary``/``body`` отдаются
      строкой выбранного языка (fallback RU).

    Приватно-именованные АВТОДРАФТЫ (``_module``/``obj._attr``, см.
    ``_is_private_name``) скрыты из выдачи ученика ВСЕГДА, даже под явным
    ``?status=draft`` (issue #436 AC2). Фильтр применяется ТОЛЬКО к не-``ready``
    карточкам: рукописные/промотированные ``ready`` (включая легитимные
    dunder-slug OOP-карточки ``__add__``/``__len__`` и ``_missing_``) приватностью
    имени не прячутся — иначе они регрессионно исчезли бы из поиска. Фильтр живёт
    здесь, а не в ``_all_cards``, поэтому детектор/очередь
    (``code_terms``/``queue_code_gaps``) по-прежнему видят ПОЛНУЮ базу (AC3).
    """
    cards = _all_cards(store_path)
    cards = [c for c in cards if c.status == "ready" or not is_private_name(c.id)]
    if query.strip():
        cards = [c for c in cards if c.matches(query)]
    if section:
        cards = [c for c in cards if c.section == section]
    if kind:
        cards = [c for c in cards if c.kind == kind]
    if group in GROUPS:
        cards = [c for c in cards if card_group(c) == group]
    effective_status = status if status else "ready"
    if effective_status != "all":
        cards = [c for c in cards if c.status == effective_status]
    cards = sort_cards(cards, sort, query)
    # ``group``/``section_label`` — аддитивные поля ответа (issue #685): по
    # первому UI строит семейства и списки их разделов, второе — подпись раздела
    # на языке ``lang`` (само ``section`` остаётся серверным значением фильтра).
    return [
        {
            **c.to_api_dict(lang),
            "group": card_group(c),
            "section_label": section_label(c.section, lang),
        }
        for c in cards
    ]


def glossary_get(
    card_id: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """Карточка по id, либо None (адаптер отдаёт 404 в этом случае).

    ``lang`` — локаль ``?lang=`` для ``summary``/``body`` (issue #363, fallback RU)
    и для подписи раздела ``section_label`` (issue #685) — как в ``glossary_search``,
    чтобы deep-link на карточку показывал раздел на том же языке, что и список.
    """
    card = _glossary_index(store_path).by_id.get(card_id)  # issue #404: O(1) вместо O(n)
    if card is None:
        return None
    return {
        **card.to_api_dict(lang),
        "group": card_group(card),
        "section_label": section_label(card.section, lang),
    }


def code_terms(
    code: str, *, lang: str = "ru", store_path: pathlib.Path | None = None
) -> list[dict[str, Any]]:
    """Термины глоссария для концепций, найденных в ``code`` (issue #321/#322/#367/#686).

    Сканирует код (``scan_code_concepts`` — builtin'ы и методы из stdlib-
    инвентаря, вызовы stdlib с разворотом цепочки ``os.path.join``, синтаксические
    конструкции, исключения из ``raise``/``except`` и атрибуты модулей) и
    сопоставляет с карточками базы. Возвращает **все**
    распознанные концепции (а не только покрытые) в виде
    ``{id, title, summary, kind, has_card, confidence, snippet}``:
    покрытые несут данные карточки (``has_card=True``), непокрытые —
    сам концепт (``has_card=False``, панель рисует их приглушённо). Методы —
    ``confidence="low"`` (тип получателя статически неизвестен). Порядок:
    покрытые вперёд, затем по ``title``.

    issue #686: набор имён методов — инвентарь встроенных типов ПЛЮС имена,
    известные самой базе (``index.method_names``), поэтому в панель попадают и
    методы stdlib-классов (``Path.exists()``), которых инвентарь не знает; набор
    имён для голых ссылок (``detect_names``) — ``index.name_concepts`` вместе с
    инвентарными builtins, поэтому распознаётся любое имя с карточкой, а не
    только вызванное.
    """
    index_data = _glossary_index(store_path)  # issue #404: индекс кеширован по mtime
    inventory_builtins, methods = scanner_name_sets()
    concepts = scan_code_concepts(
        code,
        notable_builtins=inventory_builtins | index_data.name_concepts,
        methods=methods | index_data.method_names,
        detect_names=True,
    )
    if not concepts:
        return []
    index = index_data.by_concept
    seen_cards: set[str] = set()
    seen_concepts: set[str] = set()
    terms: list[dict[str, Any]] = []
    for concept, (kind, snippet) in concepts.items():
        confidence = "low" if kind == "method" else "high"
        card = match_card(concept, index)
        if card is not None:
            if card.id in seen_cards:
                continue
            seen_cards.add(card.id)
            terms.append(
                {
                    "id": card.id,
                    "title": card.title,
                    "summary": card.localized("summary", lang),
                    "kind": card.kind,
                    "has_card": True,
                    "confidence": confidence,
                    "snippet": snippet,
                }
            )
        elif concept not in seen_concepts:
            seen_concepts.add(concept)
            terms.append(
                {
                    "id": concept,
                    "title": concept,
                    "summary": "",
                    "kind": kind,
                    "has_card": False,
                    "confidence": confidence,
                    "snippet": snippet,
                }
            )
    terms.sort(key=lambda t: (not t["has_card"], t["title"].lower()))
    return terms


def missing_queue_path(workspace: pathlib.Path | None = None) -> pathlib.Path:
    """Путь к очереди пополнения относительно рабочей директории сервера.

    Дефолт ``CONFIG.glossary_missing_queue`` относительный
    (``.grader_glossary_missing.db``), а `pathlib.Path` резолвит такой путь от
    **cwd процесса**. Для веб-слоя это не то же самое, что ``--root``: сервер
    запускают из любой папки, и очередь оказывалась то там, то тут — то есть
    «Недостающее» показывало пустоту, а пополнение уходило мимо (issue #966,
    ADD-1-03; тот же класс, что #723 для secrets.json).

    Args:
        workspace: рабочая директория сервера; ``None`` — поведение как раньше.

    Returns:
        Абсолютный путь, если задан workspace и настройка относительна.
    """
    # Конфиг читается В МОМЕНТ ВЫЗОВА (`get_config()`), а не через связанный при
    # импорте `CONFIG`: иначе значение вмораживается и `override_config` — то
    # есть флаги CLI и настройки лаунчера — до этой функции не доходят.
    configured = pathlib.Path(get_config().glossary_missing_queue)
    if workspace is None or configured.is_absolute():
        return configured
    return workspace / configured


def queue_code_gaps(
    code: str,
    *,
    source: str = "",
    queue_path: pathlib.Path | None = None,
    store_path: pathlib.Path | None = None,
) -> None:
    """Practice-driven AST-канал (issue #322): пробелы кода → очередь «Недостающее».

    ``MissingConceptDetector.detect_from_code`` (узкий notable-набор — без
    повседневных ``print``/``len``) находит функции/конструкции без карточки в
    базе; ``append_missing_entries`` дедуплицирует по ``concept``. Best-effort и
    defensive (как ``_queue_missing_concept``): плохой/незаписываемый путь очереди
    не должен ронять эндпоинт. Вызывается на разовых ``{path}``-запросах (после
    прогона), не на debounce-редактировании.
    """
    path = queue_path if queue_path is not None else pathlib.Path(CONFIG.glossary_missing_queue)
    try:
        known = {term for card in _all_cards(store_path) for term in card.search_terms}
        entries = MissingConceptDetector().detect_from_code(code, known=known, source=source)
        if entries:
            append_missing_entries(path, entries)
    except (GlossaryError, OSError):
        pass


def glossary_missing(*, queue_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Очередь пополнения (J7) — пусто при отсутствующем/битом файле очереди."""
    path = queue_path if queue_path is not None else pathlib.Path(CONFIG.glossary_missing_queue)
    try:
        entries = load_missing_queue(path)
    except GlossaryError:
        return []
    return [e.to_dict() for e in entries]
