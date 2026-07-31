"""Tests for the sandbox playground (issue #317).

Три уровня: ``run_playground`` напрямую (исполнение кода со stdin),
``submit_job(kind="playground")`` (async job-модель), и HTTP
``POST /api/v1/runs`` с ``mode="playground"`` + polling — на реальном сервере.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import urllib.error
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.web import playground, runs
from tests._wait import wait_until

# ---------------------------------------------------------------------------
# run_playground — прямые вызовы
# ---------------------------------------------------------------------------


class TestRunPlayground:
    def test_stdout_and_stdin_echo(self) -> None:
        result = playground.run_playground("print(input().upper())", "hello")
        assert result["status"] == "OK"
        assert result["stdout"] == "HELLO\n"
        assert result["exit_code"] == 0
        assert result["truncated"] is False

    def test_reads_multiline_stdin(self) -> None:
        code = "import sys\nprint(sum(int(x) for x in sys.stdin))"
        result = playground.run_playground(code, "1\n2\n3\n")
        assert result["status"] == "OK"
        assert result["stdout"].strip() == "6"

    def test_runtime_error_is_re_with_stderr(self) -> None:
        result = playground.run_playground("1 / 0")
        assert result["status"] == "RE"
        assert result["exit_code"] != 0
        assert "ZeroDivisionError" in result["stderr"]

    def test_empty_stdout_ok(self) -> None:
        result = playground.run_playground("x = 1")
        assert result["status"] == "OK"
        assert result["stdout"] == ""

    def test_launch_error_is_re(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # issue #396: playground исполняет через активный grader_core._RUNNER
        # (не жёсткий LocalRunner) — подменяем именно его.
        from stepik_grader.core import grader_core
        from stepik_grader.core.runner import RunOutcome

        class _FakeRunner:
            def run(self, spec: object) -> RunOutcome:
                return RunOutcome(launch_error="nope")

        monkeypatch.setattr(grader_core, "_RUNNER", _FakeRunner())
        result = playground.run_playground("x = 1")
        assert result["status"] == "RE"
        assert result["stderr"] == "nope"

    def test_cancelled_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stepik_grader.core import grader_core
        from stepik_grader.core.runner import RunOutcome

        class _FakeRunner:
            def run(self, spec: object) -> RunOutcome:
                return RunOutcome(cancelled=True)

        monkeypatch.setattr(grader_core, "_RUNNER", _FakeRunner())
        assert playground.run_playground("x = 1")["status"] == "CANCELLED"

    def test_timeout_is_tle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # укоротить общий таймаут, чтобы не ждать реальные 10 с
        short = dataclasses.replace(playground.CONFIG, timeout_seconds=0.3)
        monkeypatch.setattr(playground, "CONFIG", short)
        result = playground.run_playground("while True:\n    pass")
        assert result["status"] == "TLE"

    def test_tle_keeps_partial_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """issue #798: то, что программа успела напечатать, показывается и при TLE.

        Runner специально возвращает частичный вывод (issue #421), а
        «Песочница» его выбрасывала: зациклившийся код давал пустой экран
        вместо строк, по которым видно, где он застрял.
        """
        short = dataclasses.replace(playground.CONFIG, timeout_seconds=0.6)
        monkeypatch.setattr(playground, "CONFIG", short)

        result = playground.run_playground("print('BEGIN', flush=True)\nwhile True:\n    pass\n")

        assert result["status"] == "TLE"
        assert "BEGIN" in result["stdout"], "частичный вывод потерян"

    def test_cancelled_keeps_partial_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """То же для отмены: пользователь видит, что успело выполниться."""
        from stepik_grader.core import grader_core
        from stepik_grader.core.runner import RunOutcome

        class _FakeRunner:
            def run(self, spec: object) -> RunOutcome:
                return RunOutcome(cancelled=True, stdout=b"partial\n", stderr=b"warn\n")

        monkeypatch.setattr(grader_core, "_RUNNER", _FakeRunner())

        result = playground.run_playground("x = 1")

        assert result["status"] == "CANCELLED"
        assert result["stdout"] == "partial\n"
        assert result["stderr"] == "warn\n"

    def test_output_truncated_when_huge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(playground, "_MAX_OUTPUT_CHARS", 50)
        result = playground.run_playground("print('x' * 1000)")
        assert result["truncated"] is True
        assert len(result["stdout"]) == 50


# ---------------------------------------------------------------------------
# submit_job(kind="playground") — async job-модель
# ---------------------------------------------------------------------------


def _poll_until_terminal(job_id: str, *, timeout: float = 15.0) -> dict:
    def _terminal() -> dict | None:
        job = runs.get_job(job_id)
        assert job is not None
        data = job.to_status_dict()
        return data if data["status"] in ("done", "error", "cancelled") else None

    data = wait_until(_terminal, timeout=timeout)
    if data is None:
        raise TimeoutError(f"job {job_id} did not reach terminal state within {timeout}s")
    return data


class TestPlaygroundJob:
    def test_playground_job_reaches_done_with_result(self) -> None:
        job = runs.submit_job("playground", None, {"lang": "ru"}, code="print(input())", stdin="ok")
        data = _poll_until_terminal(job.id)
        assert data["status"] == "done"
        assert data["result"]["status"] == "OK"
        assert data["result"]["stdout"] == "ok\n"

    def test_playground_cancel_marks_cancelled(self) -> None:
        job = runs.submit_job(
            "playground", None, {"lang": "ru"}, code="while True:\n    pass", stdin=""
        )
        # ждём, пока воркер реально стартует subprocess (status='running')
        assert wait_until(lambda: job.status == "running"), "worker did not start the job"
        assert runs.cancel_job(job.id) is True
        data = _poll_until_terminal(job.id)
        assert data["status"] == "cancelled"

    def test_playground_job_internal_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **k: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(runs, "run_playground", boom)
        job = runs.submit_job("playground", None, {"lang": "ru"}, code="x=1", stdin="")
        assert _poll_until_terminal(job.id)["status"] == "error"

    def test_trace_job_internal_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*a: object, **k: object) -> object:
            raise RuntimeError("boom")

        monkeypatch.setattr(runs, "trace_code", boom)
        job = runs.submit_job("trace", None, {"lang": "ru"}, code="x=1", stdin="")
        assert _poll_until_terminal(job.id)["status"] == "error"

    def test_trace_job_returns_steps(self) -> None:
        # issue #318: kind="trace" через ту же очередь → JSON-трейс в result.
        job = runs.submit_job("trace", None, {"lang": "ru"}, code="a = [1]\nb = a\n", stdin="")
        data = _poll_until_terminal(job.id)
        assert data["status"] == "done"
        assert data["result"]["error"] is None
        assert len(data["result"]["steps"]) >= 2
        # aliasing доезжает через web-слой: a и b — один ref
        last = data["result"]["steps"][-1]
        gl = last["stack"][0]["locals"]
        assert gl["a"] == gl["b"]


# ---------------------------------------------------------------------------
# HTTP — POST /api/v1/runs mode=playground + polling
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


class TestPlaygroundHttp:
    def test_playground_run_via_http(self, server: str) -> None:
        status, created = _post(
            server + "/api/v1/runs",
            {"mode": "playground", "code": "print(input())", "stdin": "web"},
        )
        assert status == 202
        run_id = created["run_id"]

        def _terminal() -> dict | None:
            data = _get(server + "/api/v1/runs/" + run_id)
            return data if data["status"] in ("done", "error", "cancelled") else None

        data = wait_until(_terminal, timeout=15.0)
        assert data is not None and data["status"] == "done"
        assert data["result"]["status"] == "OK"
        assert data["result"]["stdout"] == "web\n"

    def test_playground_without_code_is_400(self, server: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(server + "/api/v1/runs", {"mode": "playground", "code": "   "})
        assert exc.value.code == 400
