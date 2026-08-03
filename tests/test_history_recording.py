"""Tests for core/history_recording.py — сборка записей истории (issue #395/#403).

Хелперы вынесены из cli/commands.py, чтобы CLI и web писали историю одним кодом.
Здесь — прямые юнит-тесты преобразователей (cases/lint/db-path).
"""

from __future__ import annotations

import dataclasses
import pathlib

from stepik_grader.core import history, history_recording
from stepik_grader.core.lint import Violation


def test_cases_from_test_results_maps_verdict_and_time() -> None:
    cases = [
        {"passed": True, "time": 0.01},
        {"passed": False, "verdict": "WA", "time": 0.02},
        {"passed": False, "verdict": "RE", "error": "ZeroDivisionError: x", "time": 0.0},
    ]
    records = history_recording.cases_from_test_results(cases)
    assert [r.case_no for r in records] == [1, 2, 3]
    assert records[0].verdict == "AC"
    assert records[0].time_ms == 10.0  # 0.01s → 10ms
    assert records[1].verdict == "WA"
    assert records[2].verdict == "RE"
    # failure_kind проставлен (таксономия), точное значение — забота insights.
    assert records[2].failure_kind is not None


def test_cases_from_bench_results_one_record_per_solution() -> None:
    results = {
        pathlib.Path("fast.py"): {"median": 1.0, "verdict": "SIMILAR"},
        pathlib.Path("slow.py"): {"median": 2.0, "verdict": "MUCH_SLOWER"},
        pathlib.Path("broken.py"): {"error": "SyntaxError"},
    }
    records = history_recording.cases_from_bench_results(results)
    verdicts = [r.verdict for r in records]
    assert verdicts == ["SIMILAR", "MUCH_SLOWER", "ERR"]


def test_lint_records_from_violations_drops_column() -> None:
    """issue #403: Violation → LintRecord (rule_code/line_no/message; column не пишется)."""
    violations = [
        Violation(rule_code="F401", line_no=1, message="unused import", column=5),
        Violation(rule_code="E501", line_no=10, message="line too long", column=80),
    ]
    records = history_recording.lint_records_from_violations(violations)
    assert all(isinstance(r, history.LintRecord) for r in records)
    assert [(r.rule_code, r.line_no, r.message) for r in records] == [
        ("F401", 1, "unused import"),
        ("E501", 10, "line too long"),
    ]


def test_lint_records_from_violations_empty() -> None:
    assert history_recording.lint_records_from_violations([]) == []


# ---------------------------------------------------------------------------
# Резолв пути базы истории — issue #818
#
# Раньше база лежала строго в Path.cwd(). Рекомендованный сценарий — запуск из
# папки задачи, поэтому у студента заводилась своя база на каждую задачу, и
# «Подучить», «Прогресс», серия и бейджи не наполнялись никогда. Проверено
# прогоном: два прогона из соседних папок дали два файла по 40 КБ, а
# `--insights` из второй показал одну задачу вместо двух.
# ---------------------------------------------------------------------------


def _no_env_override(monkeypatch) -> None:
    """Снять STEPIK_GRADER_HISTORY_DB — иначе тесты резолва проверяли бы её.

    Переменную ставит autouse-фикстура conftest (изоляция реальной базы,
    issue #818); здесь же проверяются остальные ступени резолва.
    """
    monkeypatch.delenv("STEPIK_GRADER_HISTORY_DB", raising=False)


def _set_config(monkeypatch, **fields: object) -> None:
    """Подменить CONFIG целиком: dataclass frozen, поле присвоить нельзя."""
    _no_env_override(monkeypatch)
    monkeypatch.setattr(
        history_recording, "CONFIG", dataclasses.replace(history_recording.CONFIG, **fields)
    )


def _fake_home(monkeypatch, home: pathlib.Path) -> None:
    """Изолировать домашнюю папку — иначе поиск базы вверх цепляет реальную."""
    _no_env_override(monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))


