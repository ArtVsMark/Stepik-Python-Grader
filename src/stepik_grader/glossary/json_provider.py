"""json_provider.py — загрузка/поиск локальной базы карточек глоссария (issue #126).

Архитектурный слой: Domain. Зависит только от ``glossary/models.py`` (stdlib),
не тянет ничего из ``core/`` — DAG остаётся ацикличным.

``JsonGlossaryProvider`` — JSON-first реализация абстракции ``GlossaryProvider``
(SQLite отложен, см. web-mvp.md). Читает карточки из одного JSON-файла или из
директории с ``*.json``, валидирует минимальные поля и предоставляет поиск/
выборку. Очередь пополнения (``GlossaryMissingEntry``) сериализуется в
отдельный JSON-файл — детектор пишет туда обнаруженные пробелы.

Принцип graceful degradation (как у кэша #56): битый/отсутствующий источник —
понятная ``GlossaryError``, а не падение всего грейдера; вызывающий код решает,
показать ошибку или продолжить с пустой базой.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Protocol, runtime_checkable

from .models import GlossaryCard, GlossaryMissingEntry

__all__ = [
    "GlossaryError",
    "GlossaryProvider",
    "JsonGlossaryProvider",
    "load_missing_queue",
    "save_missing_queue",
    "append_missing_entries",
]


class GlossaryError(ValueError):
    """Ошибка чтения/валидации локальной базы глоссария."""


@runtime_checkable
class GlossaryProvider(Protocol):
    """Абстракция источника карточек глоссария (JSON сейчас, SQLite позже)."""

    def get(self, card_id: str) -> GlossaryCard | None:
        """Вернуть карточку по id или None."""
        ...

    def all(self) -> list[GlossaryCard]:
        """Все карточки базы."""
        ...

    def search(self, query: str) -> list[GlossaryCard]:
        """Поиск по id/title/aliases/keywords/tags (подстрока, без регистра)."""
        ...

    def list_by_status(self, status: str) -> list[GlossaryCard]:
        """Карточки с заданным статусом жизненного цикла."""
        ...

    def list_by_tag(self, tag: str) -> list[GlossaryCard]:
        """Карточки, помеченные заданным тегом."""
        ...


def _iter_card_dicts(payload: Any, source: pathlib.Path) -> list[dict[str, Any]]:
    """Извлечь список card-dict'ов из JSON-корня (list или {"cards": [...]})."""
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = payload.get("cards", [])
    else:
        raise GlossaryError(f"{source}: ожидался список карточек или объект с 'cards'")
    if not isinstance(raw, list):
        raise GlossaryError(f"{source}: поле 'cards' должно быть списком")
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise GlossaryError(
                f"{source}: элемент карточки должен быть объектом, а не {type(item).__name__}"
            )
        result.append(item)
    return result


