"""cache.py — opt-in кэш результатов проверки (issue #56).

Архитектурный слой: Infrastructure / Utilities.
Зависит только от core/storage.py (leaf) + stdlib (hashlib/pathlib) — новых
рёбер-циклов в DAG не создаёт (cli.py → core/cache.py → core/storage.py).

Идея: при повторном запуске грейдера не перепрогонять тесты для решения,
чьё содержимое, набор тест-кейсов И условия исполнения не изменились с
прошлого раза. Ключ кэша — три отпечатка:

    solution_sha  — sha256 содержимого файла решения
    tests_sha     — sha256 всех файлов тест-директории (путь + содержимое)
    env           — sha256 условий прогона (core/runprofile.py): runner и
                    backend песочницы, таймаут, лимиты вывода/памяти,
                    кодировка, версия интерпретатора и пакета

Изменение ЛЮБОГО из трёх инвалидирует запись — тест перезапускается.

Третий появился по находкам аудита 2026-08-10 (issue #984): вердикт зависит
от условий не меньше, чем от кода. Кэш, знавший только два хеша, отдавал
обычному прогону результат, порождённый ``--sandbox`` (``FAIL 0/2`` и чужие
15.10 МБ), а под ``--sandbox`` возвращал несанбоксный вердикт — то есть
изоляция при активном ``--cache`` не включалась вовсе.

Хранилище: один JSON-файл ``.grader_cache/results.json`` (по умолчанию в
CWD). Кэш opt-in: включается флагом ``--cache`` / ``--no-cache`` или секцией
``[tool.stepik-grader] use_cache = true`` в pyproject.toml.
"""

from __future__ import annotations

import hashlib
import pathlib
import warnings
from collections.abc import Mapping
from typing import Any

from stepik_grader.atomic_io import atomic_write_json
from stepik_grader.core.storage import load_json_file

__all__ = [
    "CACHE_DIR_NAME",
    "GraderCache",
    "hash_solution",
    "hash_tests",
]

CACHE_DIR_NAME = ".grader_cache"
_CACHE_FILE_NAME = "results.json"
# Версия 2 (issue #984): в записи появился отпечаток условий прогона. Записи
# версии 1 порождены неизвестными условиями и не подлежат переносу — файл
# трактуется как пустой, тесты прогоняются заново.
_CACHE_VERSION = 2
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
        self._save_warned = False

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
        self, solution_path: pathlib.Path, solution_sha: str, tests_sha: str, *, env: str
    ) -> dict[str, Any] | None:
        """Вернуть кэшированный result, если совпали все три отпечатка; иначе None.

        ``env`` — отпечаток условий прогона (``RunProfile.fingerprint``).
        Keyword-only и без значения по умолчанию намеренно: забытый аргумент
        должен ломать вызов на месте, а не тихо возвращать вердикт, снятый при
        других таймауте и изоляции (issue #984).
        """
        entry = self._data["entries"].get(self._key(solution_path))
        # issue #792 (FST-04): запись должна быть словарём. Прежняя проверка
        # `if not entry` пропускала непустую строку/число из повреждённого или
        # чужого results.json, и следующий же `entry.get(...)` падал
        # AttributeError мимо всех перехватов — кэш обязан деградировать до
        # «промаха», а не ронять грейдинг.
        if not isinstance(entry, dict):
            return None
        if (
            entry.get("solution_sha") == solution_sha
            and entry.get("tests_sha") == tests_sha
            and entry.get("env") == env
        ):
            result = entry.get("result")
            return result if isinstance(result, dict) else None
        return None

    def put(
        self,
        solution_path: pathlib.Path,
        solution_sha: str,
        tests_sha: str,
        result: Mapping[str, Any],
        *,
        env: str,
    ) -> None:
        """Сохранить result в память под тремя отпечатками (без записи на диск)."""
        self._data["entries"][self._key(solution_path)] = {
            "solution_sha": solution_sha,
            "tests_sha": tests_sha,
            "env": env,
            "result": result,
        }

    def queue_summary(self) -> dict[str, int]:
        """Сколько задач лежит в кэше и сколько из них уже мертвы (issue #1192).

        «Очередь задач из кэша» в сводке ``stats``: сколько решений грейдер
        помнит и по скольким из них файл уже удалён или перемещён. Второе число
        не дефект — запись регенерируема, — но по нему видно, что пора звать
        ``--clear-cache``.

        Считает по загруженному состоянию и **ничего не меняет**: сводка не
        вправе подчищать чужие данные, для этого есть :meth:`prune`.
        """
        entries: dict[str, Any] = self._data["entries"]
        stale = 0
        for key in entries:
            try:
                if not pathlib.Path(key).exists():
                    stale += 1
            except OSError:
                # Недоступный путь (снятый диск, отобранные права) — не «мёртвый»:
                # утверждать, что файла нет, мы не можем.
                continue
        return {"tasks": len(entries), "stale": stale}

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
        """Прунит мёртвые записи (issue #553) и пишет .grader_cache/results.json.

        Отказ записи не роняет грейдинг (issue #984, PY-3-02): каталог только
        для чтения, кончившееся место, сетевой диск — всё это причины не
        сохранить кэш, но не причины потерять уже посчитанный вердикт. Кэш
        регенерируем по определению, поэтому сбой гасится одним предупреждением
        на экземпляр — тот же принцип, что у ``_load()`` и ``stats.record_run``.
        """
        self.prune()
        try:
            atomic_write_json(self.cache_file, self._data)
        except OSError as e:
            if not self._save_warned:
                self._save_warned = True
                warnings.warn(
                    f"{self.cache_file}: кэш результатов не сохранён ({e}); "
                    f"на вердикт это не влияет — следующий прогон посчитает заново.",
                    UserWarning,
                    stacklevel=2,
                )

    def clear(self) -> int:
        """Удалить файл кэша и вернуть число удалённых записей."""
        removed = len(self._data.get("entries", {}))
        self._data = self._empty()
        if self.cache_file.exists():
            self.cache_file.unlink()
        return removed
