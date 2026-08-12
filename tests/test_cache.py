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

# Отпечаток условий прогона (issue #984): в юнит-тестах кэша конкретное значение
# неважно — важно лишь, совпадает оно с записью или нет.
_ENV = "test-run-profile"


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
    assert cache.get(sol, "sha_s", "sha_t", env=_ENV) is None  # холодный кэш
    cache.put(sol, "sha_s", "sha_t", result, env=_ENV)
    assert cache.get(sol, "sha_s", "sha_t", env=_ENV) == result


def test_cache_persists_across_instances(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    sol = tmp_path / "task.py"
    sol.write_text("print(1)\n", encoding="utf-8")  # реальный файл — save() не пруниет живую запись
    first = GraderCache(cache_dir=cache_dir)
    first.put(sol, "s", "t", {"ok": True}, env=_ENV)
    first.save()

    second = GraderCache(cache_dir=cache_dir)
    assert second.get(sol, "s", "t", env=_ENV) == {"ok": True}


def test_cache_miss_on_changed_hash(tmp_path: pathlib.Path) -> None:
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    sol = tmp_path / "task.py"
    cache.put(sol, "s1", "t1", {"n": 1}, env=_ENV)
    assert cache.get(sol, "s2", "t1", env=_ENV) is None  # изменился solution_sha
    assert cache.get(sol, "s1", "t2", env=_ENV) is None  # изменился tests_sha


def test_cache_miss_on_changed_run_conditions(tmp_path: pathlib.Path) -> None:
    """Отпечаток условий — третий ключ наравне с хешами (issue #984).

    Тот же код и те же кейсы под другим таймаутом/изоляцией дают другой
    вердикт, поэтому запись прошлых условий обязана считаться промахом.
    """
    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    sol = tmp_path / "task.py"
    cache.put(sol, "s", "t", {"verdict": "AC"}, env="local-runner")

    assert cache.get(sol, "s", "t", env="sandbox-runner") is None
    assert cache.get(sol, "s", "t", env="local-runner") == {"verdict": "AC"}


def test_cache_save_survives_unwritable_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError на записи не роняет грейдинг — кэш регенерируем (issue #984, PY-3-02).

    Каталог только для чтения, кончившееся место, отвалившийся сетевой диск:
    вердикт уже посчитан, терять его из-за неудачной записи кэша нельзя.
    """
    from stepik_grader.core import cache as cache_mod

    cache = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    sol = tmp_path / "task.py"
    sol.write_text("print(1)\n", encoding="utf-8")
    cache.put(sol, "s", "t", {"verdict": "AC"}, env=_ENV)

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(cache_mod, "save_json_file", _refuse)

    with pytest.warns(UserWarning, match="кэш"):
        cache.save()

    cache.save()  # повторный отказ молчит — предупреждение одно на экземпляр


def test_cache_clear_removes_file_and_counts(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / CACHE_DIR_NAME
    cache = GraderCache(cache_dir=cache_dir)
    a = tmp_path / "a.py"
    a.write_text("a\n", encoding="utf-8")  # реальные файлы — save() не пруниет живые записи
    b = tmp_path / "b.py"
    b.write_text("b\n", encoding="utf-8")
    cache.put(a, "s", "t", {}, env=_ENV)
    cache.put(b, "s", "t", {}, env=_ENV)
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
    assert cache.get(tmp_path / "task.py", "s", "t", env=_ENV) is None  # не падаем


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


def test_cache_miss_when_run_conditions_change(tmp_path: pathlib.Path) -> None:
    """Смена условий исполнения инвалидирует вердикт из кэша (PY-3-01, PERF-1-01).

    Прогон подпроцессом, а не ``cli.main``: проверяется поверхность, на которой
    найден дефект, — команда целиком, вместе с реальным применением таймаута.
    Решение спит 0.4 с; при ``timeout_seconds = 5`` это ``AC``, при ``0.05`` —
    падение по таймауту. Кэш, знающий только хеши решения и тестов, отдавал на
    второй прогон вердикт, порождённый совсем другими условиями.
    """
    import subprocess
    import sys

    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "1").write_text("4", encoding="utf-8")
    (project / "tests" / "1.clue").write_text("5", encoding="utf-8")
    (project / "solution.py").write_text(
        "import time\ntime.sleep(0.4)\nprint(int(input()) + 1)\n", encoding="utf-8"
    )
    slow = tmp_path / "generous.toml"
    slow.write_text("[tool.stepik-grader]\ntimeout_seconds = 5.0\n", encoding="utf-8")
    strict = tmp_path / "strict.toml"
    strict.write_text("[tool.stepik-grader]\ntimeout_seconds = 0.05\n", encoding="utf-8")

    def run(config_path: pathlib.Path) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "stepik_grader",
                "--mode",
                "1",
                "--file",
                "solution.py",
                "--cache",
                "--config",
                str(config_path),
                "--output",
                "json",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
        payload: dict[str, object] = json.loads(completed.stdout.strip().splitlines()[-1])
        return payload

    generous = run(slow)
    assert [c["passed"] for c in generous["cases"]] == [True]  # type: ignore[union-attr]

    tightened = run(strict)
    assert [c["passed"] for c in tightened["cases"]] == [False], (  # type: ignore[union-attr]
        "вердикт взят из кэша, порождённого другим таймаутом"
    )


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

    cache.put(alive, "sa", "ta", {"verdict": "AC"}, env=_ENV)
    cache.put(gone, "sg", "tg", {"verdict": "AC"}, env=_ENV)
    gone.unlink()  # файл решения удалён

    assert cache.prune() == 1
    assert cache.get(alive, "sa", "ta", env=_ENV) == {"verdict": "AC"}  # живой цел
    assert cache.get(gone, "sg", "tg", env=_ENV) is None  # мёртвый выброшен

    # save() тоже пруниет: перезагруженный кэш не содержит мёртвой записи
    cache.save()
    reloaded = GraderCache(cache_dir=tmp_path / CACHE_DIR_NAME)
    assert reloaded.get(alive, "sa", "ta", env=_ENV) == {"verdict": "AC"}
    assert reloaded.get(gone, "sg", "tg", env=_ENV) is None


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
        cache.put(sol, f"s{i}", "t", {"i": i}, env=_ENV)

    assert cache.prune() == 2  # все файлы существуют → срабатывает только size-cap (5 → 3)
    # выброшены два самых старых по вставке (s0, s1); s2..s4 сохранены
    assert cache.get(paths[0], "s0", "t", env=_ENV) is None
    assert cache.get(paths[1], "s1", "t", env=_ENV) is None
    assert cache.get(paths[4], "s4", "t", env=_ENV) == {"i": 4}
