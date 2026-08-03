"""Tests for stepik_client.make_session()'s transport-level retry (issue #109/#110).

Spins up a real local HTTP server (same technique as tests/test_web.py) that
returns a scripted sequence of status codes on successive requests, so the
HTTPAdapter + urllib3 Retry mounted by make_session() is exercised against
actual HTTP responses rather than mocked internals — _get_with_retry() already
has its own mock-based tests in test_stepik_client.py; this file covers the
new transport-level mechanism specifically.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core.stepik_client import (
    RETRY_STATUS_FORCELIST,
    ExternalUrlRejected,
    external_download_get,
    make_session,
)


def _make_scripted_handler(statuses: list[int]) -> type[BaseHTTPRequestHandler]:
    """Хендлер, возвращающий статусы из ``statuses`` по порядку запросов
    (последний элемент повторяется, если запросов больше, чем статусов)."""
    request_count = {"n": 0}

    class ScriptedHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            index = min(request_count["n"], len(statuses) - 1)
            status = statuses[index]
            request_count["n"] += 1
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            pass  # тихий сервер — не засорять test output

    ScriptedHandler.request_count = request_count  # type: ignore[attr-defined]
    return ScriptedHandler


@pytest.fixture
def scripted_server() -> Iterator[object]:
    """Фабрика-фикстура: запустить сервер со скриптом статусов на 127.0.0.1:0."""
    servers = []

    def _start(statuses: list[int]) -> tuple[str, type[BaseHTTPRequestHandler]]:
        handler_cls = _make_scripted_handler(statuses)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        host, port = httpd.server_address[0], httpd.server_address[1]
        return f"http://{host}:{port}/", handler_cls

    yield _start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def test_make_session_retries_on_429_then_succeeds(scripted_server) -> None:
    url, handler_cls = scripted_server([429, 429, 200])
    session = make_session("token", retries=3, backoff_factor=0.01)

    response = session.get(url, timeout=5)

    assert response.status_code == 200
    assert handler_cls.request_count["n"] == 3


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_make_session_retries_on_5xx_then_succeeds(scripted_server, status) -> None:
    url, handler_cls = scripted_server([status, 200])
    session = make_session("token", retries=3, backoff_factor=0.01)

    response = session.get(url, timeout=5)

    assert response.status_code == 200
    assert handler_cls.request_count["n"] == 2


def test_make_session_does_not_retry_404(scripted_server) -> None:
    # 404 не входит в RETRY_STATUS_FORCELIST -- одна попытка, ответ возвращается
    # как есть (requests не поднимает исключение сам по себе без raise_for_status).
    url, handler_cls = scripted_server([404])
    session = make_session("token", retries=3, backoff_factor=0.01)

    response = session.get(url, timeout=5)

    assert response.status_code == 404
    assert handler_cls.request_count["n"] == 1


def test_make_session_gives_up_after_max_retries(scripted_server) -> None:
    url, handler_cls = scripted_server([503, 503, 503, 503, 503])
    session = make_session("token", retries=2, backoff_factor=0.01)

    with pytest.raises(requests.exceptions.RetryError):
        session.get(url, timeout=5)

    # total=2 retries -> 1 исходная попытка + 2 повтора = 3 запроса всего.
    assert handler_cls.request_count["n"] == 3


def test_make_session_default_retries_is_three(scripted_server) -> None:
    url, handler_cls = scripted_server([503, 503, 503, 503, 503])
    session = make_session("token", backoff_factor=0.01)  # retries по умолчанию

    with pytest.raises(requests.exceptions.RetryError):
        session.get(url, timeout=5)

    # По умолчанию total=3 -> 1 + 3 = 4 запроса.
    assert handler_cls.request_count["n"] == 4


def test_make_session_still_sets_auth_header() -> None:
    # issue #109 AC: "Поведение не ломает OAuth и обычные запросы" -- заголовки
    # авторизации остаются на месте после добавления retry-адаптера.
    session = make_session("my-token")
    assert session.headers["Authorization"] == "Bearer my-token"


def test_retry_status_forcelist_matches_documented_statuses() -> None:
    assert set(RETRY_STATUS_FORCELIST) == {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Ретраи и лимиты — issue #815
# ---------------------------------------------------------------------------


class TestRetryCoversPost:
    """`NETA-01`: POST не входит в дефолтный `allowed_methods` urllib3.

    Замерено до фикса: `_is_method_retryable('POST')` → False, то есть единичный
    429/503 на `/api/attempts` или `/api/submissions` ронял отправку решения с
    первой попытки — при том что докстринг `_post_json` обещал транспортный
    повтор.
    """

    def _retry(self) -> object:
        return make_session("tok").get_adapter("https://stepik.org").max_retries

    def test_post_is_retryable(self) -> None:
        assert self._retry()._is_method_retryable("POST") is True

    def test_idempotent_methods_still_retryable(self) -> None:
        """Контроль: прежние методы не потеряны."""
        retry = self._retry()
        for method in ("GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"):
            assert retry._is_method_retryable(method) is True, method

    def test_status_forcelist_unchanged(self) -> None:
        """Повторяются только «сервер занят», а не любые ошибки POST."""
        assert set(self._retry().status_forcelist) == {429, 500, 502, 503, 504}


class TestRetryAfterCeiling:
    """`NETA-03`: `Retry-After` принимался без потолка.

    Дефолт urllib3 — 21600 с (6 часов): один 429 с большим заголовком подвешивал
    поток-обработчик web-запроса без возможности отмены, а в CLI — весь процесс.
    """

    def test_retry_after_is_capped(self) -> None:
        retry = make_session("tok").get_adapter("https://stepik.org").max_retries
        assert retry.retry_after_max == 60
        assert retry.retry_after_max < 21600  # дефолт urllib3

    def test_retry_after_header_still_respected(self) -> None:
        """Потолок не отменяет уважение к заголовку — только ограничивает его."""
        retry = make_session("tok").get_adapter("https://stepik.org").max_retries
        assert retry.respect_retry_after_header is True


class TestExternalDownloadSizeLimit:
    """`NETA-04`: тело внешней загрузки читалось в память целиком.

    Адрес приходит из HTML задачи, `requests` без `stream=True` читает ответ
    полностью — ссылка на многогигабайтный файл выжирала RAM до OOM.
    """

    def _response(self, *, body: bytes, declared: str | None = None) -> MagicMock:
        resp = MagicMock()
        resp.is_redirect = False
        resp.content = body
        resp.headers = {} if declared is None else {"Content-Length": declared}
        return resp

    def test_oversized_content_length_is_rejected(self) -> None:
        resp = self._response(body=b"x", declared=str(100 * 1024 * 1024))
        session = MagicMock()
        session.get.return_value = resp
        with (
            patch("requests.Session", return_value=session),
            pytest.raises(ExternalUrlRejected, match="слишком велик"),
        ):
            external_download_get("https://github.com/o/r/big.zip")

    def test_oversized_body_without_header_is_rejected(self) -> None:
        """Заголовок необязателен и может врать — смотрим и фактический размер."""
        resp = self._response(body=b"A" * (70 * 1024 * 1024))
        session = MagicMock()
        session.get.return_value = resp
        with (
            patch("requests.Session", return_value=session),
            pytest.raises(ExternalUrlRejected, match="слишком велик"),
        ):
            external_download_get("https://github.com/o/r/big.zip")

    def test_normal_body_passes(self) -> None:
        """Контроль: обычный архив тест-кейсов лимит не задевает."""
        resp = self._response(body=b"A" * 1024, declared="1024")
        session = MagicMock()
        session.get.return_value = resp
        with patch("requests.Session", return_value=session):
            assert external_download_get("https://github.com/o/r/t.zip") is resp
