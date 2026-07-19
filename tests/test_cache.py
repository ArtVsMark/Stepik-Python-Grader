"""Tests for core/cache.py — opt-in кэш результатов проверки (issue #56).

Покрывает:
  - хеширование решения/тестов (стабильность + чувствительность к изменениям);
  - GraderCache: put/get/save/clear-roundtrip, промах по хешу, битый файл;
  - CLI-интеграцию (--mode 1/2 --cache, --clear-cache): первый прогон считает,
    второй берёт из кэша, изменение решения инвалидирует.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader import cli
from stepik_grader.core.cache import (
    CACHE_DIR_NAME,
    GraderCache,
    hash_solution,
    hash_tests,
)


def _make_task(tmp_path: pathlib.Path, body: str = "print(int(input()) * 2)\n") -> str:
    """Создать решение task.py + tests/1 + tests/1.clue (Формат 1). Вернуть путь."""
    sol = tmp_path / "task.py"
    sol.write_text(body, encoding="utf-8")
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "1").write_text("21\n", encoding="utf-8")
    (tdir / "1.clue").write_text("42\n", encoding="utf-8")
    return str(sol)


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------


def test_hash_solution_stable_and_sensitive(tmp_path: pathlib.Path) -> None:
    sol = tmp_path / "task.py"
    sol.write_text("x = 1\n", encoding="utf-8")
    h1 = hash_solution(sol)
    assert h1 == hash_solution(sol)  # стабильно
    sol.write_text("x = 2\n", encoding="utf-8")
    assert hash_solution(sol) != h1  # чувствительно к содержимому


def test_hash_tests_stable_and_sensitive(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)
    tdir = tmp_path / "tests"
    h1 = hash_tests(tdir)
    assert h1 == hash_tests(tdir)
    # изменение ожидаемого вывода меняет хеш
    (tmp_path / "tests" / "1.clue").write_text("99\n", encoding="utf-8")
    assert hash_tests(tdir) != h1


def test_hash_tests_missing_dir_is_stable(tmp_path: pathlib.Path) -> None:
    """Несуществующая директория → стабильный хеш пустого потока, без падения."""
    missing = tmp_path / "nope"
    assert hash_tests(missing) == hash_tests(missing)


def test_hash_tests_ignores_subdirectories(tmp_path: pathlib.Path) -> None:
    """Вложенные директории (не-файлы) пропускаются, хешируется их содержимое."""
    _make_task(tmp_path)
    tdir = tmp_path / "tests"
    (tdir / "nested").mkdir()
    (tdir / "nested" / "extra").write_text("data\n", encoding="utf-8")
    h1 = hash_tests(tdir)
    # добавление файла в поддиректорию меняет хеш (содержимое учтено)
    (tdir / "nested" / "extra").write_text("changed\n", encoding="utf-8")
    assert hash_tests(tdir) != h1


# ---------------------------------------------------------------------------
# GraderCache roundtrip
# ---------------------------------------------------------------------------


def test_cache_put_get_roundtrip(tmp_path: pathlib.Path) -> None:
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    sol = tmp_path / "task.py"
    result = {"passed": 1, "total": 1}
    assert cache.get(sol, "sha_s", "sha_t") is None  # холодный кэш
    cache.put(sol, "sha_s", "sha_t", result)
    assert cache.get(sol, "sha_s", "sha_t") == result


def test_cache_persists_across_instances(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    sol = tmp_path / "task.py"
    sol.write_text("print(1)\n", encoding="utf-8")  # реальный файл — save() не пруниет живую запись
    first = GraderCache(cache_dir=cache_dir)
    first.put(sol, "s", "t", {"ok": True})
    first.save()

    second = GraderCache(cache_dir=cache_dir)
    assert second.get(sol, "s", "t") == {"ok": True}


def test_cache_miss_on_changed_hash(tmp_path: pathlib.Path) -> None:
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    sol = tmp_path / "task.py"
    cache.put(sol, "s1", "t1", {"n": 1})
    assert cache.get(sol, "s2", "t1") is None  # изменился solution_sha
    assert cache.get(sol, "s1", "t2") is None  # изменился tests_sha


def test_cache_clear_removes_file_and_counts(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    cache = GraderCache(cache_dir=cache_dir)
    a = tmp_path / "a.py"
    a.write_text("a\n", encoding="utf-8")  # реальные файлы — save() не пруниет живые записи
    b = tmp_path / "b.py"
    b.write_text("b\n", encoding="utf-8")
    cache.put(a, "s", "t", {})
    cache.put(b, "s", "t", {})
    cache.save()
    assert cache.cache_file.exists()

    removed = cache.clear()
    assert removed == 2
    assert not cache.cache_file.exists()


def test_cache_corrupt_file_treated_as_empty(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    cache_dir.mkdir()
    (cache_dir / "results.json").write_text("{ not json", encoding="utf-8")
    cache = GraderCache(cache_dir=cache_dir)
    assert cache.get(tmp_path / "task.py", "s", "t") is None  # не падаем


def test_cache_wrong_version_treated_as_empty(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    cache_dir.mkdir()
    (cache_dir / "results.json").write_text(
        json.dumps({"version": 999, "entries": {"x": 1}}), encoding="utf-8"
    )
    cache = GraderCache(cache_dir=cache_dir)
    assert cache._data["entries"] == {}


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_mode_1_cache_hit_on_second_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Первый прогон считает, второй берёт из кэша и печатает cache_hit."""
    sol = _make_task(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["--mode", "1", "--file", sol, "--cache", "--lang", "en"])
    first = capsys.readouterr().out
    assert "up to date" not in first  # первый раз — считаем
    assert (tmp_path / CACHE_DIR_NAME / "results.json").exists()

    cli.main(["--mode", "1", "--file", sol, "--cache", "--lang", "en"])
    second = capsys.readouterr().out
    assert "up to date" in second  # второй раз — попадание


