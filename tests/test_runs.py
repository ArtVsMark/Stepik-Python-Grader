"""Tests for web/runs.py — async job-модель (issue #262).

Прямые вызовы submit_job/get_job/cancel_job без HTTP-слоя (тот покрыт
tests/test_web.py::TestRunsApi) — быстрее и изолированнее для отладки
job-lifecycle логики.
"""

from __future__ import annotations

import dataclasses
import pathlib
import threading

import pytest

from stepik_grader.web import runs
from tests._wait import wait_until


def _make_task(tmp_path: pathlib.Path, body: str, *, name: str = "task.py") -> pathlib.Path:
    sol = tmp_path / name
    sol.write_text(body, encoding="utf-8")
    tests = tmp_path / "tests"
    if not tests.is_dir():
        tests.mkdir()
        (tests / "input_1.txt").write_text("4\n", encoding="utf-8")
        (tests / "expected_1.txt").write_text("5\n", encoding="utf-8")
    return sol


def _poll_until_terminal(job_id: str, *, timeout: float = 15.0) -> dict:
    def _terminal() -> dict | None:
        job = runs.get_job(job_id)
        assert job is not None
        data = job.to_status_dict()
        return data if data["status"] in ("done", "error", "cancelled") else None

    data = wait_until(_terminal, timeout=timeout)
    if data is None:
        raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout}s")
    return data


