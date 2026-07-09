"""Tests for web/downloader_adapter.py — раздел «Загрузчик задач» (issue #186).

Мокирование по образцу tests/test_downloader.py/test_downloader_extra.py:
MagicMock() вместо requests.Session, patch(...) для изоляции сети/OAuth.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import requests

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
