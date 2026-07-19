"""json_provider.py — загрузка/поиск локальной базы карточек глоссария (issue #126).

Архитектурный слой: Domain. Зависит от ``glossary/models.py`` и общего top-level
``db`` (оба stdlib-leaf), НЕ тянет ничего из ``core/`` — DAG остаётся ацикличным,
а подпакет ``glossary/`` независим от ``core/`` (ADR-0011).

``JsonGlossaryProvider`` — JSON-first реализация абстракции ``GlossaryProvider``
(карточки читаются из одного JSON-файла или директории с ``*.json``, валидируются
минимальные поля, поиск/выборка).

Очередь пополнения (``GlossaryMissingEntry``) — SQLite/WAL (issue #552, ADR-0011)
через общий top-level ``db`` (не ``core/``, чтобы ``glossary/`` не тянул
``core/``): read-modify-write под ``BEGIN IMMEDIATE`` закрывает межпроцессную
гонку CLI+web, которую прежний JSON (+ ``_MISSING_QUEUE_LOCK`` только на потоки)
не снимал. Legacy JSON-очереди читаются на чтение и один раз мигрируются в SQLite
при первой записи (обратная совместимость).

Принцип graceful degradation (как у кэша #56): битый/отсутствующий источник —
понятная ``GlossaryError``, а не падение всего грейдера; вызывающий код решает,
показать ошибку или продолжить с пустой базой.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import sqlite3
import threading
from typing import Any, Protocol, runtime_checkable

from stepik_grader import db

from .models import GlossaryCard, GlossaryMissingEntry

__all__ = [
    "BUNDLED_GLOSSARY_DIR",
    "GlossaryError",
    "GlossaryProvider",
    "JsonGlossaryProvider",
    "append_missing_entries",
    "load_missing_queue",
    "save_missing_queue",
]

# Комплектная база карточек (импорт из Glossary-Python, issue #326; число ready
# считает ``scripts/generate_glossary_badge.py``, не хардкод — #398/#535):
# каталог ``glossary/data/*.json``, попадает в wheel через package-data.
# Используется web-адаптером как zero-config источник по умолчанию (когда
# ``CONFIG.glossary_store`` не задан), с деградацией на компактный
# ``core/glossary.py``, если каталог отсутствует/пуст.
BUNDLED_GLOSSARY_DIR: pathlib.Path = pathlib.Path(__file__).parent / "data"


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
    def from_file(cls, path: pathlib.Path) -> JsonGlossaryProvider:
        """Загрузить карточки из одного JSON-файла."""
        return cls(cls._load_cards_from_file(path))

    @classmethod
    def from_directory(cls, path: pathlib.Path) -> JsonGlossaryProvider:
        """Загрузить карточки из всех ``*.json`` в директории (отсортировано)."""
        if not path.is_dir():
            raise GlossaryError(f"Директория глоссария не найдена: {path}")
        cards: list[GlossaryCard] = []
        for json_file in sorted(path.glob("*.json")):
            cards.extend(cls._load_cards_from_file(json_file))
        return cls(cards)

    @classmethod
    def load(cls, path: pathlib.Path) -> JsonGlossaryProvider:
        """Автоопределение: директория → from_directory, иначе from_file."""
        if path.is_dir():
            return cls.from_directory(path)
        return cls.from_file(path)

    @staticmethod
    def _load_cards_from_file(file_path: pathlib.Path) -> list[GlossaryCard]:
        if not file_path.exists():
            raise GlossaryError(f"Файл глоссария не найден: {file_path}")
        try:
            with file_path.open(encoding="utf-8") as fh:
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
# Очередь пополнения (backlog недостающих карточек) — SQLite/WAL (issue #552)
# ---------------------------------------------------------------------------
#
# Хранилище — SQLite (schema v1, таблица ``missing_entries``, PRIMARY KEY по
# ``concept``): списковые поля ``seen_in``/``suggested_tags`` держатся JSON-текстом
# в колонках. Порядок — по ``rowid`` (порядок вставки, как прежний JSON-список).
# Read-modify-write в ``append_missing_entries`` идёт под ``BEGIN IMMEDIATE`` —
# конкурентный писатель (другой ПРОЦЕСС: CLI + web) ЖДЁТ write-lock (busy_timeout),
# а не затирает добавку; это и есть durability-выигрыш #552 поверх атомарности #551.

_QUEUE_SCHEMA_VERSION = 1
_QUEUE_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS missing_entries (
    concept        TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    snippet        TEXT NOT NULL DEFAULT '',
    seen_in        TEXT NOT NULL DEFAULT '[]',
    suggested_tags TEXT NOT NULL DEFAULT '[]',
    verdict        TEXT,
    first_seen     TEXT NOT NULL DEFAULT '',
    origin         TEXT NOT NULL DEFAULT 'solution',
    module         TEXT NOT NULL DEFAULT '',
    qualname       TEXT NOT NULL DEFAULT ''
);
"""

