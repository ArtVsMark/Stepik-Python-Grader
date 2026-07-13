"""Tests for the sandbox playground (issue #317).

Три уровня: ``run_playground`` напрямую (исполнение кода со stdin),
``submit_job(kind="playground")`` (async job-модель), и HTTP
``POST /api/v1/runs`` с ``mode="playground"`` + polling — на реальном сервере.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.web import playground, runs

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

    def test_timeout_is_tle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # укоротить общий таймаут, чтобы не ждать реальные 10 с
        short = dataclasses.replace(playground.CONFIG, timeout_seconds=0.3)
        monkeypatch.setattr(playground, "CONFIG", short)
        result = playground.run_playground("while True:\n    pass")
        assert result["status"] == "TLE"

    def test_output_truncated_when_huge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(playground, "_MAX_OUTPUT_CHARS", 50)
        result = playground.run_playground("print('x' * 1000)")
        assert result["truncated"] is True
        assert len(result["stdout"]) == 50


# ---------------------------------------------------------------------------
# submit_job(kind="playground") — async job-модель
# ---------------------------------------------------------------------------


def _poll_until_terminal(job_id: str, *, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runs.get_job(job_id)
        assert job is not None
        data = job.to_status_dict()
        if data["status"] in ("done", "error", "cancelled"):
            return data
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not reach terminal state within {timeout}s")


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
        time.sleep(0.2)  # дать воркеру стартовать subprocess
        assert runs.cancel_job(job.id) is True
        data = _poll_until_terminal(job.id)
        assert data["status"] == "cancelled"


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
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost only)
        return resp.status, json.loads(resp.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read())


class TestPlaygroundHttp:
    def test_playground_run_via_http(self, server: str) -> None:
        status, created = _post(
            server + "/api/v1/runs",
            {"mode": "playground", "code": "print(input())", "stdin": "web"},
        )
        assert status == 202
        run_id = created["run_id"]

        deadline = time.monotonic() + 15
        data = None
        while time.monotonic() < deadline:
            data = _get(server + "/api/v1/runs/" + run_id)
            if data["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.05)
        assert data is not None and data["status"] == "done"
        assert data["result"]["status"] == "OK"
        assert data["result"]["stdout"] == "web\n"

    def test_playground_without_code_is_400(self, server: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(server + "/api/v1/runs", {"mode": "playground", "code": "   "})
        assert exc.value.code == 400