def test_env_override_wins_over_everything(tmp_path, monkeypatch) -> None:
    """issue #818: переменная окружения — верхняя ступень резолва.

    Без неё собственный набор тестов писал в РЕАЛЬНУЮ базу пользователя, а
    тесты `--purge-history` её удаляли: изоляции через `chdir` перестало
    хватать, когда база переехала из cwd в домашнюю папку.
    """
    _fake_home(monkeypatch, tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    (tmp_path / history.HISTORY_DB_NAME).write_bytes(b"")  # и рядом есть база
    _set_config(monkeypatch, history_db_path=str(tmp_path / "from-config.db"))
    forced = tmp_path / "forced" / "history.db"
    monkeypatch.setenv("STEPIK_GRADER_HISTORY_DB", str(forced))

    assert history_recording.default_history_db_path() == forced


def test_existing_db_nearby_is_reused(tmp_path, monkeypatch) -> None:
    """Уже накопленная история продолжает пополняться — она не осиротеет."""
    _fake_home(monkeypatch, tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / history.HISTORY_DB_NAME
    existing.write_bytes(b"")
    assert history_recording.default_history_db_path() == existing


def test_existing_db_found_upwards(tmp_path, monkeypatch) -> None:
    """База ищется и выше по дереву: запуск из папки задачи попадает в общую
    базу курса, а не заводит собственную рядом с решением."""
    _fake_home(monkeypatch, tmp_path)
    course = tmp_path / "course"
    task = course / "module1" / "task3"
    task.mkdir(parents=True)
    existing = course / history.HISTORY_DB_NAME
    existing.write_bytes(b"")
    monkeypatch.chdir(task)
    assert history_recording.default_history_db_path() == existing


def test_falls_back_to_user_wide_db(tmp_path, monkeypatch) -> None:
    """Нет базы рядом — пишем в единую пользовательскую, а не в текущую папку."""
    _fake_home(monkeypatch, tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    resolved = history_recording.default_history_db_path()
    assert resolved == tmp_path / "home" / ".stepik-grader" / "history.db"
    assert resolved != tmp_path / history.HISTORY_DB_NAME


def test_configured_path_wins(tmp_path, monkeypatch) -> None:
    """Явная настройка перекрывает и найденную базу, и пользовательскую."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / history.HISTORY_DB_NAME).write_bytes(b"")
    custom = tmp_path / "custom" / "learning.db"
    _set_config(monkeypatch, history_db_path=str(custom))
    assert history_recording.default_history_db_path() == custom


def test_configured_relative_path_restores_old_behaviour(tmp_path, monkeypatch) -> None:
    """Прежнее «база в текущей папке» возвращается строкой в конфиге.

    Это и есть «прежнее поведение доступно явным флагом» из критерия приёмки:
    относительный путь резолвится от cwd.
    """
    monkeypatch.chdir(tmp_path)
    _set_config(monkeypatch, history_db_path=history.HISTORY_DB_NAME)
    assert history_recording.default_history_db_path() == pathlib.Path(history.HISTORY_DB_NAME)


def test_find_existing_returns_none_without_db(tmp_path, monkeypatch) -> None:
    _fake_home(monkeypatch, tmp_path)
    assert history_recording.find_existing_history_db(tmp_path) is None


def test_search_does_not_escape_home(tmp_path, monkeypatch) -> None:
    """Обход не выходит за домашнюю папку.

    Без границы поиск доходил до корня диска: из временного каталога под
    ``~/AppData`` находилась посторонняя ``~/.grader_history.db`` — и вся
    история писалась бы туда (поймано этим же тестом до фикса).
    """
    home = tmp_path / "home"
    outsider = tmp_path / history.HISTORY_DB_NAME  # ВЫШЕ home
    outsider.write_bytes(b"")
    work = home / "projects" / "task"
    work.mkdir(parents=True)
    _fake_home(monkeypatch, home)

    assert history_recording.find_existing_history_db(work) is None