# Магия заголовка SQLite-файла — отличить SQLite-очередь от legacy JSON / мусора.
_SQLITE_MAGIC = b"SQLite format 3\x00"

# Процессный лок вокруг read-modify-write очереди (issue #352): в пределах ОДНОГО
# процесса потоки (ThreadPoolExecutor web/runs.py) сериализуются здесь дёшево, без
# busy-wait; межпроцессную гонку CLI+web закрывает ``BEGIN IMMEDIATE`` +
# ``busy_timeout`` соединения (issue #552).
_MISSING_QUEUE_LOCK = threading.Lock()


def _queue_migrate(conn: sqlite3.Connection) -> None:
    db.apply_schema(conn, version=_QUEUE_SCHEMA_VERSION, ddl=_QUEUE_SCHEMA_V1)


def _connect_queue(path: pathlib.Path) -> sqlite3.Connection:
    """SQLite-соединение очереди (общий ``db.connect`` + автокоммит-режим).

    ``isolation_level=None`` — чтобы явные ``BEGIN IMMEDIATE``/``COMMIT`` были
    единственным управлением транзакцией (иначе драйвер откроет неявную deferred-
    транзакцию, и upgrade read→write-lock упрётся в ``SQLITE_BUSY_SNAPSHOT``).
    """
    conn = db.connect(path, migrate=_queue_migrate)
    conn.isolation_level = None
    return conn


def _is_sqlite_db(path: pathlib.Path) -> bool:
    """True, если файл начинается с SQLite-магии (иначе legacy JSON / мусор / нет)."""
    try:
        with path.open("rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _row_to_entry(row: sqlite3.Row) -> GlossaryMissingEntry:
    return GlossaryMissingEntry.from_dict(
        {
            "concept": row["concept"],
            "kind": row["kind"],
            "status": row["status"],
            "reason": row["reason"],
            "snippet": row["snippet"],
            "seen_in": json.loads(row["seen_in"]),
            "suggested_tags": json.loads(row["suggested_tags"]),
            "verdict": row["verdict"],
            "first_seen": row["first_seen"],
            "origin": row["origin"],
            "module": row["module"],
            "qualname": row["qualname"],
        }
    )


def _read_all_rows(conn: sqlite3.Connection) -> list[GlossaryMissingEntry]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM missing_entries ORDER BY rowid").fetchall()
    return [_row_to_entry(row) for row in rows]


def _replace_all_rows(conn: sqlite3.Connection, entries: list[GlossaryMissingEntry]) -> None:
    conn.execute("DELETE FROM missing_entries")
    conn.executemany(
        "INSERT INTO missing_entries (concept, kind, status, reason, snippet, "
        "seen_in, suggested_tags, verdict, first_seen, origin, module, qualname) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                entry.concept,
                entry.kind,
                entry.status,
                entry.reason,
                entry.snippet,
                json.dumps(entry.seen_in, ensure_ascii=False),
                json.dumps(entry.suggested_tags, ensure_ascii=False),
                entry.verdict,
                entry.first_seen,
                entry.origin,
                entry.module,
                entry.qualname,
            )
            for entry in entries
        ],
    )


def _read_legacy_json_queue(path: pathlib.Path) -> list[GlossaryMissingEntry]:
    """Прочитать legacy JSON-очередь (формат до #552) в список элементов."""
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise GlossaryError(f"{path}: невалидный JSON очереди — {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("missing", [])
    if not isinstance(payload, list):
        raise GlossaryError(f"{path}: очередь должна быть списком объектов")
    entries: list[GlossaryMissingEntry] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise GlossaryError(f"{path}: элемент очереди должен быть объектом")
        try:
            entries.append(GlossaryMissingEntry.from_dict(raw))
        except ValueError as exc:
            raise GlossaryError(f"{path}: {exc}") from exc
    return entries


def _ensure_queue_db(path: pathlib.Path) -> None:
    """Гарантировать, что ``path`` — валидная SQLite-очередь (миграция legacy JSON).

    Уже SQLite → no-op. ``path`` хранит legacy JSON (или мусор) — читаем его
    best-effort, удаляем файл (``sqlite3`` не откроет не-SQLite файл) и пересоздаём
    как SQLite с теми же элементами. Файла нет, но рядом legacy ``<stem>.json``
    (сменился дефолт пути на ``.db``, #552) → импортируем его один раз. Вызывается
    только на пути записи под ``_MISSING_QUEUE_LOCK``.
    """
    if path.exists() and _is_sqlite_db(path):
        return
    legacy: list[GlossaryMissingEntry] = []
    if path.exists():
        with contextlib.suppress(GlossaryError, OSError):
            legacy = _read_legacy_json_queue(path)
        with contextlib.suppress(OSError):
            path.unlink()
    else:
        sibling = path.with_suffix(".json")
        if sibling != path and sibling.exists() and not _is_sqlite_db(sibling):
            with contextlib.suppress(GlossaryError, OSError):
                legacy = _read_legacy_json_queue(sibling)
    # Создать родительскую директорию (как прежний atomic-JSON-писатель, #551):
    # sqlite3.connect создаёт сам файл БД, но не путь к нему.
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect_queue(path)) as conn:
        if legacy:
            conn.execute("BEGIN IMMEDIATE")
            _replace_all_rows(conn, legacy)
            conn.execute("COMMIT")


