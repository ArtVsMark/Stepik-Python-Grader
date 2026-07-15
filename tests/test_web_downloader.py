"""Tests for web/downloader_adapter.py — раздел «Загрузчик задач» (issue #186).

Мокирование по образцу tests/test_downloader.py/test_downloader_extra.py:
MagicMock() вместо requests.Session, patch(...) для изоляции сети/OAuth.
"""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader import web
from stepik_grader.web import downloader_adapter

_VALID_SECRETS = {
    "client_id": "cid",
    "client_secret": "csecret",
    "redirect_uri": "http://localhost:8080/callback",
}


def _write_secrets(path: pathlib.Path, **overrides: object) -> None:
    data = dict(_VALID_SECRETS)
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_task_dir_with_tests(base: pathlib.Path) -> pathlib.Path:
    """Реалистичный результат process_step_url — legacy-формат тестов."""
    task_dir = base / "course" / "section" / "lesson" / "04-step"
    task_dir.mkdir(parents=True)
    (task_dir / "task4_1.py").write_text("print(1)\n", encoding="utf-8")
    (task_dir / "meta.json").write_text("{}", encoding="utf-8")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "1").write_text("4", encoding="utf-8")
    (tests_dir / "1.clue").write_text("5", encoding="utf-8")
    return task_dir


class TestDownloadTaskSuccess:
    def test_successful_download_with_tests(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = _make_task_dir_with_tests(tmp_path)
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 1, "html_table"),
            ),
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is True
        assert data["tests"] == {"count": 1, "source": "html_table", "format": "legacy"}
        assert "task4_1.py" in data["files"]
        assert "meta.json" in data["files"]
        assert data["message"] == ""

    def test_no_tests_found_is_ok_with_warning(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = tmp_path / "course" / "section" / "lesson" / "04-step"
        task_dir.mkdir(parents=True)
        (task_dir / "task4_1.py").write_text("", encoding="utf-8")
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 0, "none"),
            ),
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is True
        assert data["tests"]["count"] == 0
        assert data["tests"]["source"] == "none"
        assert "⚠️" in data["message"]

    def test_python_generation_format_detected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        tests_dir = task_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "input.txt").write_text("# TEST_1:\n4\n", encoding="utf-8")
        (tests_dir / "output.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 1, "zip"),
            ),
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["tests"]["format"] == "python_generation"
        assert data["tests"]["source"] == "zip"

    def test_root_param_overrides_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        (tmp_path / "stepik_config.json").write_text(
            json.dumps({"root_dir": "ConfiguredRoot", "secrets_path": "secrets.json"}),
            encoding="utf-8",
        )
        task_dir = tmp_path / "OverrideRoot" / "course" / "section" / "lesson" / "04-step"
        task_dir.mkdir(parents=True)
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ) as _mock_session,
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 0, "none"),
            ) as mock_process,
        ):
            downloader_adapter.download_task(
                "https://stepik.org/lesson/1/step/4", root="OverrideRoot"
            )
        used_root_dir = mock_process.call_args[0][2]
        assert used_root_dir == tmp_path / "OverrideRoot"

    def test_missing_stepik_config_uses_defaults(self, tmp_path, monkeypatch):
        """Нет stepik_config.json вообще -> тихие дефолты, download всё равно проходит."""
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = tmp_path / downloader_adapter.downloader.DEFAULT_ROOT_DIR / "x"
        task_dir.mkdir(parents=True)
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 0, "none"),
            ) as mock_process,
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is True
        used_root_dir = mock_process.call_args[0][2]
        assert used_root_dir == tmp_path / downloader_adapter.downloader.DEFAULT_ROOT_DIR


class TestDownloadTaskErrors:
    def test_no_secrets_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is False
        assert "installation.md" in data["message"]

    def test_secrets_missing_required_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "secrets.json").write_text(json.dumps({"client_id": "cid"}), encoding="utf-8")
        data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is False
        assert "installation.md" in data["message"]

    def test_oauth_unavailable_without_browser(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        with patch(
            "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
            return_value=None,
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is False
        assert "python -m stepik_grader.downloader" in data["message"]

    def test_bad_url_value_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                side_effect=ValueError("Не удалось распознать URL шага"),
            ),
        ):
            data = downloader_adapter.download_task("not-a-valid-url")
        assert data["ok"] is False
        assert "распознать" in data["message"]

    def test_network_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                side_effect=requests.ConnectionError("down"),
            ),
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is False
        assert "Сетевая ошибка" in data["message"]

    def test_unexpected_exception_does_not_propagate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                side_effect=RuntimeError("boom"),
            ),
        ):
            data = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert data["ok"] is False
        assert "Непредвиденная ошибка" in data["message"]


# ---------------------------------------------------------------------------
# HTTP-хендлер — POST /api/download (реальный сервер на эфемерном порту,
# тот же паттерн, что tests/test_web.py::server)
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: pathlib.Path):
    # issue #401: /api/download's own `root` (where to download TO) is now
    # confined to the workspace too (was excluded under #261) — an out-of-root
    # `root` is rejected 403 instead of letting download_task mkdir anywhere.
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(url: str, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost only)
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class TestApiDownloadHttpEndpoint:
    def test_valid_body_returns_200(self, server, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = _make_task_dir_with_tests(tmp_path)
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 1, "html_table"),
            ),
        ):
            status, body = _post(
                server + "/api/download",
                json.dumps({"url": "https://stepik.org/lesson/1/step/4"}).encode("utf-8"),
            )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_invalid_json_body_is_400(self, server):
        status, _ = _post(server + "/api/download", b"not json")
        assert status == 400

    def test_missing_url_is_400(self, server):
        status, body = _post(server + "/api/download", json.dumps({}).encode("utf-8"))
        assert status == 400
        assert json.loads(body)["ok"] is False

    def test_get_on_download_endpoint_is_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(server + "/api/download", timeout=5)  # noqa: S310
        assert exc.value.code == 404

    def test_root_outside_workspace_is_403(self, server, tmp_path):
        """issue #401: root (куда скачивать) конфайнится в workspace — путь
        наружу отклоняется 403, а не создаёт каталог произвольно через mkdir."""
        outside = str(tmp_path.parent / "evil_download_dir")
        status, body = _post(
            server + "/api/download",
            json.dumps({"url": "https://stepik.org/lesson/1/step/4", "root": outside}).encode(
                "utf-8"
            ),
        )
        assert status == 403
        assert json.loads(body)["kind"] == "error"

    def test_root_inside_workspace_is_allowed(self, server, tmp_path, monkeypatch):
        """root-поддиректория внутри workspace конфайнмент проходит (200)."""
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = _make_task_dir_with_tests(tmp_path)
        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 1, "html_table"),
            ),
        ):
            status, body = _post(
                server + "/api/download",
                json.dumps({"url": "https://stepik.org/lesson/1/step/4", "root": "SubDir"}).encode(
                    "utf-8"
                ),
            )
        assert status == 200
        assert json.loads(body)["ok"] is True
