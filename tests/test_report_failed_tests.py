"""Сводка упавших тестов в комментарии PR (issue #1382).

Предмет — разбор и форма сводки, а не сеть: в GitHub не ходит ни один тест.
Главное свойство проверяется первым: сводка **обновляет** прежний комментарий,
а не добавляет новый. Иначе на пятом красном прогоне тред PR перестанет
читаться, и механизм, заведённый ради ответа на вопрос, начнёт мешать его
задавать.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "report_failed_tests.py"
    spec = importlib.util.spec_from_file_location("report_failed_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("report_failed_tests", module)
    spec.loader.exec_module(module)
    return module


reporter = _load()


def _report(directory: pathlib.Path, name: str, body: str) -> pathlib.Path:
    path = directory / name
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">{body}'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


_FAILING_CASE = (
    '<testcase classname="tests.test_web" name="test_grade">'
    '<failure message="AssertionError: 1 != 2">трассировка</failure></testcase>'
)


def test_failure_is_named_with_job_test_and_message(tmp_path: pathlib.Path) -> None:
    _report(tmp_path, "test-results-macos-latest-3.13.xml", _FAILING_CASE)

    (failure,) = reporter.collect(tmp_path)

    assert failure.job == "macos-latest-3.13"
    assert failure.test == "tests/test_web.py::test_grade"
    assert failure.kind == "failure"
    assert failure.message == "AssertionError: 1 != 2"


def test_class_based_test_keeps_its_class(tmp_path: pathlib.Path) -> None:
    """Адрес обязан запускаться как есть — иначе им нельзя воспроизвести."""
    _report(
        tmp_path,
        "test-results-ubuntu-latest-3.12.xml",
        '<testcase classname="tests.test_runs.TestSubmitJob" name="test_folder">'
        '<failure message="Failed: временный файл"/></testcase>',
    )

    (failure,) = reporter.collect(tmp_path)

    assert failure.test == "tests/test_runs.py::TestSubmitJob::test_folder"


def test_error_counts_too(tmp_path: pathlib.Path) -> None:
    """Сломавшаяся фикстура — тот же вопрос «что именно», что и падение."""
    _report(
        tmp_path,
        "test-results-windows-latest-3.12.xml",
        '<testcase classname="tests.test_cli" name="test_menu">'
        '<error message="OSError: занято"/></testcase>',
    )

    (failure,) = reporter.collect(tmp_path)

    assert failure.kind == "error"


def test_passing_cases_are_not_reported(tmp_path: pathlib.Path) -> None:
    _report(
        tmp_path,
        "test-results-ubuntu-latest-3.13.xml",
        '<testcase classname="tests.test_ok" name="test_fine"/>',
    )

    assert reporter.collect(tmp_path) == []


def test_truncated_report_is_not_a_crash(tmp_path: pathlib.Path) -> None:
    """Отчёт пишется на аварийном пути и вполне может оборваться."""
    (tmp_path / "test-results-macos-latest-3.12.xml").write_text("<testsuites", encoding="utf-8")

    assert reporter.collect(tmp_path) == []


def test_reports_from_several_jobs_are_merged(tmp_path: pathlib.Path) -> None:
    _report(tmp_path, "test-results-macos-latest-3.12.xml", _FAILING_CASE)
    _report(tmp_path, "test-results-macos-latest-3.13.xml", _FAILING_CASE)

    assert {failure.job for failure in reporter.collect(tmp_path)} == {
        "macos-latest-3.12",
        "macos-latest-3.13",
    }


def test_overflow_is_counted_not_silently_cut() -> None:
    """Молчаливая обрезка читалась бы как «это всё»."""
    failures = [
        reporter.Failure("ubuntu", f"tests/test_{i}.py::test", "failure", "…") for i in range(30)
    ]

    text = reporter.render(failures, limit=5)

    assert "и ещё 25" in text
    assert text.count("tests/test_") == 5


def test_red_run_without_failures_says_so() -> None:
    """«Тестов не упало» при красном прогоне — это про смерть до тестов."""
    text = reporter.render([])

    assert "ни один тест не назвал себя упавшим" in text


def test_marker_is_the_first_line() -> None:
    """По нему сводка находится в следующий раз — не по автору и не по тексту."""
    assert reporter.render([]).splitlines()[0] == reporter.MARKER


def test_without_apply_nothing_is_written(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("написали в GitHub без --apply")

    monkeypatch.setattr(reporter.gh_rest, "comment_issue", refuse)
    monkeypatch.setattr(reporter.gh_rest, "update_comment", refuse)
    _report(tmp_path, "test-results-ubuntu-latest-3.12.xml", _FAILING_CASE)

    assert reporter.main(["--dir", str(tmp_path), "--pr", "1"]) == 0
    assert reporter.MARKER in capsys.readouterr().out


def test_existing_summary_is_updated_not_duplicated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное свойство: один комментарий на PR, сколько бы ни было прогонов."""
    updated: list[int] = []
    monkeypatch.setattr(
        reporter.gh_rest,
        "issue_comments",
        lambda *a, **k: [
            {"id": 7, "body": "чужой комментарий"},
            {"id": 9, "body": f"{reporter.MARKER}\n\nстарая сводка"},
        ],
    )
    monkeypatch.setattr(
        reporter.gh_rest,
        "comment_issue",
        lambda *a, **k: pytest.fail("добавлен второй комментарий вместо обновления"),
    )
    monkeypatch.setattr(
        reporter.gh_rest, "update_comment", lambda _repo, ident, _text, **k: updated.append(ident)
    )
    _report(tmp_path, "test-results-ubuntu-latest-3.12.xml", _FAILING_CASE)

    reporter.main(["--dir", str(tmp_path), "--pr", "1", "--apply"])

    assert updated == [9]


def test_first_summary_is_created(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[int] = []
    monkeypatch.setattr(reporter.gh_rest, "issue_comments", lambda *a, **k: [])
    monkeypatch.setattr(
        reporter.gh_rest, "comment_issue", lambda _repo, number, _text, **k: created.append(number)
    )
    _report(tmp_path, "test-results-ubuntu-latest-3.12.xml", _FAILING_CASE)

    reporter.main(["--dir", str(tmp_path), "--pr", "42", "--apply"])

    assert created == [42]
