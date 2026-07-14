"""json_provider.py — загрузка/поиск базы карточек правил PEP 8 (issue #345).

Архитектурный слой: Domain. Зависит только от ``rules/models.py`` (stdlib) и
общего ``core/mtime_cache.py`` — не тянет web/CLI, DAG остаётся ацикличным,
провайдер работает без web-слоя (для CLI-витрины #349).

``JsonRulesProvider`` читает карточки из одного JSON-файла или директории
``*.json``, валидирует и предоставляет поиск/выборку. Graceful degradation
(как у глоссария #126): битый/отсутствующий источник — понятная ``RulesError``,
а не падение грейдера.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from stepik_grader.core.mtime_cache import MtimeCache

from .models import RuleCard

__all__ = [
    "BUNDLED_RULES_DIR",
    "RulesError",
    "RulesProvider",
    "JsonRulesProvider",
    "bundled_rules",
]

# Комплектная база карточек правил (каталог ``rules/data/*.json``, попадает в
# wheel через package-data). Zero-config источник по умолчанию для CLI/web.
BUNDLED_RULES_DIR: Path = Path(__file__).parent / "data"


class RulesError(ValueError):
    """Ошибка чтения/валидации базы карточек правил."""


@runtime_checkable
class RulesProvider(Protocol):
    """Абстракция источника карточек правил (JSON сейчас, SQLite позже)."""

    def get(self, rule_id: str) -> RuleCard | None:
        """Вернуть карточку по коду правила или None."""
        ...

    def all(self) -> list[RuleCard]:
        """Все карточки базы."""
        ...

    def search(self, query: str) -> list[RuleCard]:
        """Поиск по id/title/tags (подстрока, без регистра)."""
        ...


def _iter_card_dicts(payload: Any, source: Path) -> list[dict[str, Any]]:
    """Извлечь список card-dict'ов из JSON-корня (list или {"rules": [...]})."""
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = payload.get("rules", [])
    else:
        raise RulesError(f"{source}: ожидался список карточек или объект с 'rules'")
    if not isinstance(raw, list):
        raise RulesError(f"{source}: поле 'rules' должно быть списком")
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RulesError(
                f"{source}: элемент карточки должен быть объектом, а не {type(item).__name__}"
            )
        result.append(item)
    return result


class JsonRulesProvider:
    """JSON-реализация ``RulesProvider``: карточки из файла или директории."""

    def __init__(self, cards: list[RuleCard]) -> None:
        self._cards: list[RuleCard] = list(cards)
        self._by_id: dict[str, RuleCard] = {}
        for card in self._cards:
            if card.id in self._by_id:
                raise RulesError(f"Дублирующийся id карточки правила: {card.id!r}")
            self._by_id[card.id] = card

    @classmethod
    def from_file(cls, path: Path) -> JsonRulesProvider:
        """Загрузить карточки из одного JSON-файла."""
        return cls(cls._load_cards_from_file(path))

    @classmethod
    def from_directory(cls, path: Path) -> JsonRulesProvider:
        """Загрузить карточки из всех ``*.json`` в директории (отсортировано)."""
        if not path.is_dir():
            raise RulesError(f"Директория правил не найдена: {path}")
        cards: list[RuleCard] = []
        for json_file in sorted(path.glob("*.json")):
            cards.extend(cls._load_cards_from_file(json_file))
        return cls(cards)

    @classmethod
    def load(cls, path: Path) -> JsonRulesProvider:
        """Автоопределение: директория → from_directory, иначе from_file."""
        return cls.from_directory(path) if path.is_dir() else cls.from_file(path)

    @staticmethod
    def _load_cards_from_file(file_path: Path) -> list[RuleCard]:
        if not file_path.exists():
            raise RulesError(f"Файл правил не найден: {file_path}")
        try:
            with open(file_path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            raise RulesError(f"{file_path}: невалидный JSON — {exc}") from exc
        cards: list[RuleCard] = []
        for raw in _iter_card_dicts(payload, file_path):
            try:
                cards.append(RuleCard.from_dict(raw))
            except ValueError as exc:
                raise RulesError(f"{file_path}: {exc}") from exc
        return cards

    def get(self, rule_id: str) -> RuleCard | None:
        """Вернуть карточку по коду правила или None."""
        return self._by_id.get(rule_id)

    def all(self) -> list[RuleCard]:
        """Все карточки базы (копия списка)."""
        return list(self._cards)

    def __len__(self) -> int:
        return len(self._cards)

    def search(self, query: str) -> list[RuleCard]:
        """Поиск по id/title/tags (подстрока, без регистра)."""
        return [card for card in self._cards if card.matches(query)]

    def list_by_tag(self, tag: str) -> list[RuleCard]:
        """Карточки, помеченные заданным тегом (без регистра)."""
        needle = tag.strip().lower()
        return [card for card in self._cards if needle in {t.lower() for t in card.tags}]


# Кеш bundled-провайдера по mtime каталога (общий механизм core/mtime_cache,
# тот же, что у глоссария #339) — парсим ~30 карточек один раз.
_BUNDLED_CACHE: MtimeCache[JsonRulesProvider] = MtimeCache()


def bundled_rules() -> JsonRulesProvider:
    """Провайдер комплектной базы правил (``rules/data``), кеш по mtime.

    Пустой провайдер (без карточек), если каталог отсутствует/битый — graceful,
    как у глоссария: витрина покажет пустой раздел, а не упадёт.
    """
    files = sorted(BUNDLED_RULES_DIR.glob("*.json")) if BUNDLED_RULES_DIR.is_dir() else []
    provider = _BUNDLED_CACHE.get_or_load(
        "bundled",
        files,
        lambda: JsonRulesProvider.from_directory(BUNDLED_RULES_DIR),
        on_error=RulesError,
    )
    return provider if provider is not None else JsonRulesProvider([])
