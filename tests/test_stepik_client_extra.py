"""Дополнительные mock-тесты для stepik_client.py.

Покрывают непокрытые пути: authorize_via_browser, _cached_api_get (с файловым
кэшем), и все fetch_* хелперы Stepik REST API. Все сетевые вызовы замоканы —
реальных HTTP-запросов и обращений к файловой системе вне tmp_path нет.
"""

from __future__ import annotations

import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest

import stepik_client
from stepik_client import (
    _cached_api_get,
    _get_with_retry,
    authorize_via_browser,
    fetch_course_data,
    fetch_lesson_data,
    fetch_section_data,
    fetch_step_data,
    fetch_submission_data,
    fetch_unit_data,
)


class TestAuthorizeViaBrowser:
    """authorize_via_browser открывает браузер и обменивает код на токены."""

    def test_happy_path(self):
        """Успешный flow: open браузер → код → POST токена → expires_at добавлен."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
        }
        with (
            patch("stepik_client.webbrowser.open") as mock_open,
            patch("stepik_client.wait_for_auth_code", return_value="CODE123") as mock_wait,
            patch("stepik_client.requests.post", return_value=token_resp) as mock_post,
        ):
            result = authorize_via_browser("cid", "csecret", "http://localhost:8080/cb")

        mock_open.assert_called_once()
        mock_wait.assert_called_once_with("localhost", 8080, "/cb")
        mock_post.assert_called_once()
        assert result["access_token"] == "AT"
        assert result["refresh_token"] == "RT"
        assert result["expires_at"] > time.time()

    def test_browser_open_oserror_is_swallowed(self):
        """Если webbrowser.open бросает OSError — flow продолжается."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "AT", "expires_in": 100}
        with (
            patch("stepik_client.webbrowser.open", side_effect=OSError("no display")),
            patch("stepik_client.wait_for_auth_code", return_value="CODE"),
            patch("stepik_client.requests.post", return_value=token_resp),
        ):
            result = authorize_via_browser("cid", "csecret", "https://localhost/cb")
        assert result["access_token"] == "AT"

    def test_default_host_port_path(self):
        """redirect_uri без явных host/port/path → localhost, 80, /."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "AT", "expires_in": 100}
        with (
            patch("stepik_client.webbrowser.open"),
            patch("stepik_client.wait_for_auth_code", return_value="C") as mock_wait,
            patch("stepik_client.requests.post", return_value=token_resp),
        ):
            authorize_via_browser("cid", "cs", "https://example.org")
        mock_wait.assert_called_once_with("example.org", 80, "/")


class TestGetWithRetryNoAttempts:
    """_get_with_retry с retries=0 не делает попыток и бросает RuntimeError."""

    def test_zero_retries_raises_runtime_error(self):
        """retries=0 → цикл не выполняется → RuntimeError (last_exc is None)."""
        session = MagicMock()
        with pytest.raises(RuntimeError, match="no attempts made"):
            _get_with_retry(session, "http://x", retries=0)
        session.get.assert_not_called()


class TestCachedApiGet:
    """_cached_api_get кэширует JSON-ответы в файл с TTL."""

    def test_cache_miss_then_write(self, tmp_path: pathlib.Path):
        """Кэша нет → выполняется запрос, ответ пишется в кэш-файл."""
        resp = MagicMock()
        resp.json.return_value = {"lessons": [{"id": 1}]}
        session = MagicMock()
        with (
            patch.object(stepik_client, "CACHE_DIR", tmp_path / "cache"),
            patch("stepik_client._get_with_retry", return_value=resp) as mock_get,
        ):
            data = _cached_api_get(session, "http://api/lessons/1")
        assert data == {"lessons": [{"id": 1}]}
        mock_get.assert_called_once()
        cache_files = list((tmp_path / "cache").glob("*.json"))
        assert len(cache_files) == 1

    def test_cache_hit_skips_request(self, tmp_path: pathlib.Path):
        """Свежий кэш-файл → запрос не выполняется, читается из кэша."""
        resp = MagicMock()
        resp.json.return_value = {"v": 1}
        session = MagicMock()
        cache_dir = tmp_path / "cache"
        with (
            patch.object(stepik_client, "CACHE_DIR", cache_dir),
            patch("stepik_client._get_with_retry", return_value=resp) as mock_get,
        ):
            _cached_api_get(session, "http://api/x")  # первый — пишет кэш
            assert mock_get.call_count == 1
            data = _cached_api_get(session, "http://api/x")  # второй — из кэша
            assert mock_get.call_count == 1  # не вырос
        assert data == {"v": 1}

    def test_stale_cache_triggers_request(self, tmp_path: pathlib.Path):
        """Протухший кэш (mtime в прошлом) → выполняется новый запрос."""
        resp = MagicMock()
        resp.json.return_value = {"fresh": True}
        session = MagicMock()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with (
            patch.object(stepik_client, "CACHE_DIR", cache_dir),
            patch.object(stepik_client, "CACHE_TTL_SECONDS", 10),
            patch("stepik_client._get_with_retry", return_value=resp),
        ):
            # создаём первый кэш и состариваем его
            _cached_api_get(session, "http://api/y")
            cache_file = next(cache_dir.glob("*.json"))
            old = time.time() - 9999
            import os

            os.utime(cache_file, (old, old))
            data = _cached_api_get(session, "http://api/y")
        assert data == {"fresh": True}

    def test_corrupt_cache_falls_back_to_request(self, tmp_path: pathlib.Path):
        """Битый JSON в свежем кэше → молча игнорируется, идёт запрос."""
        resp = MagicMock()
        resp.json.return_value = {"ok": 1}
        session = MagicMock()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        with (
            patch.object(stepik_client, "CACHE_DIR", cache_dir),
            patch("stepik_client._get_with_retry", return_value=resp) as mock_get,
        ):
            # первый запрос пишет валидный кэш
            _cached_api_get(session, "http://api/z")
            cache_file = next(cache_dir.glob("*.json"))
            cache_file.write_text("{ broken", encoding="utf-8")
            data = _cached_api_get(session, "http://api/z")
        assert data == {"ok": 1}
        assert mock_get.call_count == 2


class TestFetchStepData:
    """fetch_step_data ищет шаг по позиции с пагинацией."""

    def test_found_on_first_page(self):
        """Шаг найден на первой странице — возвращается его объект."""
        resp = MagicMock()
        resp.json.return_value = {
            "steps": [{"position": 1, "id": 11}, {"position": 2, "id": 22}],
            "meta": {"has_next": False},
        }
        session = MagicMock()
        with patch("stepik_client._get_with_retry", return_value=resp):
            step = fetch_step_data(session, lesson_id=5, step_position=2)
        assert step["id"] == 22

    def test_pagination_second_page(self):
        """Шаг на второй странице — выполняется пагинация."""
        page1 = MagicMock()
        page1.json.return_value = {"steps": [{"position": 1}], "meta": {"has_next": True}}
        page2 = MagicMock()
        page2.json.return_value = {
            "steps": [{"position": 3, "id": 99}],
            "meta": {"has_next": False},
        }
        session = MagicMock()
        with patch("stepik_client._get_with_retry", side_effect=[page1, page2]) as mock_get:
            step = fetch_step_data(session, lesson_id=5, step_position=3)
        assert step["id"] == 99
        assert mock_get.call_count == 2

    def test_not_found_raises(self):
        """Шаг с искомой позицией отсутствует → ValueError."""
        resp = MagicMock()
        resp.json.return_value = {"steps": [{"position": 1}], "meta": {"has_next": False}}
        session = MagicMock()
        with patch("stepik_client._get_with_retry", return_value=resp):
            with pytest.raises(ValueError, match="не найден"):
                fetch_step_data(session, lesson_id=5, step_position=42)


class TestFetchSingletonHelpers:
    """fetch_lesson/unit/section/course — обёртки над _cached_api_get."""

    def test_fetch_lesson_ok(self):
        with patch("stepik_client._cached_api_get", return_value={"lessons": [{"id": 7}]}):
            assert fetch_lesson_data(MagicMock(), 7)["id"] == 7

    def test_fetch_lesson_missing_raises(self):
        with patch("stepik_client._cached_api_get", return_value={"lessons": []}):
            with pytest.raises(ValueError, match="Урок"):
                fetch_lesson_data(MagicMock(), 7)

    def test_fetch_unit_with_id(self):
        """unit_id передаётся в params."""
        with patch("stepik_client._cached_api_get", return_value={"units": [{"id": 3}]}) as m:
            assert fetch_unit_data(MagicMock(), 5, 3)["id"] == 3
        _, kwargs = m.call_args
        assert kwargs["params"] == {"lesson": 5, "id": 3}

    def test_fetch_unit_without_id(self):
        with patch("stepik_client._cached_api_get", return_value={"units": [{"id": 9}]}) as m:
            fetch_unit_data(MagicMock(), 5, None)
        _, kwargs = m.call_args
        assert "id" not in kwargs["params"]

    def test_fetch_unit_missing_raises(self):
        with patch("stepik_client._cached_api_get", return_value={"units": []}):
            with pytest.raises(ValueError, match="Юнит"):
                fetch_unit_data(MagicMock(), 5, None)

    def test_fetch_section_ok_and_missing(self):
        with patch("stepik_client._cached_api_get", return_value={"sections": [{"id": 2}]}):
            assert fetch_section_data(MagicMock(), 2)["id"] == 2
        with patch("stepik_client._cached_api_get", return_value={"sections": []}):
            with pytest.raises(ValueError, match="Секция"):
                fetch_section_data(MagicMock(), 2)

    def test_fetch_course_ok_and_missing(self):
        with patch("stepik_client._cached_api_get", return_value={"courses": [{"id": 4}]}):
            assert fetch_course_data(MagicMock(), 4)["id"] == 4
        with patch("stepik_client._cached_api_get", return_value={"courses": []}):
            with pytest.raises(ValueError, match="Курс"):
                fetch_course_data(MagicMock(), 4)


class TestFetchSubmissionData:
    """fetch_submission_data возвращает последний сабмишн или None."""

    def test_returns_latest(self):
        resp = MagicMock()
        resp.json.return_value = {"submissions": [{"id": 1}, {"id": 2}]}
        with patch("stepik_client._get_with_retry", return_value=resp):
            assert fetch_submission_data(MagicMock(), 100)["id"] == 1

    def test_returns_none_when_empty(self):
        resp = MagicMock()
        resp.json.return_value = {"submissions": []}
        with patch("stepik_client._get_with_retry", return_value=resp):
            assert fetch_submission_data(MagicMock(), 100) is None
