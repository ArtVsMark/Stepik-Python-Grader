"""OAuth safety-net tests written BEFORE the oauth_flow.py refactoring (Issue #1).

These tests pin the CURRENT behavior of the OAuth functions at their current
locations:

  * ``load_secrets``        — diagnostic_stepik.py (tuple-returning variant; this
                              is the shape the future ``oauth_flow.load_secrets``
                              will keep, per Issue #1).
  * ``token_is_valid``      — stepik_client.py
  * ``wait_for_auth_code``  — stepik_client.py
  * ``_make_oauth_handler`` — stepik_client.py
  * ``refresh_access_token``— stepik_client.py (+ create_user_session refresh path)

REFACTORING INVARIANT (applies to every test below): after the OAuth logic moves
into oauth_flow.py, the imports here will change but the asserted behavior must
not. Update the import lines, keep the assertions.
"""

from __future__ import annotations

import json
import threading
import time
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core import oauth_flow, stepik_client
from stepik_grader.core.oauth_flow import (
    authorize_and_get_token,
    load_secrets,
    load_secrets_dict,
    token_is_valid,
    try_create_session_without_browser,
    wait_for_auth_code,
)
from stepik_grader.core.stepik_client import (
    _make_oauth_handler,
    create_user_session,
    refresh_access_token,
)

# ---------------------------------------------------------------------------
# load_secrets
# ---------------------------------------------------------------------------


