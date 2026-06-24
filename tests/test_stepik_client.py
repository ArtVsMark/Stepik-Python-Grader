"""Unit tests for stepik_client.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_client import (
    CACHE_DIR,
    CACHE_TTL_SECONDS,
    _get_with_retry,
    token_is_valid,
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
    def test_success_first_try(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        result = _get_with_retry(mock_session, "http://example.com")
        assert result is mock_resp
        assert mock_session.get.call_count == 1

    def test_retries_on_network_error(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.side_effect = [
            requests.ConnectionError("timeout"),
            requests.ConnectionError("timeout"),
            mock_resp,
        ]
        with patch("stepik_client.time.sleep"):
            result = _get_with_retry(
                mock_session, "http://example.com", retries=3, backoff=0.01
            )
        assert result is mock_resp
        assert mock_session.get.call_count == 3

    def test_raises_after_max_retries(self):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError("always fails")
        with patch("stepik_client.time.sleep"):
            with pytest.raises(requests.ConnectionError):
                _get_with_retry(
                    mock_session, "http://example.com", retries=2, backoff=0.01
                )
        assert mock_session.get.call_count == 2


def test_cache_constants() -> None:
    assert str(CACHE_DIR) == ".stepik_cache"
    assert CACHE_TTL_SECONDS == 3600