class TestSubmitJobBench:
    def test_reaches_done_with_result(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 2, "lang": "ru"})

        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["mode"] == "bench"
        assert data["progress"]["total"] > 0
        assert data["progress"]["done"] == data["progress"]["total"]

    def test_progress_visible_before_done(self, tmp_path: pathlib.Path) -> None:
        # Solution slow enough that at least one poll should catch it mid-run.
        sol = _make_task(tmp_path, "import time\ntime.sleep(0.05)\nprint(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 5, "lang": "ru"})

        # progress.total выставляется, как только воркер узнал число тиков —
        # ждём этого момента (решение спит 0.05с, т.е. total виден до завершения).
        def _total_known() -> dict | None:
            data = job.to_status_dict()
            return data if data["progress"]["total"] > 0 else None

        data = wait_until(_total_known, timeout=10.0)
        assert data is not None and data["progress"]["total"] > 0


class TestSubmitJobTests:
    """issue #297 — корректность режима 1 через async job (mode="tests")."""

    def test_reaches_done_with_correctness_result(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("tests", sol, {"lang": "ru"})

        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["mode"] == "tests"
        assert data["result"]["rows"][0]["status"] == "OK"
        assert data["progress"]["total"] == 1  # one tick per test case (1 case here)
        assert data["progress"]["done"] == 1

    def test_code_in_body_executes_without_touching_target_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        """AC1: «Проверить» исполняет код из тела, НЕ пишет в целевой файл."""
        # На диске — НЕВЕРНОЕ решение; в теле — верное. Результат отражает тело,
        # а файл на диске остаётся нетронутым (никакой гонки save→grade).
        sol = _make_task(tmp_path, "print(999)\n")
        original_disk = sol.read_text(encoding="utf-8")

        job = runs.submit_job("tests", sol, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["rows"][0]["status"] == "OK"  # тело верное
        assert sol.read_text(encoding="utf-8") == original_disk  # диск не тронут
        # Временный файл убран — только исходный task.py остался.
        assert {p.name for p in tmp_path.glob("*.py")} == {"task.py"}

    def test_folder_path_with_code_grades_single_temp_file(self, tmp_path: pathlib.Path) -> None:
        """Новый (несохранённый) код: path = папка, temp кладётся в неё, tests/
        резолвится там же — целевых файлов не появляется."""
        # Папка с tests/, но без единого .py-решения на диске.
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "input_1.txt").write_text("4\n", encoding="utf-8")
        (tests / "expected_1.txt").write_text("5\n", encoding="utf-8")

        job = runs.submit_job("tests", tmp_path, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["rows"][0]["status"] == "OK"
        assert list(tmp_path.glob("*.py")) == []  # ни одного .py не осталось на диске


class TestSubmitJobMicrobench:
    def test_reaches_done_with_result(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("microbench", sol, {"number": 100, "lang": "ru"})

        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["mode"] == "microbench"
        assert data["progress"]["total"] == 1  # one tick per solution


class TestTraceCancel:
    """issue #422: _run_trace_job должен финализировать отменённую трассировку
    как cancelled, а не done (trace_code сам cancel_event не прерывает)."""

    def test_trace_cancelled_when_cancel_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runs, "trace_code", lambda code, stdin, timeout: {"steps": []})
        job = runs.Job("trace-cancel", "trace")
        job.cancel_event.set()

        runs._run_trace_job(job, "print(1)", "", "ru")

        assert job.status == "cancelled"
        assert job.result is None

    def test_trace_done_when_not_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runs, "trace_code", lambda code, stdin, timeout: {"steps": [1]})
        job = runs.Job("trace-done", "trace")

        runs._run_trace_job(job, "print(1)", "", "ru")

        assert job.status == "done"
        assert job.result == {"steps": [1]}


class TestHintAndSubmitCancel:
    """issue #797 (QA-04): отмена hint- и submit-job'ы больше не no-op.

    Их тела не читали `cancel_event` вовсе: клик «Отмена» помечал job'у
    отменённой в реестре, но воркер продолжал ходить к AI-провайдеру или ждать
    вердикт Stepik, а потом публиковал результат поверх отменённой job'ы.
    """

    def test_hint_cancelled_before_provider_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Отмена в очереди executor'а: к провайдеру не идём вообще."""
        called: list[object] = []
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: called.append(fc))
        job = runs.Job("hint-cancel", "hint")
        job.cancel_event.set()

        runs._run_hint_job(job, "print(1)", {"case": {}}, "ru")

        assert job.status == "cancelled"
        assert job.result is None
        assert called == [], "запрос к AI-провайдеру не должен уходить у отменённой job'ы"

    def test_hint_cancelled_during_provider_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Отмена во время запроса: результат не публикуется поверх «Отмены»."""

        job = runs.Job("hint-cancel-mid", "hint")

        def _slow_hint(fc: object, cfg: object) -> str:
            job.cancel_event.set()  # пользователь нажал «Отмена», пока шёл запрос
            return "подсказка"

        monkeypatch.setattr(runs, "explain_failure", _slow_hint)

        runs._run_hint_job(job, "print(1)", {"case": {}}, "ru")

        assert job.status == "cancelled"
        assert job.result is None

    def test_hint_done_when_not_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Обычный путь не затронут."""
        monkeypatch.setattr(runs, "explain_failure", lambda fc, cfg: "подсказка")
        job = runs.Job("hint-done", "hint")

        runs._run_hint_job(job, "print(1)", {"case": {}}, "ru")

        assert job.status == "done"
        assert job.result is not None
        assert job.result["hint"] == "подсказка"

    def test_submit_passes_cancel_event_and_marks_cancelled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отмена доезжает до опроса Stepik, а job финализируется как cancelled.

        Отменяется ожидание вердикта, а не сама отправка: попытка на платформе
        уже создана — поэтому проверяется и то, что событие ДОШЛО до клиента.
        """
        seen: dict[str, object] = {}

        def _fake_submit(session, step_id, code, *, cancel_event=None):
            seen["cancel_event"] = cancel_event
            cancel_event.set()  # вердикт не дождались, пользователь отменил
            from stepik_grader.core.stepik_client import SubmissionResult

            return SubmissionResult(status="evaluation", hint="", submission_id=1)

        # Импорты в теле job'ы ленивые, поэтому патчим исходные модули.
        from stepik_grader.core import oauth_flow, stepik_client

        monkeypatch.setattr(stepik_client, "submit_and_wait", _fake_submit)
        monkeypatch.setattr(
            oauth_flow, "try_create_session_without_browser", lambda secrets, path: object()
        )
        monkeypatch.setattr(oauth_flow, "load_secrets_dict", lambda path: {})
        job = runs.Job("submit-cancel", "stepik_submit")

        runs._run_stepik_submit_job(
            job, "print(1)", {"step_id": 1, "secrets_path": "secrets.json"}, "ru"
        )

        assert seen["cancel_event"] is job.cancel_event, "cancel_event должен доезжать до клиента"
        assert job.status == "cancelled"
        assert job.result is None


class TestCancelJob:
    def test_cancel_running_job_marks_cancelled(self, tmp_path: pathlib.Path) -> None:
        """issue #296: отмена — отдельный терминальный статус, не "error"."""
        sol = _make_task(tmp_path, "import time\ntime.sleep(30)\nprint(input())\n")
        job = runs.submit_job("bench", sol, {"repeats": 20, "lang": "ru"})

        # wait until the worker actually starts the subprocess (status='running')
        assert wait_until(lambda: job.status == "running"), "worker did not start the job"
        assert runs.cancel_job(job.id) is True

        data = _poll_until_terminal(job.id)
        assert data["status"] == "cancelled"
        assert data["message_id"] == "run_cancelled"

    def test_cancel_unknown_job_returns_false(self) -> None:
        assert runs.cancel_job("no-such-id") is False

    def test_cancel_already_done_job_returns_false(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 1, "lang": "ru"})
        _poll_until_terminal(job.id)

        assert runs.cancel_job(job.id) is False

    def test_cancel_already_cancelled_job_returns_false(self, tmp_path: pathlib.Path) -> None:
        """Повторный cancel уже отменённой job'ы — тоже False (issue #296)."""
        sol = _make_task(tmp_path, "import time\ntime.sleep(30)\nprint(input())\n")
        job = runs.submit_job("bench", sol, {"repeats": 20, "lang": "ru"})

        assert wait_until(lambda: job.status == "running"), "worker did not start the job"
        assert runs.cancel_job(job.id) is True
        _poll_until_terminal(job.id)

        assert runs.cancel_job(job.id) is False