class TestLoadSecrets:
    def test_load_secrets_returns_valid_credentials(self, tmp_path):
        """load_secrets reads client_id/client_secret/redirect_uri from secrets.json.

        REFACTORING INVARIANT: after moving to oauth_flow.py, same behavior —
        returns a ``(client_id, client_secret, redirect_uri)`` tuple.
        """
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(
            json.dumps(
                {
                    "client_id": "test_id",
                    "client_secret": "test_secret",
                    "redirect_uri": "http://localhost:8080/callback",
                    "access_token": "",
                    "refresh_token": "",
                    "expires_at": 0,
                }
            ),
            encoding="utf-8",
        )
        result = load_secrets(secrets_file)
        assert result == ("test_id", "test_secret", "http://localhost:8080/callback")

    def test_load_secrets_strips_whitespace(self, tmp_path):
        """REFACTORING INVARIANT: surrounding whitespace is stripped from fields."""
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(
            json.dumps(
                {
                    "client_id": "  id  ",
                    "client_secret": "  secret  ",
                    "redirect_uri": "  http://localhost/cb  ",
                }
            ),
            encoding="utf-8",
        )
        assert load_secrets(secrets_file) == ("id", "secret", "http://localhost/cb")

    def test_load_secrets_missing_field_raises(self, tmp_path):
        """Missing required field raises ValueError.

        REFACTORING INVARIANT: incomplete secrets must still raise ValueError.
        """
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(
            json.dumps({"client_id": "id", "client_secret": "secret"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_secrets(secrets_file)

    def test_load_secrets_empty_field_raises(self, tmp_path):
        """REFACTORING INVARIANT: blank required field raises ValueError."""
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(
            json.dumps({"client_id": "id", "client_secret": "", "redirect_uri": "http://x/cb"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_secrets(secrets_file)

    def test_load_secrets_missing_file_raises(self, tmp_path):
        """Non-existent file raises FileNotFoundError.

        REFACTORING INVARIANT: missing file must still raise FileNotFoundError.
        """
        with pytest.raises(FileNotFoundError):
            load_secrets(tmp_path / "does_not_exist.json")

    def test_load_secrets_directory_raises(self, tmp_path):
        """REFACTORING INVARIANT: a directory path raises IsADirectoryError."""
        with pytest.raises(IsADirectoryError):
            load_secrets(tmp_path)

    def test_load_secrets_non_object_raises(self, tmp_path):
        """REFACTORING INVARIANT: a JSON non-object (e.g. list) raises ValueError."""
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        with pytest.raises(ValueError):
            load_secrets(secrets_file)


# ---------------------------------------------------------------------------
# token_is_valid
# ---------------------------------------------------------------------------


class TestTokenIsValid:
    def test_token_is_valid_future_expiry(self):
        """Token with future expires_at is valid.

        REFACTORING INVARIANT: unchanged after move to oauth_flow.py.
        """
        secrets = {"access_token": "abc", "expires_at": time.time() + 3600}
        assert token_is_valid(secrets) is True

    def test_token_is_valid_past_expiry(self):
        """Token with past expires_at is invalid. REFACTORING INVARIANT."""
        secrets = {"access_token": "abc", "expires_at": time.time() - 1}
        assert token_is_valid(secrets) is False

    def test_token_is_valid_empty_token(self):
        """Empty access_token string is invalid. REFACTORING INVARIANT."""
        secrets = {"access_token": "", "expires_at": time.time() + 3600}
        assert token_is_valid(secrets) is False

    def test_token_is_valid_zero_expiry(self):
        """expires_at=0 means not yet obtained -> invalid. REFACTORING INVARIANT."""
        secrets = {"access_token": "abc", "expires_at": 0}
        assert token_is_valid(secrets) is False

    def test_token_is_valid_within_60s_buffer(self):
        """Token expiring inside the 60s safety buffer is invalid. REFACTORING INVARIANT."""
        secrets = {"access_token": "abc", "expires_at": time.time() + 30}
        assert token_is_valid(secrets) is False


# ---------------------------------------------------------------------------
# wait_for_auth_code (drives a real OAuthHandler via a fake server)
# ---------------------------------------------------------------------------


def _fake_server_factory(simulated_path):
    """Fake-класс сервера, прогоняющий настоящий handler на simulated_path.

    Подменяет ``_OAuthHTTPServer`` (issue #943): колбэк-сервер перестал быть
    голым ``HTTPServer`` — он переопределяет ``server_bind``, чтобы не ходить в
    обратный DNS при старте.
    """

    class _FakeServer:
        def __init__(self, server_address, handler_class):
            self.handler_class = handler_class
            self.timeout = None

        def handle_request(self):
            handler = self.handler_class.__new__(self.handler_class)
            handler.path = simulated_path
            handler.wfile = BytesIO()
            handler.send_response = lambda *a, **k: None
            handler.send_header = lambda *a, **k: None
            handler.end_headers = lambda *a, **k: None
            handler.do_GET()

        def server_close(self):
            pass

    return _FakeServer


class TestWaitForAuthCode:
    def test_wait_for_auth_code_extracts_code(self, monkeypatch):
        """Server extracts ?code=XYZ from the redirect URL.

        REFACTORING INVARIANT: same extraction logic in oauth_flow.wait_for_auth_code.
        """
        fake_server = _fake_server_factory("/callback?code=test_code&state=xyz")
        monkeypatch.setattr(stepik_client, "_OAuthHTTPServer", fake_server)
        code = wait_for_auth_code("localhost", 8080, "/callback", "xyz", timeout=1)
        assert code == "test_code"

    def test_wait_for_auth_code_raises_on_error_param(self, monkeypatch):
        """An ?error= callback (with matching state) raises RuntimeError. REFACTORING INVARIANT."""
        fake_server = _fake_server_factory("/callback?error=access_denied&state=xyz")
        monkeypatch.setattr(stepik_client, "_OAuthHTTPServer", fake_server)
        with pytest.raises(RuntimeError):
            wait_for_auth_code("localhost", 8080, "/callback", "xyz", timeout=1)

    def test_wait_for_auth_code_timeout_without_code(self, monkeypatch):
        """No code and no error within timeout raises TimeoutError. REFACTORING INVARIANT."""
        fake_server = _fake_server_factory("/callback?state=xyz")  # no code, no error
        monkeypatch.setattr(stepik_client, "_OAuthHTTPServer", fake_server)
        with pytest.raises(TimeoutError):
            wait_for_auth_code("localhost", 8080, "/callback", "xyz", timeout=1)

    def test_wait_for_auth_code_raises_on_state_mismatch(self, monkeypatch):
        """Callback with a wrong ``state`` raises RuntimeError, even with a valid code.

        Regression test for issue #241 (F-02, Login-CSRF): a callback whose
        ``state`` doesn't match the one sent in the authorize URL must never
        yield a usable code.
        """
        fake_server = _fake_server_factory("/callback?code=attacker_code&state=wrong")
        monkeypatch.setattr(stepik_client, "_OAuthHTTPServer", fake_server)
        with pytest.raises(RuntimeError, match="state"):
            wait_for_auth_code("localhost", 8080, "/callback", "expected", timeout=1)

    def test_wait_for_auth_code_raises_on_missing_state(self, monkeypatch):
        """Callback with no ``state`` param at all is rejected the same as a mismatch."""
        fake_server = _fake_server_factory("/callback?code=attacker_code")
        monkeypatch.setattr(stepik_client, "_OAuthHTTPServer", fake_server)
        with pytest.raises(RuntimeError, match="state"):
            wait_for_auth_code("localhost", 8080, "/callback", "expected", timeout=1)


# ---------------------------------------------------------------------------
# _make_oauth_handler / OAuthHandler
# ---------------------------------------------------------------------------


def _build_handler(handler_class, path):
    """Instantiate handler without running the socket machinery."""
    handler = handler_class.__new__(handler_class)
    handler.path = path
    handler.wfile = BytesIO()
    handler._responses = []
    handler.send_response = lambda code, *a, **k: handler._responses.append(code)
    handler.send_header = lambda *a, **k: None
    handler.end_headers = lambda *a, **k: None
    return handler


class TestOAuthHandler:
    def test_oauth_handler_sets_auth_code_on_callback(self):
        """Handler stores the authorization code when the callback path is hit.

        REFACTORING INVARIANT: handler extraction logic unchanged in oauth_flow.py.
        """
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "s")
        handler = _build_handler(handler_class, "/callback?code=abc123&state=s")
        handler.do_GET()
        assert auth_data["code"] == "abc123"
        assert auth_data["error"] is None

    def test_oauth_handler_returns_200_and_html_on_success(self):
        """Handler returns 200 and an HTML response body. REFACTORING INVARIANT."""
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "s")
        handler = _build_handler(handler_class, "/callback?code=abc123&state=s")
        handler.do_GET()
        assert handler._responses == [200]
        assert b"<h1>" in handler.wfile.getvalue()

    def test_oauth_handler_captures_error_param(self):
        """Handler captures the error parameter from the callback. REFACTORING INVARIANT."""
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "s")
        handler = _build_handler(handler_class, "/callback?error=denied&state=s")
        handler.do_GET()
        assert auth_data["error"] == "denied"
        assert auth_data["code"] is None

    def test_oauth_handler_404_on_wrong_path(self):
        """Requests to a non-callback path return 404 and set no code. REFACTORING INVARIANT."""
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "s")
        handler = _build_handler(handler_class, "/favicon.ico?code=ignored")
        handler.do_GET()
        assert handler._responses == [404]
        assert "code" not in auth_data

    def test_oauth_handler_rejects_mismatched_state(self):
        """Callback with a wrong ``state`` is rejected with 400 and no code captured.

        Regression test for issue #241 (F-02, Login-CSRF).
        """
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "expected")
        handler = _build_handler(handler_class, "/callback?code=attacker_code&state=wrong")
        handler.do_GET()
        assert handler._responses == [400]
        assert auth_data["error"] == "state_mismatch"
        assert "code" not in auth_data

    def test_oauth_handler_rejects_missing_state(self):
        """Callback with no ``state`` param at all is rejected the same as a mismatch."""
        auth_data: dict = {}
        handler_class = _make_oauth_handler(auth_data, "/callback", "expected")
        handler = _build_handler(handler_class, "/callback?code=attacker_code")
        handler.do_GET()
        assert handler._responses == [400]
        assert auth_data["error"] == "state_mismatch"
        assert "code" not in auth_data


# ---------------------------------------------------------------------------
# refresh_access_token / create_user_session refresh path
# ---------------------------------------------------------------------------


class TestRefreshAccessToken:
    def test_refresh_access_token_posts_to_token_endpoint(self):
        """refresh_access_token calls /oauth2/token/ and returns the new token dict.

        REFACTORING INVARIANT: same endpoint and return contract in oauth_flow.py.
        """
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 3600,
        }
        with patch(
            "stepik_grader.core.stepik_client.requests.post", return_value=mock_resp
        ) as mock_post:
            result = refresh_access_token("cid", "csecret", "old_refresh")
        assert result["access_token"] == "new_access"
        called_url = mock_post.call_args[0][0]
        assert called_url.endswith("/oauth2/token/")
        sent_data = mock_post.call_args.kwargs["data"]
        assert sent_data["grant_type"] == "refresh_token"
        assert sent_data["refresh_token"] == "old_refresh"

    def test_create_user_session_refresh_updates_secrets_file(self, tmp_path):
        """create_user_session refresh path updates the secrets file with new tokens.

        REFACTORING INVARIANT: refresh-then-persist behavior must survive the move.
        """
        secrets_path = tmp_path / "secrets.json"
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "",
            "refresh_token": "old_refresh",
            "expires_at": 0,
        }
        secrets_path.write_text(json.dumps(secrets), encoding="utf-8")

        new_tokens = {
            "access_token": "fresh_access",
            "refresh_token": "fresh_refresh",
            "expires_in": 3600,
        }
        with patch(
            "stepik_grader.core.stepik_client.refresh_access_token", return_value=dict(new_tokens)
        ):
            session = create_user_session(secrets, secrets_path)

        assert session.headers["Authorization"] == "Bearer fresh_access"
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "fresh_access"
        assert saved["refresh_token"] == "fresh_refresh"
        assert saved["expires_at"] > time.time()

    def test_create_user_session_valid_token_skips_refresh(self, tmp_path):
        """A still-valid token returns a session without any network call.

        REFACTORING INVARIANT: valid-token short-circuit preserved.
        """
        secrets_path = tmp_path / "secrets.json"
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "already_valid",
            "refresh_token": "r",
            "expires_at": time.time() + 3600,
        }
        with (
            patch("stepik_grader.core.stepik_client.refresh_access_token") as mock_refresh,
            patch("stepik_grader.core.stepik_client.authorize_via_browser") as mock_authorize,
        ):
            session = create_user_session(secrets, secrets_path)
        assert session.headers["Authorization"] == "Bearer already_valid"
        mock_refresh.assert_not_called()
        mock_authorize.assert_not_called()

    def test_create_user_session_falls_back_to_browser_on_refresh_failure(self, tmp_path):
        """When refresh fails with HTTPError, full browser OAuth flow runs.

        REFACTORING INVARIANT: refresh-failure fallback preserved.
        """
        secrets_path = tmp_path / "secrets.json"
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "",
            "refresh_token": "stale_refresh",
            "expires_at": 0,
        }
        secrets_path.write_text(json.dumps(secrets), encoding="utf-8")

        browser_tokens = {
            "access_token": "browser_access",
            "refresh_token": "browser_refresh",
            "expires_at": time.time() + 3600,
        }
        with (
            patch(
                "stepik_grader.core.stepik_client.refresh_access_token",
                side_effect=requests.HTTPError("expired"),
            ),
            patch(
                "stepik_grader.core.stepik_client.authorize_via_browser",
                return_value=dict(browser_tokens),
            ) as mock_authorize,
        ):
            session = create_user_session(secrets, secrets_path)

        mock_authorize.assert_called_once()
        assert session.headers["Authorization"] == "Bearer browser_access"
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "browser_access"


