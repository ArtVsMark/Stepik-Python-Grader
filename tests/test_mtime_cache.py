"""Тесты mtime_cache.py — generic mtime-инвалидируемый кеш (issue #345)."""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from stepik_grader.mtime_cache import MtimeCache, mtime_signature


def test_mtime_signature_counts_files_and_takes_the_newest(tmp_path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("1", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("2", encoding="utf-8")
    assert mtime_signature([a, b]) == (2, max(a.stat().st_mtime, b.stat().st_mtime))


def test_mtime_signature_empty_and_missing(tmp_path) -> None:
    assert mtime_signature([]) == (0, 0.0)
    assert mtime_signature([tmp_path / "nope.txt"]) == (0, 0.0)  # OSError → «источника нет»


def test_cache_loads_once_and_reuses(tmp_path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[int] = MtimeCache()
    calls = 0

    def load() -> int:
        nonlocal calls
        calls += 1
        return 42

    assert cache.get_or_load("k", [f], load) == 42
    assert cache.get_or_load("k", [f], load) == 42  # из кеша, без повторной загрузки
    assert calls == 1


def test_cache_reloads_on_mtime_change(tmp_path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[int] = MtimeCache()
    n = 0

    def load() -> int:
        nonlocal n
        n += 1
        return n

    assert cache.get_or_load("k", [f], load) == 1
    os.utime(f, (f.stat().st_atime + 100, f.stat().st_mtime + 100))
    assert cache.get_or_load("k", [f], load) == 2  # mtime сменился → перезагрузка


def test_cache_on_error_returns_none_and_does_not_cache(tmp_path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[int] = MtimeCache()

    def boom() -> int:
        raise ValueError("bad")

    assert cache.get_or_load("k", [f], boom, on_error=ValueError) is None
    # ошибка не закеширована — последующая успешная загрузка работает
    assert cache.get_or_load("k", [f], lambda: 7) == 7


def test_cache_reraises_unlisted_error(tmp_path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[int] = MtimeCache()

    def boom() -> int:
        raise KeyError("x")

    with pytest.raises(KeyError):
        cache.get_or_load("k", [f], boom, on_error=ValueError)


def test_cache_clear_forces_reload(tmp_path) -> None:
    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[int] = MtimeCache()
    n = 0

    def load() -> int:
        nonlocal n
        n += 1
        return n

    cache.get_or_load("k", [f], load)
    cache.clear()
    cache.get_or_load("k", [f], load)
    assert n == 2


def test_cache_concurrent_hits_return_identical_object(tmp_path) -> None:
    """issue #405: кеш — module-level singleton, шарится потоками
    ThreadingHTTPServer. После прогрева N параллельных get_or_load одного ключа
    (mtime не менялся) отдают ОДИН И ТОТ ЖЕ объект — не пересобирают его на
    каждый поток/запрос (горячий путь /api/glossary*, #404)."""
    import threading

    f = tmp_path / "src.txt"
    f.write_text("x", encoding="utf-8")
    cache: MtimeCache[object] = MtimeCache()
    calls = 0

    def load() -> object:
        nonlocal calls
        calls += 1
        return object()  # свежий объект — идентичность различима

    warm = cache.get_or_load("k", [f], load)  # прогрев одним потоком
    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()  # старт всех потоков одновременно — максимум гонки
        results.append(cache.get_or_load("k", [f], load))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert all(r is warm for r in results)  # тот же объект во всех потоках
    assert calls == 1  # ни одного пересбора после прогрева


# ---------------------------------------------------------------------------
# issue #996 (PERF-1-03) — подпись по одному max(mtime) не замечала, что состав
# набора изменился. Список файлов у обоих bundled-потребителей приходит из
# glob по каталогу, поэтому удалённый файл просто исчезает из списка, а
# добавленный со старым mtime не сдвигает максимум. Витрина уверенно
# показывает не то, что на диске, — и до перезапуска.
# ---------------------------------------------------------------------------


class TestSignatureNoticesSetChanges:
    def _dir_files(self, directory: pathlib.Path) -> list[pathlib.Path]:
        """Так же, как bundled-потребители: список каждый раз строится заново."""
        return sorted(directory.glob("*.json"))

    def _aged(self, path: pathlib.Path, hours: float) -> None:
        stamp = time.time() - hours * 3600
        os.utime(path, (stamp, stamp))

    def test_removing_a_file_invalidates(self, tmp_path: pathlib.Path) -> None:
        """Самый свежий файл остался прежним — а карточек стало меньше."""
        newest = tmp_path / "b.json"
        newest.write_text("{}", encoding="utf-8")
        stale = tmp_path / "a.json"
        stale.write_text("{}", encoding="utf-8")
        self._aged(stale, hours=48)
        before = mtime_signature(self._dir_files(tmp_path))

        stale.unlink()

        assert mtime_signature(self._dir_files(tmp_path)) != before

    def test_adding_a_file_with_old_mtime_invalidates(self, tmp_path: pathlib.Path) -> None:
        """Распаковка архива и `cp -p` сохраняют исходный mtime — он старый."""
        existing = tmp_path / "b.json"
        existing.write_text("{}", encoding="utf-8")
        before = mtime_signature(self._dir_files(tmp_path))

        added = tmp_path / "a.json"
        added.write_text("{}", encoding="utf-8")
        self._aged(added, hours=48)

        assert mtime_signature(self._dir_files(tmp_path)) != before

    def test_cache_reloads_after_the_set_changed(self, tmp_path: pathlib.Path) -> None:
        """Не сигнатура ради сигнатуры: кеш обязан перечитать источник."""
        first = tmp_path / "a.json"
        first.write_text("{}", encoding="utf-8")
        second = tmp_path / "b.json"
        second.write_text("{}", encoding="utf-8")
        self._aged(second, hours=48)
        cache: MtimeCache[int] = MtimeCache()
        calls = {"n": 0}

        def _load() -> int:
            calls["n"] += 1
            return calls["n"]

        cache.get_or_load("bundled", self._dir_files(tmp_path), _load)
        second.unlink()
        cache.get_or_load("bundled", self._dir_files(tmp_path), _load)

        assert calls["n"] == 2

    def test_untouched_set_still_hits_the_cache(self, tmp_path: pathlib.Path) -> None:
        """Граница: лишних перечитываний быть не должно — кеш для того и есть."""
        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        cache: MtimeCache[int] = MtimeCache()
        calls = {"n": 0}

        def _load() -> int:
            calls["n"] += 1
            return calls["n"]

        cache.get_or_load("bundled", self._dir_files(tmp_path), _load)
        cache.get_or_load("bundled", self._dir_files(tmp_path), _load)

        assert calls["n"] == 1
