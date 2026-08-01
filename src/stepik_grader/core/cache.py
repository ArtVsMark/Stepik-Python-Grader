"""cache.py — opt-in кэш результатов проверки (issue #56).

Архитектурный слой: Infrastructure / Utilities.
Зависит только от core/storage.py (leaf) + stdlib (hashlib/pathlib) — новых
рёбер-циклов в DAG не создаёт (cli.py → core/cache.py → core/storage.py).

Идея: при повторном запуске грейдера не перепрогонять тесты для решения,
чьё содержимое И набор тест-кейсов не изменились с прошлого раза. Ключ
кэша — пара sha256:

    solution_sha  — sha256 содержимого файла решения
    tests_sha     — sha256 всех файлов тест-директории (путь + содержимое)

Изменение ЛЮБОГО из хешей инвалидирует запись — тест перезапускается.

Хранилище: один JSON-файл ``.grader_cache/results.json`` (по умолчанию в
CWD). Кэш opt-in: включается флагом ``--cache`` / ``--no-cache`` или секцией
``[tool.stepik-grader] use_cache = true`` в pyproject.toml.
"""

from __future__ import annotations

import hashlib
import pathlib
from typing import Any

from stepik_grader.core.storage import load_json_file, save_json_file

__all__ = [
    "CACHE_DIR_NAME",
    "GraderCache",
    "hash_solution",
    "hash_tests",
]

CACHE_DIR_NAME = ".grader_cache"
_CACHE_FILE_NAME = "results.json"
_CACHE_VERSION = 1
# Верхняя граница числа записей кэша (issue #553): backstop против неограниченного
# роста results.json. При превышении отбрасываются самые старые по порядку вставки.
_CACHE_MAX_ENTRIES = 512


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_solution(solution_path: pathlib.Path) -> str:
    """sha256 содержимого файла решения.

    Единый источник хеша решения для всего пакета (issue #553): кормит и ключ
    кэша (``solution_sha``), и колонку ``runs.solution_hash`` истории через
    ``cli/commands`` и ``web/viewmodels`` (реэкспорт — фасад ``web/grading``).
    Прежняя дублирующая ``history.hash_solution(code: str)`` удалена.
    """
    return _sha256_bytes(solution_path.read_bytes())


def hash_tests(test_dir: pathlib.Path) -> str:
    """sha256 всех файлов тест-директории (относительный путь + содержимое).

    Файлы обходятся отсортированно по относительному POSIX-пути, поэтому
    хеш стабилен между запусками и операционными системами. Несуществующая
    или пустая директория даёт стабильный хеш пустого потока.
    """
    root = test_dir
    if not root.is_dir():
        return _sha256_bytes(b"")
    h = hashlib.sha256()
    for f in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not f.is_file():
            continue
        h.update(f.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


class GraderCache:
    """JSON-бэкенд кэша результатов проверки (issue #56).

    Загружает кэш в память при создании; ``put()`` копит изменения,
    ``save()`` сбрасывает их на диск (один раз для всей пачки решений).
    Битый/несовместимый по версии файл кэша трактуется как пустой — кэш
    никогда не роняет грейдер.
    """

    def __init__(self, cache_dir: pathlib.Path | None = None) -> None:
        base = cache_dir if cache_dir is not None else pathlib.Path.cwd() / CACHE_DIR_NAME
        self.cache_dir = base
        self.cache_file = base / _CACHE_FILE_NAME
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.cache_file.exists():
            return self._empty()
        try:
            data = load_json_file(self.cache_file)
        except (OSError, ValueError):
            return self._empty()
        if data.get("version") != _CACHE_VERSION or not isinstance(data.get("entries"), dict):
            return self._empty()
        return data

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": _CACHE_VERSION, "entries": {}}

    @staticmethod
    def _key(solution_path: pathlib.Path) -> str:
        return str(solution_path.resolve())

    def get(
        self, solution_path: pathlib.Path, solution_sha: str, tests_sha: str
    ) -> dict[str, Any] | None:
        """Вернуть кэшированный result, если оба хеша совпадают; иначе None."""
        entry = self._data["entries"].get(self._key(solution_path))
        # issue #792 (FST-04): запись должна быть словарём. Прежняя проверка
        # `if not entry` пропускала непустую строку/число из повреждённого или
        # чужого results.json, и следующий же `entry.get(...)` падал
        # AttributeError мимо всех перехватов — кэш обязан деградировать до
        # «промаха», а не ронять грейдинг.
        if not isinstance(entry, dict):
            return None
        if entry.get("solution_sha") == solution_sha and entry.get("tests_sha") == tests_sha:
            result = entry.get("result")
            return result if isinstance(result, dict) else None
        return None

    def put(
        self,
        solution_path: pathlib.Path,
        solution_sha: str,
        tests_sha: str,
        result: dict[str, Any],
    ) -> None:
        """Сохранить result в память под парой хешей (без записи на диск)."""
        self._data["entries"][self._key(solution_path)] = {
            "solution_sha": solution_sha,
            "tests_sha": tests_sha,
            "result": result,
        }

    def prune(self) -> int:
        """Удалить мёртвые записи; вернуть их число (issue #553).

        Ключ записи — абсолютный путь решения; если файла больше нет
        (удалён/перемещён), запись мертва — при повторном появлении файла тест
        просто перепрогонится (кэш регенерируем). Дополнительно ограничивает число
        записей ``_CACHE_MAX_ENTRIES``, отбрасывая самые старые по порядку вставки
        (backstop против неограниченного роста ``results.json``).
        """
        entries: dict[str, Any] = self._data["entries"]
        removed = 0
        for key in list(entries):
            try:
                alive = pathlib.Path(key).exists()
            except OSError:
                alive = False
            if not alive:
                del entries[key]
                removed += 1
        surplus = len(entries) - _CACHE_MAX_ENTRIES
        if surplus > 0:
            for key in list(entries)[:surplus]:  # самые старые по порядку вставки
                del entries[key]
                removed += 1
        return removed

    def save(self) -> None:
        """Прунит мёртвые записи (issue #553) и пишет .grader_cache/results.json."""
        self.prune()
        save_json_file(self.cache_file, self._data)

    def clear(self) -> int:
        """Удалить файл кэша и вернуть число удалённых записей."""
        removed = len(self._data.get("entries", {}))
        self._data = self._empty()
        if self.cache_file.exists():
            self.cache_file.unlink()
        return removed