# ---------------------------------------------------------------------------
# try_create_session_without_browser — issue #186 (web Downloader, no browser)
# ---------------------------------------------------------------------------


class TestTryCreateSessionWithoutBrowser:
    def test_valid_token_returns_session_without_network(self, tmp_path):
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "already_valid",
            "refresh_token": "r",
            "expires_at": time.time() + 3600,
        }
        with (
            patch("stepik_grader.core.oauth_flow.refresh_access_token") as mock_refresh,
            patch("stepik_grader.core.oauth_flow.authorize_via_browser") as mock_authorize,
        ):
            session = try_create_session_without_browser(secrets, tmp_path / "secrets.json")
        assert session is not None
        assert session.headers["Authorization"] == "Bearer already_valid"
        mock_refresh.assert_not_called()
        mock_authorize.assert_not_called()

    def test_expired_token_with_valid_refresh_returns_session_and_saves(self, tmp_path):
        secrets_path = tmp_path / "secrets.json"
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "",
            "refresh_token": "old_refresh",
            "expires_at": 0,
        }
        secrets_path.write_text(json.dumps(secrets), encoding="utf-8")
        new_tokens = {
            "access_token": "fresh_access",
            "refresh_token": "fresh_refresh",
            "expires_in": 3600,
        }
        with patch(
            "stepik_grader.core.oauth_flow.refresh_access_token", return_value=dict(new_tokens)
        ):
            session = try_create_session_without_browser(secrets, secrets_path)
        assert session is not None
        assert session.headers["Authorization"] == "Bearer fresh_access"
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "fresh_access"

    def test_expired_refresh_token_returns_none_without_browser(self, tmp_path):
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "",
            "refresh_token": "stale_refresh",
            "expires_at": 0,
        }
        with (
            patch(
                "stepik_grader.core.oauth_flow.refresh_access_token",
                side_effect=requests.HTTPError("expired"),
            ),
            patch("stepik_grader.core.oauth_flow.authorize_via_browser") as mock_authorize,
        ):
            session = try_create_session_without_browser(secrets, tmp_path / "secrets.json")
        assert session is None
        mock_authorize.assert_not_called()

    def test_no_token_no_refresh_returns_none_without_browser(self, tmp_path):
        secrets = {
            "client_id": "cid",
            "client_secret": "csecret",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "",
            "refresh_token": "",
            "expires_at": 0,
        }
        with patch("stepik_grader.core.oauth_flow.authorize_via_browser") as mock_authorize:
            session = try_create_session_without_browser(secrets, tmp_path / "secrets.json")
        assert session is None
        mock_authorize.assert_not_called()