def test_mode_1_cache_invalidated_on_solution_change(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sol = _make_task(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["--mode", "1", "--file", sol, "--cache", "--lang", "en"])
    capsys.readouterr()

    # меняем решение — хеш решения меняется, кэш инвалидируется
    pathlib.Path(sol).write_text("print(int(input()) * 3)\n", encoding="utf-8")
    cli.main(["--mode", "1", "--file", sol, "--cache", "--lang", "en"])
    out = capsys.readouterr().out
    assert "up to date" not in out


def test_mode_1_without_cache_flag_writes_nothing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без --cache (и use_cache=false) .grader_cache/ не создаётся."""
    sol = _make_task(tmp_path)
    monkeypatch.chdir(tmp_path)
    cli.main(["--mode", "1", "--file", sol, "--no-cache", "--lang", "en"])
    assert not (tmp_path / CACHE_DIR_NAME).exists()


def test_mode_2_cache_summary(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Режим 2 печатает сводку 'N из M решений из кэша' на втором прогоне."""
    _make_task(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["--mode", "2", "--dir", str(tmp_path), "--cache", "--lang", "en"])
    capsys.readouterr()

    cli.main(["--mode", "2", "--dir", str(tmp_path), "--cache", "--lang", "en"])
    out = capsys.readouterr().out
    assert "1 of 1 solutions served from cache" in out


def test_clear_cache_flag(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sol = _make_task(tmp_path)
    monkeypatch.chdir(tmp_path)
    cli.main(["--mode", "1", "--file", sol, "--cache", "--lang", "en"])
    capsys.readouterr()
    assert (tmp_path / CACHE_DIR_NAME / "results.json").exists()

    cli.main(["--clear-cache", "--lang", "en"])
    out = capsys.readouterr().out
    assert "Cache cleared" in out
    assert not (tmp_path / CACHE_DIR_NAME / "results.json").exists()


# ---------------------------------------------------------------------------
# pruning (issue #553)
# ---------------------------------------------------------------------------


def test_prune_drops_entries_for_missing_solution_files(tmp_path: pathlib.Path) -> None:
    """save() пруниет записи, чей файл-решение исчез (issue #553)."""
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    alive = tmp_path / "alive.py"
    alive.write_text("x = 1\n", encoding="utf-8")
    gone = tmp_path / "gone.py"
    gone.write_text("y = 2\n", encoding="utf-8")

    cache.put(alive, "sa", "ta", {"verdict": "AC"})
    cache.put(gone, "sg", "tg", {"verdict": "AC"})
    gone.unlink()  # файл решения удалён

    assert cache.prune() == 1
    assert cache.get(alive, "sa", "ta") == {"verdict": "AC"}  # живой цел
    assert cache.get(gone, "sg", "tg") is None  # мёртвый выброшен

    # save() тоже пруниет: перезагруженный кэш не содержит мёртвой записи
    cache.save()
    reloaded = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    assert reloaded.get(alive, "sa", "ta") == {"verdict": "AC"}
    assert reloaded.get(gone, "sg", "tg") is None


def test_prune_caps_total_entries_dropping_oldest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prune ограничивает число записей ``_CACHE_MAX_ENTRIES``, отбрасывая старейшие."""
    from stepik_grader.core import cache as cache_mod

    monkeypatch.setattr(cache_mod, "_CACHE_MAX_ENTRIES", 3)
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    paths = []
    for i in range(5):
        sol = tmp_path / f"s{i}.py"
        sol.write_text(f"x = {i}\n", encoding="utf-8")
        paths.append(sol)
        cache.put(sol, f"s{i}", "t", {"i": i})

    assert cache.prune() == 2  # все файлы существуют → срабатывает только size-cap (5 → 3)
    # выброшены два самых старых по вставке (s0, s1); s2..s4 сохранены
    assert cache.get(paths[0], "s0", "t") is None
    assert cache.get(paths[1], "s1", "t") is None
    assert cache.get(paths[4], "s4", "t") == {"i": 4}
