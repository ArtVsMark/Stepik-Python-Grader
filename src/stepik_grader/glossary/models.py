"""models.py — типизированные модели локального глоссария (issue #126).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Здесь живут две доменные сущности локального knowledge-модуля глоссария
(дизайн — [`docs/web-mvp.md § Контракты данных`](../../../docs/web-mvp.md)):

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
from typing import Any, Literal

__all__ = [
    "CardStatus",
    "CardKind",
    "MissingKind",
    "MissingStatus",
    "GlossaryCard",
    "GlossaryMissingEntry",
]

# Жизненный цикл карточки: new (обнаружена) → draft (наполняется) →
# ready (готова к показу/экспорту) → exported (выгружена наружу).
CardStatus = Literal["new", "draft", "ready", "exported"]
CardKind = Literal["exception", "function", "construct", "term"]

# Очередь пополнения: элемент живёт как new/draft, пока не станет GlossaryCard.
MissingKind = Literal["function", "exception", "construct"]
MissingStatus = Literal["new", "draft"]

_CARD_STATUSES: frozenset[str] = frozenset({"new", "draft", "ready", "exported"})
_CARD_KINDS: frozenset[str] = frozenset({"exception", "function", "construct", "term"})


def _as_str_list(value: Any) -> list[str]:
    """Привести значение поля JSON к списку строк (терпимо к None/строке)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    raise ValueError(f"Ожидался список строк, получено: {value!r}")


@dataclass
class GlossaryCard:
    """Карточка локального глоссария (термин/исключение/функция/конструкция).

    Обязательные поля — ``id`` и ``title``; остальное опционально и
    расширяет компактный ``core/glossary.py`` (см. docs/web-mvp.md). ``aliases``,
    ``keywords`` и ``tags`` используются провайдером для поиска.
    """

    id: str
    title: str
    kind: CardKind = "term"
    summary: str = ""  # однострочное пояснение (RU); синоним hint из core/glossary
    body: str = ""  # расширенное описание (Markdown), опционально
    status: CardStatus = "draft"
    url: str = ""  # ссылка во внешний Glossary-Python (куда карточка экспортируется)
    section: str = ""  # раздел глоссария (напр. «Исключения»)
    aliases: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)  # id связанных карточек
    related_errors: list[str] = field(default_factory=list)  # коды/имена ошибок

    @property
    def search_terms(self) -> list[str]:
        """Все строки, по которым карточка находится поиском (lower-case)."""
        terms = [self.id, self.title, *self.aliases, *self.keywords, *self.tags]
        return [t.lower() for t in terms if t]

    def matches(self, query: str) -> bool:
        """True, если ``query`` (подстрока, без регистра) есть в search_terms."""
        needle = query.strip().lower()
        if not needle:
            return False
        return any(needle in term for term in self.search_terms)

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

        return cls(
            id=card_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            summary=str(data.get("summary", data.get("hint", ""))),
            body=str(data.get("body", "")),
            status=status,  # type: ignore[arg-type]
            url=str(data.get("url", "")),
            section=str(data.get("section", "")),
            aliases=_as_str_list(data.get("aliases")),
            keywords=_as_str_list(data.get("keywords")),
            tags=_as_str_list(data.get("tags")),
            examples=_as_str_list(data.get("examples")),
            related=_as_str_list(data.get("related")),
            related_errors=_as_str_list(data.get("related_errors")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать карточку в JSON-совместимый dict (стабильный порядок)."""
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "summary": self.summary,
            "body": self.body,
            "status": self.status,
            "url": self.url,
            "section": self.section,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "tags": list(self.tags),
            "examples": list(self.examples),
            "related": list(self.related),
            "related_errors": list(self.related_errors),
        }


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlossaryMissingEntry:
        """Собрать элемент очереди из JSON-объекта."""
        concept = str(data.get("concept", "")).strip()
        if not concept:
            raise ValueError("Элемент очереди без обязательного поля 'concept'")
        verdict = data.get("verdict")
        return cls(
            concept=concept,
            kind=str(data.get("kind", "function")),  # type: ignore[arg-type]
            status=str(data.get("status", "new")),  # type: ignore[arg-type]
            reason=str(data.get("reason", "")),
            snippet=str(data.get("snippet", "")),
            seen_in=_as_str_list(data.get("seen_in")),
            suggested_tags=_as_str_list(data.get("suggested_tags")),
            verdict=None if verdict is None else str(verdict),
            first_seen=str(data.get("first_seen", "")),
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
        }