def load_missing_queue(path: pathlib.Path) -> list[GlossaryMissingEntry]:
    """Прочитать очередь пополнения (пустой список, если файла нет).

    Читает SQLite-очередь (issue #552) либо, для обратной совместимости, legacy
    JSON (read-only — миграция в SQLite происходит при первой записи). Битая
    БД/JSON → ``GlossaryError`` (вызывающий решает: показать или продолжить пусто).
    Файл не создаётся, если его нет (opt-in, как история #134).
    """
    if not path.exists():
        return []
    if not _is_sqlite_db(path):
        return _read_legacy_json_queue(path)
    try:
        with contextlib.closing(_connect_queue(path)) as conn:
            return _read_all_rows(conn)
    except (sqlite3.Error, ValueError) as exc:
        raise GlossaryError(f"{path}: ошибка чтения SQLite-очереди — {exc}") from exc


def save_missing_queue(path: pathlib.Path, entries: list[GlossaryMissingEntry]) -> None:
    """Заменить очередь пополнения целиком (replace-all) в SQLite/WAL (issue #552).

    Транзакция ``BEGIN IMMEDIATE`` сериализует конкурентных писателей (в т.ч.
    межпроцессно). Legacy JSON по пути один раз мигрируется в SQLite перед записью.
    """
    with _MISSING_QUEUE_LOCK:
        try:
            _ensure_queue_db(path)
            with contextlib.closing(_connect_queue(path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    _replace_all_rows(conn, entries)
                    conn.execute("COMMIT")
                except BaseException:
                    with contextlib.suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                    raise
        except (sqlite3.Error, OSError) as exc:
            raise GlossaryError(f"{path}: ошибка записи SQLite-очереди — {exc}") from exc


def append_missing_entries(
    path: pathlib.Path, entries: list[GlossaryMissingEntry]
) -> list[GlossaryMissingEntry]:
    """Дозаписать элементы в очередь, дедуплицируя по ``concept``.

    Существующие элементы сохраняются; повторный ``concept`` не добавляется, но
    его источники (``seen_in``) объединяются. ``origin`` первого обнаружения не
    перезаписывается (practice-driven и source-driven записи для одного
    ``concept`` не конкурируют), но пустые ``module``/``qualname`` дополняются
    из новой записи — так source-driven скан (issue #196/#197) обогащает уже
    обнаруженный practice-driven пробел. Возвращает итоговую очередь.

    Read-modify-write идёт в ОДНОЙ транзакции ``BEGIN IMMEDIATE`` (issue #552):
    write-lock берётся до чтения, поэтому конкурентный писатель — как другой поток
    (``_MISSING_QUEUE_LOCK``), так и другой ПРОЦЕСС (busy_timeout) — ждёт, а не
    затирает добавку.
    """
    with _MISSING_QUEUE_LOCK:
        try:
            _ensure_queue_db(path)
            with contextlib.closing(_connect_queue(path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing = _read_all_rows(conn)
                    by_concept: dict[str, GlossaryMissingEntry] = {
                        entry.concept: entry for entry in existing
                    }
                    for entry in entries:
                        current = by_concept.get(entry.concept)
                        if current is None:
                            by_concept[entry.concept] = entry
                            existing.append(entry)
                        else:
                            for src in entry.seen_in:
                                if src not in current.seen_in:
                                    current.seen_in.append(src)
                            if not current.module and entry.module:
                                current.module = entry.module
                            if not current.qualname and entry.qualname:
                                current.qualname = entry.qualname
                    _replace_all_rows(conn, existing)
                    conn.execute("COMMIT")
                except BaseException:
                    with contextlib.suppress(sqlite3.Error):
                        conn.execute("ROLLBACK")
                    raise
                return existing
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise GlossaryError(f"{path}: ошибка записи SQLite-очереди — {exc}") from exc