class TestGetJob:
    def test_unknown_id_returns_none(self) -> None:
        assert runs.get_job("no-such-id") is None


class TestConcurrentJobsDoNotMix:
    def test_two_parallel_jobs_keep_independent_results(self, tmp_path: pathlib.Path) -> None:
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        sol_a = _make_task(dir_a, "print(int(input()) + 1)\n", name="task.py")
        # Crashes (RE, error field set) -- run_benchmark short-circuits to an
        # "ERR" row for this one, unlike sol_a's clean run.
        sol_b = _make_task(dir_b, "raise ValueError('boom')\n", name="task.py")

        job_a = runs.submit_job("bench", sol_a, {"repeats": 2, "lang": "ru"})
        job_b = runs.submit_job("bench", sol_b, {"repeats": 2, "lang": "ru"})

        data_a = _poll_until_terminal(job_a.id)
        data_b = _poll_until_terminal(job_b.id)

        assert data_a["status"] == "done"
        assert data_a["result"]["rows"][0]["verdict"] != "ERR"
        assert data_b["status"] == "done"
        # issue #729: падающее решение отсеивает пре-флайт до замера — SKIPPED,
        # а не ERR по факту первого запуска внутри бенчмарка.
        assert data_b["result"]["rows"][0]["verdict"] == "SKIPPED"


