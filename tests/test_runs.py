"""Tests for web/runs.py — async job-модель (issue #262).

Прямые вызовы submit_job/get_job/cancel_job без HTTP-слоя (тот покрыт
tests/test_web.py::TestRunsApi) — быстрее и изолированнее для отладки
job-lifecycle логики.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from stepik_grader.web import runs


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runs.get_job(job_id)
        assert job is not None
        data = job.to_status_dict()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout}s")


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

        seen_total_before_done = False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            data = job.to_status_dict()
            if data["progress"]["total"] > 0:
                seen_total_before_done = True
            if data["status"] in ("done", "error"):
                break
            time.sleep(0.02)

        assert seen_total_before_done


class TestSubmitJobMicrobench:
    def test_reaches_done_with_result(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("microbench", sol, {"number": 100, "lang": "ru"})

        data = _poll_until_terminal(job.id)

        assert data["status"] == "done"
        assert data["result"]["mode"] == "microbench"
        assert data["progress"]["total"] == 1  # one tick per solution


class TestCancelJob:
    def test_cancel_running_job_marks_error_with_cancelled_message(
        self, tmp_path: pathlib.Path
    ) -> None:
        sol = _make_task(tmp_path, "import time\ntime.sleep(30)\nprint(input())\n")
        job = runs.submit_job("bench", sol, {"repeats": 20, "lang": "ru"})

        # give the worker a moment to actually start the subprocess
        time.sleep(0.3)
        assert runs.cancel_job(job.id) is True

        data = _poll_until_terminal(job.id)
        assert data["status"] == "error"
        assert data["message_id"] == "run_cancelled"

    def test_cancel_unknown_job_returns_false(self) -> None:
        assert runs.cancel_job("no-such-id") is False

    def test_cancel_already_done_job_returns_false(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 1, "lang": "ru"})
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
        assert data_b["result"]["rows"][0]["verdict"] == "ERR"


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

        # No stray .py files besides the original solution should remain.
        py_files = {p.name for p in tmp_path.glob("*.py")}
        assert py_files == {"task.py"}


class TestTtlSweep:
    def test_expired_job_is_swept_on_next_access(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        job = runs.submit_job("bench", sol, {"repeats": 1, "lang": "ru"})
        _poll_until_terminal(job.id)

        # Simulate TTL expiry without sleeping 15 real minutes.
        job.created_at -= runs._JOB_TTL_SECONDS + 1

        assert runs.get_job(job.id) is None