# ---------------------------------------------------------------------------
# authorize_and_get_token (full flow facade)
# ---------------------------------------------------------------------------


class TestAuthorizeAndGetToken:
    def test_merges_tokens_into_existing_secrets_file(self, tmp_path, monkeypatch):
        """Tokens from the browser flow are merged into existing secrets and saved."""
        secrets_path = tmp_path / "secrets.json"
        secrets_path.write_text(
            json.dumps(
                {
                    "client_id": "cid",
                    "client_secret": "csecret",
                    "redirect_uri": "http://localhost:8080/callback",
                }
            ),
            encoding="utf-8",
        )
        token_data = {
            "access_token": "fresh",
            "refresh_token": "fresh_r",
            "expires_at": 9999999999,
        }
        monkeypatch.setattr(oauth_flow, "authorize_via_browser", lambda *a, **k: dict(token_data))
        result = authorize_and_get_token(
            "cid", "csecret", "http://localhost:8080/callback", secrets_path
        )
        assert result["access_token"] == "fresh"
        assert result["client_id"] == "cid"
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "fresh"
        assert saved["refresh_token"] == "fresh_r"

    def test_creates_secrets_file_when_absent(self, tmp_path, monkeypatch):
        """When secrets_path does not exist, the tokens are written to a new file."""
        secrets_path = tmp_path / "new_secrets.json"
        token_data = {"access_token": "a", "refresh_token": "r", "expires_at": 1}
        monkeypatch.setattr(oauth_flow, "authorize_via_browser", lambda *a, **k: dict(token_data))
        result = authorize_and_get_token("cid", "csecret", "http://x/cb", secrets_path)
        assert result == token_data
        assert secrets_path.exists()
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "a"

    def test_tokens_written_with_0600_permissions(self, tmp_path, monkeypatch):
        """issue #400: токены пишутся через save_secrets (атомарно, 0600), а не
        save_json_file (0644, world/group-readable) — иначе обходится фикс #243."""
        import os
        import stat

        secrets_path = tmp_path / "secrets.json"
        monkeypatch.setattr(
            oauth_flow,
            "authorize_via_browser",
            lambda *a, **k: {"access_token": "tok", "refresh_token": "ref"},
        )
        authorize_and_get_token("cid", "csecret", "http://x/cb", secrets_path)

        assert secrets_path.is_file()
        if os.name == "posix":  # семантика 0600 — POSIX; на Windows chmod иной
            mode = stat.S_IMODE(secrets_path.stat().st_mode)
            assert mode == 0o600, oct(mode)