class TestInlineCode:
    def test_code_overrides_path_content(self, tmp_path: pathlib.Path) -> None:
        # On-disk solution is WRONG; inline `code` is the correct one -- the
        # graded result must reflect `code`, not what's on disk at `path`.
        sol = _make_task(tmp_path, "print(999)\n")
        job = runs.submit_job(
            "bench",
            sol,
            {"repeats": 1, "lang": "ru"},
            code="print(int(input()) + 1)\n",
        )

        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["rows"][0]["verdict"] != "ERR"

    def test_temp_file_cleaned_up_after_job_done(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n")
        job = runs.submit_job(
            "bench", sol, {"repeats": 1, "lang": "ru"}, code="print(int(input()) + 1)\n"
        )
        _poll_until_terminal(job.id)

        # issue #605: cleanup runs BEFORE the terminal status is published, so a
        # poller that has just seen "done" must never observe a stray temp .py
        # (previously the unlink ran after job.status="done" — a visibility race).
        py_files = {p.name for p in tmp_path.glob("*.py")}
        assert py_files == {"task.py"}


class TestRobustUnlink:
    """issue #605: temp-файл inline-кода удаляется устойчиво к Windows-блокировке."""

    def test_retries_then_succeeds_on_transient_lock(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "locked.py"
        target.write_text("x", encoding="utf-8")
        real_unlink = pathlib.Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self: pathlib.Path, *a: object, **k: object) -> None:
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("file is locked by another process")
            real_unlink(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "unlink", flaky_unlink)
        runs._robust_unlink(target, attempts=5, delay=0)  # delay=0 → тест не спит

        assert calls["n"] == 3
        assert not target.exists()

    def test_gives_up_silently_after_attempts(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "stuck.py"
        target.write_text("x", encoding="utf-8")
        calls = {"n": 0}

        def always_locked(self: pathlib.Path, *a: object, **k: object) -> None:
            calls["n"] += 1
            raise PermissionError("permanently locked")

        monkeypatch.setattr(pathlib.Path, "unlink", always_locked)
        # best-effort: не должно бросить, даже если файл так и не удалился.
        runs._robust_unlink(target, attempts=3, delay=0)

        assert calls["n"] == 3  # все попытки исчерпаны, без исключения

    def test_missing_file_is_noop(self, tmp_path: pathlib.Path) -> None:
        # FileNotFoundError (файл уже удалён) — тихий успех, без ретраев/исключения.
        runs._robust_unlink(tmp_path / "does-not-exist.py", attempts=3, delay=0)


class TestTtlSweep:
    def test_expired_job_is_swept_on_next_access(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 1, "lang": "ru"})
        _poll_until_terminal(job.id)

        # Simulate TTL expiry without sleeping 15 real minutes (issue #408:
        # TTL меряется от completed_at — момента перехода в терминал, не от
        # created_at/постановки в очередь).
        assert job.completed_at is not None
        job.completed_at -= runs._JOB_TTL_SECONDS + 1

        assert runs.get_job(job.id) is None

    def test_expired_cancelled_job_is_swept_on_next_access(self, tmp_path: pathlib.Path) -> None:
        """issue #296: "cancelled" — тоже терминальный статус для TTL-уборки,
        не только "done"/"error"."""
        sol = _make_task(tmp_path, "import time\ntime.sleep(30)\nprint(input())\n")
        job = runs.submit_job("bench", sol, {"repeats": 20, "lang": "ru"})
        assert wait_until(lambda: job.status == "running"), "worker did not start the job"
        assert runs.cancel_job(job.id) is True
        data = _poll_until_terminal(job.id)
        assert data["status"] == "cancelled"

        assert job.completed_at is not None
        job.completed_at -= runs._JOB_TTL_SECONDS + 1

        assert runs.get_job(job.id) is None

    def test_long_job_kept_full_ttl_after_completion(self, tmp_path: pathlib.Path) -> None:
        """issue #408: TTL меряется от ЗАВЕРШЕНИЯ, не от постановки в очередь —
        job, поставленный в очередь давно (дольше TTL назад), но завершившийся
        только что, доступен ещё TTL после финиша (раньше выметался сразу после
        долгого bench, и следующий GET /api/v1/runs/{id} возвращал 404)."""
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 1, "lang": "ru"})
        _poll_until_terminal(job.id)

        # Долгий прогон: created_at задолго до TTL, но completed_at свежий.
        # Старим ТОЛЬКО created_at — уборка не должна тронуть job.
        job.created_at -= 10 * runs._JOB_TTL_SECONDS

        assert runs.get_job(job.id) is job  # не выметен: TTL идёт от completed_at


class TestBackPressure:
    """issue #429 — лимит одновременных нетерминальных job'ов + safety-net."""

    def test_over_limit_raises_then_frees_after_completion(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Изоляция: свежий реестр и низкий лимит вместо дефолтных 20.
        monkeypatch.setattr(runs, "_JOBS", {})
        monkeypatch.setattr(runs, "CONFIG", dataclasses.replace(runs.CONFIG, max_active_runs=2))
        # Блокирующее тело держит job'ы нетерминальными до release.set().
        release = threading.Event()

        def _blocking(job: runs.Job, *args: object, **kwargs: object) -> None:
            with job.lock:
                job.status = "running"
            release.wait(10)
            with job.lock:
                job.status = "done"

        monkeypatch.setattr(runs, "_run_job", _blocking)

        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        j1 = runs.submit_job("tests", sol, {"lang": "ru"})
        j2 = runs.submit_job("tests", sol, {"lang": "ru"})

        # Третий сверх лимита → отказ (источник HTTP-429).
        with pytest.raises(runs.TooManyRunsError) as excinfo:
            runs.submit_job("tests", sol, {"lang": "ru"})
        assert excinfo.value.limit == 2

        # После завершения активных — submit снова проходит.
        release.set()
        assert _poll_until_terminal(j1.id)["status"] == "done"
        assert _poll_until_terminal(j2.id)["status"] == "done"
        j3 = runs.submit_job("tests", sol, {"lang": "ru"})
        assert isinstance(j3, runs.Job)
        _poll_until_terminal(j3.id)

    def test_safety_net_grade_path_raise_becomes_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Исключение в grade_path → job терминальна со status=error, не вечное
        running (safety-net _run_job, issue #429 критерий)."""

        def _boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("boom in grade")

        monkeypatch.setattr(runs, "grade_path", _boom)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("tests", sol, {"lang": "ru"})
        data = _poll_until_terminal(job.id)
        assert data["status"] == "error"
        assert data["message_id"] == "run_internal_error"
        assert "boom in grade" in data["message"]
