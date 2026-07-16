"""Unit tests for stepik_client.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core.stepik_client import (
    CACHE_DIR,
    CACHE_TTL_SECONDS,
    EXTERNAL_DOWNLOAD_ALLOWED_HOSTS,
    ExternalUrlRejected,
    _get_with_retry,
    external_download_get,
    is_stepik_url,
    token_is_valid,
    validate_external_url,
)


class TestTokenIsValid:
    def test_valid_token(self):
        secrets = {"access_token": "abc", "expires_at": time.time() + 3600}
        assert token_is_valid(secrets) is True

    def test_expired_token(self):
        secrets = {"access_token": "abc", "expires_at": time.time() - 1}
        assert token_is_valid(secrets) is False

    def test_empty_token(self):
        secrets = {"access_token": "", "expires_at": time.time() + 3600}
        assert token_is_valid(secrets) is False

    def test_missing_token(self):
        secrets = {"expires_at": time.time() + 3600}
        assert token_is_valid(secrets) is False

    def test_expires_soon(self):
        # Токен истекает через 30 секунд — должен считаться невалидным (буфер 60с)
        secrets = {"access_token": "abc", "expires_at": time.time() + 30}
        assert token_is_valid(secrets) is False


class TestGetWithRetry:
    """issue #404: прикладная retry-петля снята — повтор transient-сбоев (сеть,
    429, 5xx) живёт в транспортном ``urllib3.Retry`` (см. ``make_session`` и
    tests/test_stepik_client_retry.py). Здесь проверяется остаточный контракт
    хелпера: один GET на уровне приложения, ``raise_for_status``, проброс ошибок.
    """

    def test_success_first_try(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        result = _get_with_retry(mock_session, "http://example.com")
        assert result is mock_resp
        assert mock_session.get.call_count == 1

    def test_single_attempt_no_application_retry(self):
        """Сетевая ошибка пробрасывается сразу: прикладного повтора нет — ровно
        один вызов ``session.get`` (повтор был бы на транспортном уровне)."""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError("boom")
        with pytest.raises(requests.ConnectionError):
            _get_with_retry(mock_session, "http://example.com")
        assert mock_session.get.call_count == 1

    def test_raise_for_status_propagates(self):
        """Не-retryable 4xx (404/403) поднимается через ``raise_for_status`` —
        транспортный Retry их не трогает, поэтому хелпер обязан их проявить."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404")
        mock_session.get.return_value = mock_resp
        with pytest.raises(requests.HTTPError):
            _get_with_retry(mock_session, "http://example.com")
        assert mock_session.get.call_count == 1


def test_cache_constants() -> None:
    assert str(CACHE_DIR) == ".stepik_cache"
    assert CACHE_TTL_SECONDS == 3600


# ── issue #240 (F-01): внешние загрузки без Bearer-токена ──────────────────


class TestIsStepikUrl:
    def test_stepik_org(self):
        assert is_stepik_url("https://stepik.org/media/attachments/x.zip") is True

    def test_stepik_subdomain(self):
        assert is_stepik_url("https://cdn.stepik.org/x.zip") is True

    def test_other_host(self):
        assert is_stepik_url("https://github.com/o/r") is False

    def test_lookalike_host_not_matched_as_subdomain(self):
        assert is_stepik_url("https://stepik.org.evil.example/x.zip") is False


class TestValidateExternalUrl:
    @pytest.mark.parametrize("host", sorted(EXTERNAL_DOWNLOAD_ALLOWED_HOSTS))
    def test_allowlisted_hosts_pass(self, host: str) -> None:
        validate_external_url(f"https://{host}/some/path")  # не должно поднять исключение

    def test_non_allowlisted_host_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("https://example.com/tests.zip")

    def test_subdomain_trick_rejected(self):
        """github.com должен матчиться точно, а не как suffix произвольного хоста."""
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("https://raw.githubusercontent.com.evil.example/x")

    def test_localhost_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("http://localhost/x")

    def test_loopback_ipv4_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("http://127.0.0.1/x")

    def test_loopback_ipv6_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("http://[::1]/x")

    def test_private_ip_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("http://10.0.0.5/x")

    def test_invalid_scheme_rejected(self):
        with pytest.raises(ExternalUrlRejected):
            validate_external_url("file:///etc/passwd")


class TestExternalDownloadGet:
    def test_rejects_invalid_url_before_any_request(self):
        with pytest.raises(ExternalUrlRejected):
            external_download_get("https://example.com/x")

    def test_sends_no_authorization_header(self):
        """Regression test issue #240: внешний запрос не наследует
        Authorization текущей авторизованной Stepik-сессии."""

        class _RecordingSession:
            def __init__(self) -> None:
                self.headers: dict[str, str] = {}
                self.captured_headers: dict[str, str] | None = None

            def get(self, url: str, timeout: int = 30) -> MagicMock:
                self.captured_headers = dict(self.headers)
                return MagicMock()

        fake_session = _RecordingSession()
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=fake_session):
            external_download_get("https://github.com/o/r/tree/main/d")

        assert fake_session.captured_headers is not None
        assert "Authorization" not in fake_session.captured_headers

    def test_has_no_session_parameter(self) -> None:
        """У функции нет параметра session — новая сессия создаётся всегда сама."""
        import inspect

        assert "session" not in inspect.signature(external_download_get).parameters
