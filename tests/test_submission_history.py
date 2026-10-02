"""Сбор обратной связи платформы и глоссария (issue #1175/#1220).

Здесь проверяется не хранилище (оно в `test_history_schema_v4.py`), а **место
сбора**. Пока запись живёт в веб-адаптере, она собирает данные ровно одной
поверхности: подключат CLI или оркестратор прогона — и те пройдут мимо. Поэтому
отправка и запись — один вызов ядра (`core/submission.submit_and_record`), а
веб зовёт его вместо голого `submit_and_wait`.

Второе, что здесь закрыто, — согласие. История opt-in (ADR-0002), и у веба
поверх конфига лежит оверрайд `--serve --no-history`. Ядро не решает за
пользователя: путь к базе приходит параметром, `None` означает «не писать».
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from stepik_grader.core import history, stepik_client, submission
from stepik_grader.core.history import CaseRecord


def _result(status: str, **kwargs: Any) -> stepik_client.SubmissionResult:
    return stepik_client.SubmissionResult(status=status, **kwargs)


@pytest.fixture
def fake_submit(monkeypatch: pytest.MonkeyPatch):
    """Подменить сетевую отправку; вернуть управляемый вердикт платформы."""

    def _install(result: stepik_client.SubmissionResult) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        def _submit(session: Any, step_id: int, code: str, **kwargs: Any) -> Any:
            seen.update({"step_id": step_id, "code": code, **kwargs})
            return result

        monkeypatch.setattr(stepik_client, "submit_and_wait", _submit)
        return seen

    return _install


def _task(tmp_path: pathlib.Path, step_id: int = 42) -> pathlib.Path:
    task = tmp_path / "task"
    task.mkdir()
    (task / "meta.json").write_text(json.dumps({"step_id": step_id}), encoding="utf-8")
    return task


class TestCoreSubmissionPath:
    def test_verdict_lands_in_history(self, tmp_path: pathlib.Path, fake_submit) -> None:
        fake_submit(_result("correct", hint="Верно!", submission_id=9))
        db = tmp_path / history.HISTORY_DB_NAME
        task = _task(tmp_path)

        submission.submit_and_record(
            object(), 42, "print(1)", task_dir=task, base_dir=tmp_path, history_db=db
        )

        rows = history.read_stepik_submissions(db)
        assert [(r["step_id"], r["verdict"], r["submission_id"]) for r in rows] == [
            (42, "correct", 9)
        ]

    def test_result_is_returned_unchanged(self, tmp_path: pathlib.Path, fake_submit) -> None:
        """Запись — побочный эффект: вызывающий получает ровно ответ платформы."""
        fake_submit(_result("wrong", hint="Тест 3 не пройден"))

        result = submission.submit_and_record(
            object(), 42, "print(1)", history_db=tmp_path / history.HISTORY_DB_NAME
        )

        assert (result.status, result.hint) == ("wrong", "Тест 3 не пройден")

    def test_without_history_db_nothing_is_written(
        self, tmp_path: pathlib.Path, fake_submit
    ) -> None:
        """`None` — «не писать»: гейт согласия отрабатывает у вызывающего."""
        fake_submit(_result("correct"))
        db = tmp_path / history.HISTORY_DB_NAME

        submission.submit_and_record(object(), 42, "print(1)", task_dir=_task(tmp_path))

        assert not db.exists()

    def test_task_key_binds_the_verdict_to_our_run(
        self, tmp_path: pathlib.Path, fake_submit
    ) -> None:
        """Ключ считается по `meta.json`, тот же, что у прогона, — иначе не сойдётся."""
        fake_submit(_result("wrong"))
        db = tmp_path / history.HISTORY_DB_NAME
        task = _task(tmp_path)
        history.record_run(
            1, [CaseRecord(1, "AC")], db_path=db, task_key=history.task_key_for(task, tmp_path)
        )

        submission.submit_and_record(
            object(), 42, "print(1)", task_dir=task, base_dir=tmp_path, history_db=db
        )

        row = history.read_stepik_submissions(db)[0]
        assert row["task_key"] == "step:42"
        assert row["our_verdict"] == "AC"
        assert row["diverged"] == 1

    def test_without_task_dir_verdict_is_still_kept(
        self, tmp_path: pathlib.Path, fake_submit
    ) -> None:
        """Папки нет — сравнивать не с чем, но вердикт платформы терять нельзя."""
        fake_submit(_result("correct"))
        db = tmp_path / history.HISTORY_DB_NAME

        submission.submit_and_record(object(), 42, "print(1)", history_db=db)

        row = history.read_stepik_submissions(db)[0]
        assert (row["task_key"], row["run_id"], row["our_verdict"]) == ("", None, None)

    @pytest.mark.parametrize("status", ["evaluation", "error"])
    def test_non_terminal_status_is_not_recorded(
        self, tmp_path: pathlib.Path, fake_submit, status: str
    ) -> None:
        """`evaluation`/`error` — не ответ о решении: сравнивать их не с чем."""
        fake_submit(_result(status))
        db = tmp_path / history.HISTORY_DB_NAME

        submission.submit_and_record(object(), 42, "print(1)", history_db=db)

        assert history.read_stepik_submissions(db) == []

    def test_cancel_event_reaches_the_client(self, tmp_path: pathlib.Path, fake_submit) -> None:
        """Обёртка не съедает отмену ожидания вердикта (issue #797)."""
        import threading

        seen = fake_submit(_result("evaluation"))
        event = threading.Event()

        submission.submit_and_record(object(), 42, "print(1)", cancel_event=event)

        assert seen["cancel_event"] is event


class TestWebSubmitJob:
    """Веб зовёт общий путь ядра, а не собственную ветку записи."""

    def test_job_records_the_verdict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fake_submit
    ) -> None:
        from stepik_grader.core import oauth_flow
        from stepik_grader.web import runs, viewmodels

        fake_submit(_result("correct", submission_id=5))
        monkeypatch.setattr(
            oauth_flow, "try_create_session_without_browser", lambda secrets, path: object()
        )
        monkeypatch.setattr(oauth_flow, "load_secrets_dict", lambda path: {})
        db = tmp_path / history.HISTORY_DB_NAME
        # Через настоящий гейт, а не подменой самой функции: проверяется в том
        # числе то, что веб спрашивает разрешение, а не пишет безусловно.
        monkeypatch.setattr(viewmodels, "_web_record_history", True)
        monkeypatch.setenv("STEPIK_GRADER_HISTORY_DB", str(db))
        task = _task(tmp_path)
        job = runs.Job("submit", "stepik_submit")

        runs._run_stepik_submit_job(
            job,
            "print(1)",
            {
                "step_id": 42,
                "secrets_path": str(tmp_path / "secrets.json"),
                "task_dir": str(task),
                "workspace": str(tmp_path),
            },
            "ru",
        )

        assert job.status == "done"
        assert [r["task_key"] for r in history.read_stepik_submissions(db)] == ["step:42"]

    def test_history_off_means_nothing_written(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fake_submit
    ) -> None:
        """`--serve --no-history` — тумблер один на весь журнал, второго нет."""
        from stepik_grader.core import oauth_flow
        from stepik_grader.web import runs, viewmodels

        fake_submit(_result("correct"))
        monkeypatch.setattr(
            oauth_flow, "try_create_session_without_browser", lambda secrets, path: object()
        )
        monkeypatch.setattr(oauth_flow, "load_secrets_dict", lambda path: {})
        monkeypatch.setattr(viewmodels, "_web_record_history", False)
        db = tmp_path / history.HISTORY_DB_NAME
        job = runs.Job("submit", "stepik_submit")

        runs._run_stepik_submit_job(
            job,
            "print(1)",
            {"step_id": 42, "secrets_path": str(tmp_path / "secrets.json")},
            "ru",
        )

        assert job.status == "done"
        assert not db.exists()


class TestIsolationIsRecorded:
    def test_web_run_writes_the_current_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Без отметки сравнение времён между прогонами вводит в заблуждение."""
        from stepik_grader.core import history_recording

        assert history_recording.current_isolation() == history.ISOLATION_NONE

    def test_sandbox_backend_is_named_not_flattened(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Гарантии backend'ов разные (SECURITY.md) — «под песочницей» мало."""
        from stepik_grader.core import history_recording, runner

        class _FakeSandbox:
            backend_name = "bwrap"

        monkeypatch.setattr(runner, "_RUNNER", _FakeSandbox())

        assert history_recording.current_isolation() == "bwrap"

    def test_cli_label_and_history_agree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Одно значение на два потребителя: CLI-статистика и история."""
        from stepik_grader.cli import commands
        from stepik_grader.core import history_recording, runner

        class _FakeSandbox:
            backend_name = "sandbox-exec"

        monkeypatch.setattr(runner, "_RUNNER", _FakeSandbox())

        assert commands._isolation_label() == history_recording.current_isolation()
