"""models.py — типизированные модели локального глоссария (issue #126).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Здесь живут две доменные сущности локального knowledge-модуля глоссария
(реализация — [`docs/web-current.md § Контракты данных`](../../../docs/web-current.md)):

- ``GlossaryCard`` — карточка термина/исключения/функции/конструкции в
  локальной базе (истина хранится локально, экспорт во внешний
  Glossary-Python — односторонний).
- ``GlossaryMissingEntry`` — элемент очереди пополнения: обнаруженный
  ``MissingConceptDetector``'ом пробел (нет карточки под конструкцию/функцию).

Обе модели — обычные ``dataclass``'ы поверх stdlib (без внешних зависимостей),
с симметричными ``from_dict``/``to_dict`` для JSON-хранилища.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Literal

__all__ = [
    "CardKind",
    "CardStatus",
    "GlossaryCard",
    "GlossaryMissingEntry",
    "MissingKind",
    "MissingOrigin",
    "MissingStatus",
]

# Жизненный цикл карточки: new (обнаружена) → draft (наполняется) →
# ready (готова к показу/экспорту) → exported (выгружена наружу).
CardStatus = Literal["new", "draft", "ready", "exported"]
CardKind = Literal["exception", "function", "construct", "term"]

# Очередь пополнения: элемент живёт как new/draft, пока не станет GlossaryCard.
# "class" — только для source-driven записей (issue #197): классы stdlib
# (напр. dataclasses.dataclass, pathlib.Path), которые practice-детектор не
# производит (он даёт только function/exception/construct).
MissingKind = Literal["function", "exception", "construct", "class"]
MissingStatus = Literal["new", "draft"]
# Источник пробела: solution/error — practice-driven (MissingConceptDetector),
# stdlib_scan — source-driven (инвентаризатор stdlib, issue #196, + coverage.py #197).
MissingOrigin = Literal["solution", "error", "stdlib_scan"]

_CARD_STATUSES: frozenset[str] = frozenset({"new", "draft", "ready", "exported"})
_CARD_KINDS: frozenset[str] = frozenset({"exception", "function", "construct", "term"})
_MISSING_KINDS: frozenset[str] = frozenset({"function", "exception", "construct", "class"})
_MISSING_STATUSES: frozenset[str] = frozenset({"new", "draft"})
_MISSING_ORIGINS: frozenset[str] = frozenset({"solution", "error", "stdlib_scan"})

# Ранг «совпадения нет» (issue #685): заведомо больше любого ранга из
# ``GlossaryCard.match_rank``, поэтому несовпавшие карточки всегда в хвосте.
_NO_MATCH_RANK = 99


def _as_str_list(value: Any) -> list[str]:
    """Привести значение поля JSON к списку строк (терпимо к None/строке)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    raise ValueError(f"Ожидался список строк, получено: {value!r}")


def _parse_localized(value: Any) -> tuple[str, str]:
    """Разобрать текстовое поле карточки в пару ``(ru, en)`` (issue #363).

    Двуязычный глоссарий поверх механизма ``?lang=`` (issue #264/#387): текстовые
    поля хранятся вложенным объектом ``{"ru": ..., "en": ...}``. Приём терпим к
    старому плоскому формату для обратной совместимости — строка трактуется как
    ``ru`` (``en`` пуст), ``None`` → ``("", "")``.
    """
    if value is None:
        return "", ""
    if isinstance(value, dict):
        return str(value.get("ru", "")), str(value.get("en", ""))
    return str(value), ""


