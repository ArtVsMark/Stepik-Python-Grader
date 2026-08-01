"""Unit tests for stepik_client.py."""

from __future__ import annotations

import os
import pathlib
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core import stepik_client
from stepik_grader.core.stepik_client import (
    _MAX_EXTERNAL_REDIRECT_HOPS,
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


def _mk_resp(*, is_redirect: bool, location: str | None = None) -> MagicMock:
    """Мок ``requests.Response``: редирект (is_redirect + Location) или финальный."""
    resp = MagicMock()
    resp.is_redirect = is_redirect
    resp.headers = {"Location": location} if location is not None else {}
    return resp


class _SeqSession:
    """Фейковая ``requests.Session``: отдаёт заранее заданную очередь ответов на
    последовательные ``get()`` и запоминает запрошенные URL (issue #564)."""

    def __init__(self, responses: list[MagicMock]) -> None:
        self.headers: dict[str, str] = {}
        self._responses = list(responses)
        self.requested_urls: list[str] = []

    def get(self, url: str, timeout: int = 30, allow_redirects: bool = True) -> MagicMock:
        self.requested_urls.append(url)
        return self._responses.pop(0)


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

            def get(self, url: str, timeout: int = 30, allow_redirects: bool = True) -> MagicMock:
                self.captured_headers = dict(self.headers)
                return MagicMock(is_redirect=False)

        fake_session = _RecordingSession()
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=fake_session):
            external_download_get("https://github.com/o/r/tree/main/d")

        assert fake_session.captured_headers is not None
        assert "Authorization" not in fake_session.captured_headers

    def test_has_no_session_parameter(self) -> None:
        """У функции нет параметра session — новая сессия создаётся всегда сама."""
        import inspect

        assert "session" not in inspect.signature(external_download_get).parameters

    def test_redirect_to_private_ip_is_rejected(self) -> None:
        """issue #564: allowlist-хост, редиректящий на metadata/приватный IP,
        отклоняется — приватный hop не запрашивается (SSRF-обход закрыт)."""
        session = _SeqSession(
            [_mk_resp(is_redirect=True, location="http://169.254.169.254/latest/meta-data/")]
        )
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=session):
            with pytest.raises(ExternalUrlRejected):
                external_download_get("https://raw.githubusercontent.com/o/r/main/f")
        assert session.requested_urls == ["https://raw.githubusercontent.com/o/r/main/f"]

    def test_redirect_within_allowlist_is_followed(self) -> None:
        """Легитимный hop github.com → codeload.github.com проходит ревалидацию."""
        final = _mk_resp(is_redirect=False)
        session = _SeqSession(
            [
                _mk_resp(is_redirect=True, location="https://codeload.github.com/o/r/zip/main"),
                final,
            ]
        )
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=session):
            result = external_download_get("https://github.com/o/r/archive/main.zip")
        assert result is final
        assert session.requested_urls == [
            "https://github.com/o/r/archive/main.zip",
            "https://codeload.github.com/o/r/zip/main",
        ]

    def test_relative_redirect_revalidated_against_current_host(self) -> None:
        """Относительный Location абсолютизируется от текущего (allowlist) хоста."""
        final = _mk_resp(is_redirect=False)
        session = _SeqSession([_mk_resp(is_redirect=True, location="/o/r/zip/main"), final])
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=session):
            result = external_download_get("https://codeload.github.com/start")
        assert result is final
        assert session.requested_urls[-1] == "https://codeload.github.com/o/r/zip/main"

    def test_redirect_loop_hits_hop_limit(self) -> None:
        """Бесконечный редирект (в пределах allowlist) обрывается лимитом hop'ов:
        исходный запрос + _MAX hop'ов = _MAX + 1 фактических get()."""
        loop = [
            _mk_resp(is_redirect=True, location="https://codeload.github.com/loop")
            for _ in range(_MAX_EXTERNAL_REDIRECT_HOPS + 3)
        ]
        session = _SeqSession(loop)
        with patch("stepik_grader.core.stepik_client.requests.Session", return_value=session):
            with pytest.raises(ExternalUrlRejected):
                external_download_get("https://github.com/o/r/archive/main.zip")
        assert len(session.requested_urls) == _MAX_EXTERNAL_REDIRECT_HOPS + 1


class TestApiCachePruning:
    """issue #816 (DEV-11): файловый кэш Stepik API не растёт без предела.

    TTL сам по себе рост не сдерживал: просроченная запись лишь игнорировалась
    при чтении и переписывалась, если повторится ТОТ ЖЕ ключ, — а каждая новая
    задача даёт новый URL и новый файл. Для сравнения, `.grader_cache` имеет
    лимит записей и `prune()` с issue #553.
    """

    def _entry(self, cache_dir: pathlib.Path, name: str, *, age_s: float = 0.0) -> pathlib.Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{name}.json"
        path.write_text("{}", encoding="utf-8")
        if age_s:
            stamp = time.time() - age_s
            os.utime(path, (stamp, stamp))
        return path

    def test_expired_entries_are_removed(self, tmp_path: pathlib.Path) -> None:
        fresh = self._entry(tmp_path, "fresh")
        stale = self._entry(tmp_path, "stale", age_s=stepik_client.CACHE_TTL_SECONDS + 60)

        removed = stepik_client.prune_cache(tmp_path)

        assert removed == 1
        assert fresh.exists()
        assert not stale.exists()

    def test_surplus_entries_are_capped(self, tmp_path: pathlib.Path) -> None:
        """Сверх лимита выбывают самые давние — свежие ответы остаются в кэше."""
        for i in range(stepik_client.CACHE_MAX_ENTRIES + 5):
            # Возраст в пределах TTL, но убывающий: entry-0 — самая давняя.
            self._entry(tmp_path, f"entry-{i}", age_s=(stepik_client.CACHE_MAX_ENTRIES + 5 - i))

        removed = stepik_client.prune_cache(tmp_path)

        assert removed == 5
        survivors = {p.stem for p in tmp_path.glob("*.json")}
        assert len(survivors) == stepik_client.CACHE_MAX_ENTRIES
        assert "entry-0" not in survivors  # самая давняя выбыла
        assert f"entry-{stepik_client.CACHE_MAX_ENTRIES + 4}" in survivors  # свежая осталась

    def test_prune_on_missing_dir_is_noop(self, tmp_path: pathlib.Path) -> None:
        assert stepik_client.prune_cache(tmp_path / "nope") == 0

    def test_clear_cache_removes_everything(self, tmp_path: pathlib.Path) -> None:
        for i in range(3):
            self._entry(tmp_path, f"e{i}")

        assert stepik_client.clear_cache(tmp_path) == 3
        assert list(tmp_path.glob("*.json")) == []

    def test_cached_get_prunes_on_write(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Уборка происходит при записи ответа, а не только по явному вызову.

        Отдельного фонового потока у кэшей в проекте нет (тот же best-effort
        приём, что у реестра job'ов): если не чистить на записи, лимит и TTL
        останутся мёртвой буквой — их просто некому применить.
        """
        monkeypatch.setattr(stepik_client, "CACHE_DIR", tmp_path)
        stale = self._entry(tmp_path, "old-key", age_s=stepik_client.CACHE_TTL_SECONDS + 60)

        response = MagicMock()
        response.json.return_value = {"ok": True}
        monkeypatch.setattr(stepik_client, "_get_with_retry", lambda *a, **k: response)

        data = stepik_client._cached_api_get(MagicMock(), "https://stepik.org/api/steps")

        assert data == {"ok": True}
        assert not stale.exists(), "просроченная запись пережила запись нового ответа"