class JsonGlossaryProvider:
    """JSON-реализация ``GlossaryProvider``: карточки из файла или директории."""

    def __init__(self, cards: list[GlossaryCard]) -> None:
        self._cards: list[GlossaryCard] = list(cards)
        self._by_id: dict[str, GlossaryCard] = {}
        for card in self._cards:
            if card.id in self._by_id:
                raise GlossaryError(f"Дублирующийся id карточки: {card.id!r}")
            self._by_id[card.id] = card

    # -- конструкторы загрузки -------------------------------------------

    @classmethod
    def from_file(cls, path: pathlib.Path | str) -> JsonGlossaryProvider:
        """Загрузить карточки из одного JSON-файла."""
        file_path = pathlib.Path(path)
        return cls(cls._load_cards_from_file(file_path))

    @classmethod
    def from_directory(cls, path: pathlib.Path | str) -> JsonGlossaryProvider:
        """Загрузить карточки из всех ``*.json`` в директории (отсортировано)."""
        dir_path = pathlib.Path(path)
        if not dir_path.is_dir():
            raise GlossaryError(f"Директория глоссария не найдена: {dir_path}")
        cards: list[GlossaryCard] = []
        for json_file in sorted(dir_path.glob("*.json")):
            cards.extend(cls._load_cards_from_file(json_file))
        return cls(cards)

    @classmethod
    def load(cls, path: pathlib.Path | str) -> JsonGlossaryProvider:
        """Автоопределение: директория → from_directory, иначе from_file."""
        target = pathlib.Path(path)
        if target.is_dir():
            return cls.from_directory(target)
        return cls.from_file(target)

    @staticmethod
    def _load_cards_from_file(file_path: pathlib.Path) -> list[GlossaryCard]:
        if not file_path.exists():
            raise GlossaryError(f"Файл глоссария не найден: {file_path}")
        try:
            with open(file_path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            raise GlossaryError(f"{file_path}: невалидный JSON — {exc}") from exc
        cards: list[GlossaryCard] = []
        for raw in _iter_card_dicts(payload, file_path):
            try:
                cards.append(GlossaryCard.from_dict(raw))
            except ValueError as exc:
                raise GlossaryError(f"{file_path}: {exc}") from exc
        return cards

    # -- запросы ----------------------------------------------------------

    def get(self, card_id: str) -> GlossaryCard | None:
        """Вернуть карточку по id или None."""
        return self._by_id.get(card_id)

    def all(self) -> list[GlossaryCard]:
        """Все карточки базы (копия списка)."""
        return list(self._cards)

    def __len__(self) -> int:
        return len(self._cards)

    def search(self, query: str) -> list[GlossaryCard]:
        """Поиск по id/title/aliases/keywords/tags (подстрока, без регистра)."""
        return [card for card in self._cards if card.matches(query)]

    def list_by_status(self, status: str) -> list[GlossaryCard]:
        """Карточки с заданным статусом жизненного цикла."""
        return [card for card in self._cards if card.status == status]

    def list_by_tag(self, tag: str) -> list[GlossaryCard]:
        """Карточки, помеченные заданным тегом (без регистра)."""
        needle = tag.strip().lower()
        return [card for card in self._cards if needle in {t.lower() for t in card.tags}]

    def known_terms(self) -> set[str]:
        """Все search-термины базы — для подавления дублей в детекторе."""
        terms: set[str] = set()
        for card in self._cards:
            terms.update(card.search_terms)
        return terms


# ---------------------------------------------------------------------------
# Очередь пополнения (backlog недостающих карточек)
# ---------------------------------------------------------------------------


def load_missing_queue(path: pathlib.Path | str) -> list[GlossaryMissingEntry]:
    """Прочитать очередь пополнения из JSON-файла (пустой список, если нет файла)."""
    file_path = pathlib.Path(path)
    if not file_path.exists():
        return []
    try:
        with open(file_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise GlossaryError(f"{file_path}: невалидный JSON очереди — {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("missing", [])
    if not isinstance(payload, list):
        raise GlossaryError(f"{file_path}: очередь должна быть списком объектов")
    entries: list[GlossaryMissingEntry] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise GlossaryError(f"{file_path}: элемент очереди должен быть объектом")
        try:
            entries.append(GlossaryMissingEntry.from_dict(raw))
        except ValueError as exc:
            raise GlossaryError(f"{file_path}: {exc}") from exc
    return entries


def save_missing_queue(path: pathlib.Path | str, entries: list[GlossaryMissingEntry]) -> None:
    """Записать очередь пополнения в JSON-файл (создавая родительские директории)."""
    file_path = pathlib.Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [entry.to_dict() for entry in entries]
    with open(file_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def append_missing_entries(
    path: pathlib.Path | str, entries: list[GlossaryMissingEntry]
) -> list[GlossaryMissingEntry]:
    """Дозаписать элементы в очередь, дедуплицируя по ``concept``.

    Существующие элементы сохраняются; повторный ``concept`` не добавляется, но
    его источники (``seen_in``) объединяются. Возвращает итоговую очередь.
    """
    existing = load_missing_queue(path)
    by_concept: dict[str, GlossaryMissingEntry] = {e.concept: e for e in existing}
    for entry in entries:
        current = by_concept.get(entry.concept)
        if current is None:
            by_concept[entry.concept] = entry
            existing.append(entry)
        else:
            for src in entry.seen_in:
                if src not in current.seen_in:
                    current.seen_in.append(src)
    save_missing_queue(path, existing)
    return existing