@dataclass
class GlossaryCard:
    """Карточка локального глоссария (термин/исключение/функция/конструкция).

    Обязательные поля — ``id`` и ``title``; остальное опционально и
    расширяет компактный ``core/glossary.py`` (см. docs/web-current.md). ``aliases``,
    ``keywords`` и ``tags`` используются провайдером для поиска.
    """

    id: str
    title: str
    kind: CardKind = "term"
    summary: str = ""  # однострочное пояснение (RU); синоним hint из core/glossary
    body: str = ""  # расширенное описание (Markdown, RU), опционально
    syntax: str = ""  # сигнатура/шаблон использования, напр. "sorted(iterable, *, key=None)" (#325)
    status: CardStatus = "draft"
    url: str = ""  # ссылка во внешний Glossary-Python (куда карточка экспортируется)
    docs_url: str = ""  # ссылка на официальную документацию docs.python.org (issue #325)
    version: str = ""  # мин. версия Python, если релевантно (issue #325), напр. "3.10"
    section: str = ""  # раздел глоссария (напр. «Исключения»)
    subcat: str = ""  # подкатегория внутри section (issue #325)
    aliases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)  # id связанных карточек
    related_errors: list[str] = field(default_factory=list)  # коды/имена ошибок
    # EN-переводы текстовых полей (issue #363). Плоские RU-атрибуты выше
    # остаются источником по умолчанию (обратная совместимость всех читателей
    # ``card.summary``); в JSON summary/body сериализуются вложенным {ru,en},
    # а web-API сплющивает их по ?lang= через ``to_api_dict``/``localized``.
    summary_en: str = ""
    body_en: str = ""

    @cached_property
    def search_terms(self) -> list[str]:
        """Все строки, по которым карточка находится поиском (lower-case).

        ``cached_property`` (issue #404): термины строятся один раз на карточку и
        переиспользуются на каждом ``matches``/``known_terms``/``queue_code_gaps``
        — раньше каждый такой вызов заново приводил id/title/aliases/keywords/tags
        к нижнему регистру. Карточки после загрузки иммутабельны (см.
        ``web/glossary_adapter._all_cards`` — общий read-only список), поэтому кеш
        не устаревает; при гонке потоков ThreadingHTTPServer пересчёт идемпотентен
        (Python 3.12 ``cached_property`` — без общего лока)."""
        terms = [self.id, self.title, *self.aliases, *self.keywords, *self.tags]
        return [t.lower() for t in terms if t]

    @cached_property
    def full_text_terms(self) -> list[str]:
        """``search_terms`` плюс полнотекстовые поля, lower-case.

        issue #536: поиск по карточке ищет и в её тексте, не только в
        id/title/aliases/keywords/tags. issue #685: охват расширен на ``syntax``
        и ``examples`` — ученик ищет по куску сигнатуры (``maxsplit``,
        ``key=None``) или по фрагменту примера, а раньше такой запрос не находил
        карточку, хотя строка в ней есть. Держится отдельно от ``search_terms``,
        потому что те питают ``known_terms``/coverage и детектор пробелов — туда
        текстовые поля попадать НЕ должны (иначе концепт ложно считался бы
        «покрытым» по любому упоминанию в тексте). Тот же ``cached_property``-
        контракт иммутабельной карточки, что и у ``search_terms``.
        """
        extra = (self.summary, self.body, self.summary_en, self.body_en, self.syntax)
        return [
            *self.search_terms,
            *(text.lower() for text in extra if text),
            *(example.lower() for example in self.examples if example),
        ]

    def matches(self, query: str) -> bool:
        """True, если ``query`` (подстрока, без регистра) есть в тексте карточки.

        Полнотекстовый поиск (issue #536/#685): id/title/aliases/keywords/tags И
        summary/body (RU+EN)/syntax/examples. ``search_terms`` (для coverage)
        остаётся узким.
        """
        needle = query.strip().lower()
        if not needle:
            return False
        return any(needle in term for term in self.full_text_terms)

    def match_rank(self, query: str) -> int:
        """Качество совпадения ``query`` с карточкой: чем меньше, тем релевантнее.

        Ранжирование выдачи поиска (issue #685): раньше совпадение по одной букве
        в середине ``body`` весило столько же, сколько точное попадание в
        ``title``, и карточка ``sorted`` тонула среди сотни упоминаний слова
        «сортировка». Шкала:

        ``0`` — точное совпадение id/title/alias; ``1`` — id/title начинается с
        запроса; ``2`` — запрос входит в id/title/alias; ``3`` — совпал
        keyword/tag; ``4`` — нашлось только в тексте (summary/body/syntax/
        examples); ``_NO_MATCH_RANK`` — не совпало вовсе (в т.ч. пустой запрос,
        тогда порядок задаёт тай-брейк вызывающей стороны).

        Именем карточки считается и «хвост» её id после точки: запрос ``split``
        обязан поднимать ``str.split``, а не ``bytearray.rsplit`` (у обоих
        ``split`` — лишь подстрока полного id).
        """
        needle = query.strip().lower()
        if not needle:
            return _NO_MATCH_RANK
        names = [self.id.lower(), self.title.lower(), self.id.rsplit(".", 1)[-1].lower()]
        aliases = [alias.lower() for alias in self.aliases if alias]
        if needle in names or needle in aliases:
            return 0
        if any(name.startswith(needle) for name in names):
            return 1
        if any(needle in term for term in (*names, *aliases)):
            return 2
        if any(needle in term.lower() for term in (*self.keywords, *self.tags) if term):
            return 3
        return 4 if self.matches(needle) else _NO_MATCH_RANK

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlossaryCard:
        """Собрать карточку из JSON-объекта с валидацией обязательных полей.

        Raises:
            ValueError: если нет ``id``/``title`` или ``kind``/``status``
                имеют недопустимое значение.
        """
        card_id = str(data.get("id", "")).strip()
        title = str(data.get("title", "")).strip()
        if not card_id:
            raise ValueError("Карточка глоссария без обязательного поля 'id'")
        if not title:
            raise ValueError(f"Карточка '{card_id}' без обязательного поля 'title'")

        kind = str(data.get("kind", "term"))
        if kind not in _CARD_KINDS:
            raise ValueError(
                f"Карточка '{card_id}': недопустимый kind={kind!r} "
                f"(ожидалось одно из {sorted(_CARD_KINDS)})"
            )
        status = str(data.get("status", "draft"))
        if status not in _CARD_STATUSES:
            raise ValueError(
                f"Карточка '{card_id}': недопустимый status={status!r} "
                f"(ожидалось одно из {sorted(_CARD_STATUSES)})"
            )

        # Текстовые поля — двуязычные (issue #363): вложенный {ru,en} или (старый
        # плоский формат) строка → ru. ``hint`` — legacy-алиас summary из схемы
        # Glossary-Python.
        summary_ru, summary_en = _parse_localized(data.get("summary", data.get("hint", "")))
        body_ru, body_en = _parse_localized(data.get("body", ""))

        return cls(
            id=card_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            summary=summary_ru,
            body=body_ru,
            syntax=str(data.get("syntax", "")),
            status=status,  # type: ignore[arg-type]
            url=str(data.get("url", "")),
            # ``docs`` — алиас ``docs_url`` для совместимости со схемой
            # Glossary-Python (как ``hint`` → ``summary``); ``version`` там
            # бывает null → нормализуем в "".
            docs_url=str(data.get("docs_url", data.get("docs", ""))),
            version=str(data.get("version") or ""),
            section=str(data.get("section", "")),
            subcat=str(data.get("subcat", "")),
            aliases=_as_str_list(data.get("aliases")),
            keywords=_as_str_list(data.get("keywords")),
            tags=_as_str_list(data.get("tags")),
            examples=_as_str_list(data.get("examples")),
            related=_as_str_list(data.get("related")),
            related_errors=_as_str_list(data.get("related_errors")),
            summary_en=summary_en,
            body_en=body_en,
        )

    def localized(self, field_name: str, lang: str = "ru") -> str:
        """Текст двуязычного поля (``summary``/``body``) по локали ``lang``.

        ``en`` — только если запрошен и непуст; иначе fallback на RU (плоский
        атрибут), тот же принцип graceful degradation, что у ``resolve_lang``.
        """
        ru = str(getattr(self, field_name))
        en = str(getattr(self, f"{field_name}_en"))
        return en if lang == "en" and en else ru

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать карточку в JSON-совместимый dict для хранилища.

        Текстовые поля ``summary``/``body`` — вложенным {ru,en} (issue #363);
        стабильный порядок ключей. Для web-API используй ``to_api_dict(lang)``,
        который сплющивает их в строку по локали.
        """
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "summary": {"ru": self.summary, "en": self.summary_en},
            "body": {"ru": self.body, "en": self.body_en},
            "syntax": self.syntax,
            "status": self.status,
            "url": self.url,
            "docs_url": self.docs_url,
            "version": self.version,
            "section": self.section,
            "subcat": self.subcat,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "tags": list(self.tags),
            "examples": list(self.examples),
            "related": list(self.related),
            "related_errors": list(self.related_errors),
        }

    def to_api_dict(self, lang: str = "ru") -> dict[str, Any]:
        """Представление карточки для web-API: ``summary``/``body`` — строки по ``lang``.

        Хранилищный ``to_dict()`` держит вложенный {ru,en}; web-API отдаёт текст
        уже выбранной локали (``?lang=``, issue #363), fallback на RU — так
        контракт полей ``summary``/``body`` остаётся строковым (issue #264).
        """
        data = self.to_dict()
        data["summary"] = self.localized("summary", lang)
        data["body"] = self.localized("body", lang)
        return data


@dataclass
class GlossaryMissingEntry:
    """Элемент очереди пополнения глоссария (обнаруженный пробел).

    Пишется ``MissingConceptDetector`` при обнаружении функции/конструкции/
    исключения, для которых нет карточки в локальной базе (журнал J7).
    """

    concept: str  # напр. "functools.reduce", "match/case", "RecursionError"
    kind: MissingKind = "function"
    status: MissingStatus = "new"
    reason: str = ""  # почему помечено (человекочитаемо)
    snippet: str = ""  # фрагмент кода/ошибки, где встретилось
    seen_in: list[str] = field(default_factory=list)  # источники (файлы решений)
    suggested_tags: list[str] = field(default_factory=list)
    verdict: str | None = None  # вердикт, если пробел найден из ошибки (RE/WA)
    first_seen: str = ""  # ISO-дата первого обнаружения
    origin: MissingOrigin = "solution"  # practice (solution/error) vs source-driven (stdlib_scan)
    module: str = ""  # stdlib-модуль происхождения (source-driven, issue #196/#197)
    qualname: str = ""  # полное квалифицированное имя (source-driven)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlossaryMissingEntry:
        """Собрать элемент очереди из JSON-объекта с валидацией kind/status/origin.

        Raises:
            ValueError: если нет ``concept`` или ``kind``/``status``/``origin``
                имеют недопустимое значение.
        """
        concept = str(data.get("concept", "")).strip()
        if not concept:
            raise ValueError("Элемент очереди без обязательного поля 'concept'")

        kind = str(data.get("kind", "function"))
        if kind not in _MISSING_KINDS:
            raise ValueError(
                f"Элемент очереди '{concept}': недопустимый kind={kind!r} "
                f"(ожидалось одно из {sorted(_MISSING_KINDS)})"
            )
        status = str(data.get("status", "new"))
        if status not in _MISSING_STATUSES:
            raise ValueError(
                f"Элемент очереди '{concept}': недопустимый status={status!r} "
                f"(ожидалось одно из {sorted(_MISSING_STATUSES)})"
            )
        origin = str(data.get("origin", "solution"))
        if origin not in _MISSING_ORIGINS:
            raise ValueError(
                f"Элемент очереди '{concept}': недопустимый origin={origin!r} "
                f"(ожидалось одно из {sorted(_MISSING_ORIGINS)})"
            )

        verdict = data.get("verdict")
        return cls(
            concept=concept,
            kind=kind,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            reason=str(data.get("reason", "")),
            snippet=str(data.get("snippet", "")),
            seen_in=_as_str_list(data.get("seen_in")),
            suggested_tags=_as_str_list(data.get("suggested_tags")),
            verdict=None if verdict is None else str(verdict),
            first_seen=str(data.get("first_seen", "")),
            origin=origin,  # type: ignore[arg-type]
            module=str(data.get("module", "")),
            qualname=str(data.get("qualname", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать элемент очереди в JSON-совместимый dict."""
        return {
            "concept": self.concept,
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "snippet": self.snippet,
            "seen_in": list(self.seen_in),
            "suggested_tags": list(self.suggested_tags),
            "verdict": self.verdict,
            "first_seen": self.first_seen,
            "origin": self.origin,
            "module": self.module,
            "qualname": self.qualname,
        }