# ---------------------------------------------------------------------------
# Гонка обновления токена — issue #816 (NETA-02)
# ---------------------------------------------------------------------------


class TestRefreshRace:
    """Параллельный refresh не обменивает один и тот же refresh_token дважды.

    Stepik ротирует refresh_token: после обмена старый отозван. Без взаимного
    исключения два job'а web-сервера (ThreadingHTTPServer) читали один файл и
    шли на обмен одновременно — замерено прогоном: сервер выдавал ДВЕ пары
    токенов, в файле оставалась одна, а второй обмен шёл уже по отозванному.
    В реальности это invalid_grant и потеря доступа до браузерного OAuth из CLI.
    """

    _BASE = {
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "http://localhost:8080/callback",
        "access_token": "expired",
        "refresh_token": "rt-original",
        "expires_at": 0,
    }

    def test_parallel_refresh_exchanges_token_once(self, tmp_path):
        secrets_path = tmp_path / "secrets.json"
        secrets_path.write_text(json.dumps(self._BASE), encoding="utf-8")

        presented: list[str] = []
        counter = threading.Lock()
        issued = [0]

        def _rotating(client_id, client_secret, refresh_token):
            presented.append(refresh_token)
            with counter:
                issued[0] += 1
                n = issued[0]
            time.sleep(0.05)  # окно, в которое влезал второй поток
            return {"access_token": f"at-{n}", "refresh_token": f"rt-{n}", "expires_in": 3600}

        def _worker() -> None:
            secrets = load_secrets_dict(secrets_path)
            try_create_session_without_browser(secrets, secrets_path)

        with patch("stepik_grader.core.oauth_flow.refresh_access_token", side_effect=_rotating):
            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)

        assert issued[0] == 1, f"обменов было {issued[0]} — refresh_token обменяли повторно"
        assert presented == ["rt-original"]
        saved = json.loads(secrets_path.read_text(encoding="utf-8"))
        assert saved["refresh_token"] == "rt-1"  # в файле именно выданная пара

    def test_second_caller_reuses_fresh_token_from_disk(self, tmp_path):
        """Опоздавший поток берёт токен, записанный победителем, а не обменивает свой.

        Он входит в критическую секцию уже со СТАРЫМ словарём в руках — если
        не перечитать файл, обмен уйдёт по отозванному токену.
        """
        secrets_path = tmp_path / "secrets.json"
        secrets_path.write_text(json.dumps(self._BASE), encoding="utf-8")
        stale = dict(self._BASE)  # снимок «до» обновления, как у опоздавшего

        # Победитель уже обновил файл.
        fresh = dict(self._BASE)
        fresh.update(
            {
                "access_token": "winner-token",
                "refresh_token": "rt-1",
                "expires_at": time.time() + 3600,
            }
        )
        secrets_path.write_text(json.dumps(fresh), encoding="utf-8")

        with patch("stepik_grader.core.oauth_flow.refresh_access_token") as mock_refresh:
            session = try_create_session_without_browser(stale, secrets_path)

        assert session is not None
        assert session.headers["Authorization"] == "Bearer winner-token"
        mock_refresh.assert_not_called()  # обмена не было вовсе
