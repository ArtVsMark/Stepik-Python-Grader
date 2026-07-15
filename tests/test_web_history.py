"""Tests for web → history recording (issue #395).

Раздел «Подучить» в ``--serve`` наполняется только если web-грейдинг пишет в
``.grader_history.db``. Здесь проверяем, что ``grade_path``/``grade_benchmark``/
``grade_microbench`` пишут прогоны с ``source="web"`` под гейтом записи, что
``--serve`` включает историю по умолчанию (оверрайд поверх frozen ``CONFIG``),
и что отмена/выключенный флаг не пишут.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.core import history
from stepik_grader.web import viewmodels


@pytest.fixture(autouse=True)
def _reset_web_history_override():
    """Оверрайд истории — глобальный; сбрасываем после каждого теста."""
    yield
    viewmodels.set_web_record_history(None)


def _make_task(root: pathlib.Path, name: str = "task1.py") -> pathlib.Path:
    """Простое stdin-решение (a+b) с двумя named-кейсами."""
    sol = root / name
    sol.write_text("a, b = int(input()), int(input())\nprint(a + b)\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "input_1.txt").write_text("3\n7\n", encoding="utf-8")
    (tests / "expected_1.txt").write_text("10\n", encoding="utf-8")
    (tests / "input_2.txt").write_text("5\n5\n", encoding="utf-8")
    (tests / "expected_2.txt").write_text("10\n", encoding="utf-8")
    return sol


def _db(root: pathlib.Path) -> pathlib.Path:
    return root / history.HISTORY_DB_NAME


def test_grade_path_file_records_mode1(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path)
    viewmodels.set_web_record_history(True)

    viewmodels.grade_path(sol)

    runs = history.read_recent_runs(_db(tmp_path))
    assert len(runs) == 1
    run = runs[0]
    assert run["mode"] == 1
    assert run["source"] == "web"
    assert run["solution_name"] == "task1.py"
    assert run["solution_hash"]  # непустой sha256
    assert len(run["cases"]) == 2
    assert all(c["verdict"] == "AC" for c in run["cases"])


def test_grade_path_dir_records_mode2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path, "task1.py")
    # второе решение в той же папке (общий tests/)
    (tmp_path / "task2.py").write_text(
        "a, b = int(input()), int(input())\nprint(a + b)\n", encoding="utf-8"
    )
    viewmodels.set_web_record_history(True)

    viewmodels.grade_path(tmp_path)  # папка → режим 2

    runs = history.read_recent_runs(_db(tmp_path))
    assert len(runs) == 1
    assert runs[0]["mode"] == 2
    assert runs[0]["source"] == "web"
    # 2 решения × 2 кейса = 4 агрегированных case-записи
    assert len(runs[0]["cases"]) == 4


def test_grade_benchmark_records_mode3(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path)
    viewmodels.set_web_record_history(True)

    viewmodels.grade_benchmark(tmp_path, repeats=3)

    runs = history.read_recent_runs(_db(tmp_path))
    assert len(runs) == 1
    assert runs[0]["mode"] == 3
    assert runs[0]["source"] == "web"


def test_grade_microbench_records_mode4(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path)
    viewmodels.set_web_record_history(True)

    viewmodels.grade_microbench(tmp_path, number=10)

    runs = history.read_recent_runs(_db(tmp_path))
    assert len(runs) == 1
    assert runs[0]["mode"] == 4
    assert runs[0]["source"] == "web"


def test_disabled_records_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path)
    viewmodels.set_web_record_history(False)

    viewmodels.grade_path(sol)

    assert not _db(tmp_path).exists()


def test_default_override_falls_back_to_config(tmp_path, monkeypatch) -> None:
    """None-оверрайд → берём ``CONFIG.record_history`` (opt-in, по умолчанию False)."""
    if viewmodels.CONFIG.record_history:
        pytest.skip("CONFIG.record_history включён в этом окружении — фолбэк не проверить")
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path)
    viewmodels.set_web_record_history(None)  # фолбэк на CONFIG (frozen — не мутируем)

    viewmodels.grade_path(sol)

    assert not _db(tmp_path).exists()


def test_grade_path_records_lint_when_ruff_available(tmp_path, monkeypatch) -> None:
    """issue #403: web-грейд пишет lint-нарушения в историю (ruff — opt-in extra)."""
    if not viewmodels._ruff_available_cached():
        pytest.skip("ruff не установлен (extra [lint]) — lint в историю не пишется")
    monkeypatch.chdir(tmp_path)
    # F401: неиспользуемый импорт — гарантированное нарушение.
    sol = tmp_path / "task1.py"
    sol.write_text("import os\na, b = int(input()), int(input())\nprint(a + b)\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "input_1.txt").write_text("3\n7\n", encoding="utf-8")
    (tests / "expected_1.txt").write_text("10\n", encoding="utf-8")
    viewmodels.set_web_record_history(True)

    viewmodels.grade_path(sol)

    runs = history.read_recent_runs(_db(tmp_path))
    # read_recent_runs отдаёт lint как список rule_code-строк (как ждёт insights).
    assert "F401" in runs[0]["lint"]


def test_cancelled_run_is_not_recorded(tmp_path, monkeypatch) -> None:
    """Отменённый прогон не пишет частичную историю (issue #395)."""
    import threading

    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path)
    viewmodels.set_web_record_history(True)

    ev = threading.Event()
    ev.set()  # отменён ещё до старта
    viewmodels.grade_path(sol, cancel_event=ev)

    assert not _db(tmp_path).exists()
