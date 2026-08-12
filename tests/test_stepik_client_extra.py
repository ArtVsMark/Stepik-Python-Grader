"""Дополнительные mock-тесты для stepik_client.py.

Покрывают непокрытые пути: authorize_via_browser, _cached_api_get (с файловым
кэшем), и все fetch_* хелперы Stepik REST API. Все сетевые вызовы замоканы —
реальных HTTP-запросов и обращений к файловой системе вне tmp_path нет.
"""

from __future__ import annotations

import http.client
import pathlib
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from stepik_grader.core import stepik_client
from stepik_grader.core.stepik_client import (
    _cached_api_get,
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
            patch("stepik_grader.core.stepik_client.webbrowser.open") as mock_open,
            patch(
                "stepik_grader.core.stepik_client.wait_for_auth_code", return_value="CODE123"
            ) as mock_wait,
            patch(
                "stepik_grader.core.stepik_client.requests.post", return_value=token_resp
            ) as mock_post,
        ):
            result = authorize_via_browser("cid", "csecret", "http://localhost:8080/cb")

        mock_open.assert_called_once()
        mock_wait.assert_called_once()
        wait_args = mock_wait.call_args.args
        assert wait_args[0] == "localhost"
        assert wait_args[1] == 8080
        assert wait_args[2] == "/cb"
        assert isinstance(wait_args[3], str) and len(wait_args[3]) > 20  # random state
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
            patch(
                "stepik_grader.core.stepik_client.webbrowser.open",
                side_effect=OSError("no display"),
            ),
            patch("stepik_grader.core.stepik_client.wait_for_auth_code", return_value="CODE"),
            patch("stepik_grader.core.stepik_client.requests.post", return_value=token_resp),
        ):
            result = authorize_via_browser("cid", "csecret", "https://localhost/cb")
        assert result["access_token"] == "AT"

    def test_default_host_port_path(self):
        """redirect_uri без явных host/port/path → localhost, 80, /."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "AT", "expires_in": 100}
        with (
            patch("stepik_grader.core.stepik_client.webbrowser.open"),
            patch(
                "stepik_grader.core.stepik_client.wait_for_auth_code", return_value="C"
            ) as mock_wait,
            patch("stepik_grader.core.stepik_client.requests.post", return_value=token_resp),
        ):
            authorize_via_browser("cid", "cs", "https://example.org")
        wait_args = mock_wait.call_args.args
        assert wait_args[0] == "example.org"
        assert wait_args[1] == 80
        assert wait_args[2] == "/"


class TestAuthorizeViaBrowserState:
    """authorize_via_browser отправляет случайный state в authorize URL (issue #241)."""

    def test_auth_url_includes_random_state_matching_wait_call(self):
        """URL, открытый в браузере, содержит тот же state, что уходит в wait_for_auth_code."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "AT", "expires_in": 100}
        with (
            patch("stepik_grader.core.stepik_client.webbrowser.open") as mock_open,
            patch(
                "stepik_grader.core.stepik_client.wait_for_auth_code", return_value="C"
            ) as mock_wait,
            patch("stepik_grader.core.stepik_client.requests.post", return_value=token_resp),
        ):
            authorize_via_browser("cid", "cs", "http://localhost:8080/cb")

        opened_url = mock_open.call_args.args[0]
        sent_state = mock_wait.call_args.args[3]
        assert f"state={sent_state}" in opened_url

    def test_state_differs_between_calls(self):
        """Каждый вызов генерирует новый state (не хардкод/константа)."""
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "AT", "expires_in": 100}
        states = []
        with (
            patch("stepik_grader.core.stepik_client.webbrowser.open"),
            patch(
                "stepik_grader.core.stepik_client.wait_for_auth_code", return_value="C"
            ) as mock_wait,
            patch("stepik_grader.core.stepik_client.requests.post", return_value=token_resp),
        ):
            authorize_via_browser("cid", "cs", "http://localhost:8080/cb")
            states.append(mock_wait.call_args.args[3])
            authorize_via_browser("cid", "cs", "http://localhost:8080/cb")
            states.append(mock_wait.call_args.args[3])
        assert states[0] != states[1]


class TestCachedApiGet:
    """_cached_api_get кэширует JSON-ответы в файл с TTL."""

    def test_cache_miss_then_write(self, tmp_path: pathlib.Path):
        """Кэша нет → выполняется запрос, ответ пишется в кэш-файл."""
        resp = MagicMock()
        resp.json.return_value = {"lessons": [{"id": 1}]}
        session = MagicMock()
        with (
            patch.object(stepik_client, "CACHE_DIR", tmp_path / "cache"),
            patch(
                "stepik_grader.core.stepik_client._get_with_retry", return_value=resp
            ) as mock_get,
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
            patch(
                "stepik_grader.core.stepik_client._get_with_retry", return_value=resp
            ) as mock_get,
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
            patch("stepik_grader.core.stepik_client._get_with_retry", return_value=resp),
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
            patch(
                "stepik_grader.core.stepik_client._get_with_retry", return_value=resp
            ) as mock_get,
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
        with patch("stepik_grader.core.stepik_client._get_with_retry", return_value=resp):
            step = fetch_step_data(session, lesson_id=5, step_position=2)
        assert step["id"] == 22

    def test_pagination_second_page(self):
        """Шаг на второй странице — выполняется пагинация."""
        page1 = MagicMock()
        page1.json.return_value = {
            "steps": [{"position": 1}],
            "meta": {"has_next": True},
        }
        page2 = MagicMock()
        page2.json.return_value = {
            "steps": [{"position": 3, "id": 99}],
            "meta": {"has_next": False},
        }
        session = MagicMock()
        with patch(
            "stepik_grader.core.stepik_client._get_with_retry", side_effect=[page1, page2]
        ) as mock_get:
            step = fetch_step_data(session, lesson_id=5, step_position=3)
        assert step["id"] == 99
        assert mock_get.call_count == 2

    def test_not_found_raises(self):
        """Шаг с искомой позицией отсутствует → ValueError."""
        resp = MagicMock()
        resp.json.return_value = {
            "steps": [{"position": 1}],
            "meta": {"has_next": False},
        }
        session = MagicMock()
        with patch("stepik_grader.core.stepik_client._get_with_retry", return_value=resp):
            with pytest.raises(ValueError, match="не найден"):
                fetch_step_data(session, lesson_id=5, step_position=42)


class TestFetchSingletonHelpers:
    """fetch_lesson/unit/section/course — обёртки над _cached_api_get."""

    def test_fetch_lesson_ok(self):
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get",
            return_value={"lessons": [{"id": 7}]},
        ):
            assert fetch_lesson_data(MagicMock(), 7)["id"] == 7

    def test_fetch_lesson_missing_raises(self):
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get", return_value={"lessons": []}
        ):
            with pytest.raises(ValueError, match="Урок"):
                fetch_lesson_data(MagicMock(), 7)

    def test_fetch_unit_with_id(self):
        """unit_id передаётся в params."""
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get", return_value={"units": [{"id": 3}]}
        ) as m:
            assert fetch_unit_data(MagicMock(), 5, 3)["id"] == 3
        _, kwargs = m.call_args
        assert kwargs["params"] == {"lesson": 5, "id": 3}

    def test_fetch_unit_without_id(self):
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get", return_value={"units": [{"id": 9}]}
        ) as m:
            fetch_unit_data(MagicMock(), 5, None)
        _, kwargs = m.call_args
        assert "id" not in kwargs["params"]

    def test_fetch_unit_missing_raises(self):
        with patch("stepik_grader.core.stepik_client._cached_api_get", return_value={"units": []}):
            with pytest.raises(ValueError, match="Юнит"):
                fetch_unit_data(MagicMock(), 5, None)

    def test_fetch_section_ok_and_missing(self):
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get",
            return_value={"sections": [{"id": 2}]},
        ):
            assert fetch_section_data(MagicMock(), 2)["id"] == 2
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get", return_value={"sections": []}
        ):
            with pytest.raises(ValueError, match="Секция"):
                fetch_section_data(MagicMock(), 2)

    def test_fetch_course_ok_and_missing(self):
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get",
            return_value={"courses": [{"id": 4}]},
        ):
            assert fetch_course_data(MagicMock(), 4)["id"] == 4
        with patch(
            "stepik_grader.core.stepik_client._cached_api_get", return_value={"courses": []}
        ):
            with pytest.raises(ValueError, match="Курс"):
                fetch_course_data(MagicMock(), 4)


class TestFetchSubmissionData:
    """fetch_submission_data возвращает последний сабмишн или None."""

    def test_returns_latest(self):
        resp = MagicMock()
        resp.json.return_value = {"submissions": [{"id": 1}, {"id": 2}]}
        with patch("stepik_grader.core.stepik_client._get_with_retry", return_value=resp):
            assert fetch_submission_data(MagicMock(), 100)["id"] == 1

    def test_returns_none_when_empty(self):
        resp = MagicMock()
        resp.json.return_value = {"submissions": []}
        with patch("stepik_grader.core.stepik_client._get_with_retry", return_value=resp):
            assert fetch_submission_data(MagicMock(), 100) is None


# ── OAuth-колбэк и валидация токен-ответа (issue #943) ─────────────────────


# Тесты ходят по 127.0.0.1, а не по "localhost", намеренно: ``HTTPServer``
# всегда IPv4 (``address_family = AF_INET``), а на macOS "localhost"
# резолвится сначала в IPv6 ``::1`` — ``urlopen`` честно ждёт там таймаут
# вместо мгновенного отказа (браузер в этой ситуации переключается на IPv4 сам,
# библиотека — нет). Прибитый литерал убирает эту неоднозначность из теста.
_LOOPBACK = "127.0.0.1"


def _free_port() -> int:
    """Свободный порт: два теста подряд не должны драться за один номер."""
    with socket.socket() as sock:
        sock.bind((_LOOPBACK, 0))
        return int(sock.getsockname()[1])


def _get(port: int, target: str) -> int:
    """Код ответа локального колбэк-сервера.

    Через ``http.client``, а не ``urllib``: ``urlopen`` уважает переменные
    окружения ``HTTP_PROXY``/``http_proxy`` и на раннере, где они выставлены,
    уводит запрос к loopback через прокси — оттуда ответа нет вовсе, и тест
    падает ``URLError: timed out`` вместо кода ответа (поймано матрицей CI на
    macOS). ``HTTPConnection`` идёт прямо на адрес и от окружения не зависит.
    """
    conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=10)
    try:
        conn.request("GET", target)
        return int(conn.getresponse().status)
    finally:
        conn.close()


def _wait_until_listening(
    port: int,
    thread: threading.Thread,
    outcome: dict[str, str],
    timeout: float = 20.0,
) -> None:
    """Дождаться, пока колбэк-сервер реально слушает порт.

    Фиксированный ``sleep`` — источник флака: поток мог не успеть поднять
    сервер на загруженном раннере, и первый же запрос уходил в никуда. Запас
    щедрый намеренно: старт упирается не в наш код, а в то, как быстро ОС
    отдаёт сокет.

    В сообщение об ошибке кладётся состояние потока и его исход — без этого
    падение на чужой ОС говорит лишь «не поднялся», и причину приходится
    угадывать по кругу через CI.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((_LOOPBACK, port)) == 0:
                return
        if not thread.is_alive():
            raise AssertionError(
                f"поток ожидания колбэка завершился, не подняв сервер на {port}: {outcome}"
            )
        time.sleep(0.05)
    raise AssertionError(
        f"колбэк-сервер не поднялся на порту {port} за {timeout}s "
        f"(поток жив: {thread.is_alive()}, исход: {outcome})"
    )


class TestOAuthCallbackSurvivesStrayRequests:
    """Колбэк-сервер обслуживает запросы до дедлайна, а не ровно один (#943).

    Раньше стоял единственный ``handle_request``, и посторонний GET съедал его
    целиком: браузер сам префетчит ``/favicon.ico``, а ветки 404 и
    ``state_mismatch`` тоже отвечают и возвращают управление. Сервер
    закрывался, настоящий редирект Stepik упирался в ECONNREFUSED, и
    пользователь мгновенно получал «код не получен за 120с».
    """

    def _await_code(self, port: int, state: str, timeout: int) -> dict[str, str]:
        """Запустить ожидание колбэка в фоне; вернуть словарь с исходом."""
        outcome: dict[str, str] = {}

        def waiter() -> None:
            try:
                outcome["code"] = stepik_client.wait_for_auth_code(
                    host=_LOOPBACK, port=port, path="/", expected_state=state, timeout=timeout
                )
            except Exception as exc:  # исход теста, а не глушение ошибки
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        outcome["_thread"] = thread  # type: ignore[assignment]
        return outcome

    def test_stray_requests_do_not_end_the_wait(self) -> None:
        """Префетч favicon и открытый вручную корень не срывают ожидание."""
        port = _free_port()
        outcome = self._await_code(port, "STATE123", timeout=20)
        thread: threading.Thread = outcome.pop("_thread")  # type: ignore[assignment]
        _wait_until_listening(port, thread, outcome)

        assert _get(port, "/favicon.ico") == 404
        assert _get(port, "/") == 400  # корень без параметров
        assert _get(port, "/robots.txt") == 404

        # После трёх посторонних запросов сервер ЖИВ и принимает настоящий колбэк.
        assert _get(port, "/?code=REALCODE&state=STATE123") == 200
        thread.join(timeout=15)
        assert outcome.get("code") == "REALCODE", outcome

    def test_csrf_callback_still_rejected(self) -> None:
        """Guard: ``code`` с чужим ``state`` по-прежнему отклоняется (issue #241).

        Терпимость к посторонним запросам не должна ослабить защиту от
        Login-CSRF: решение принимается только там, где есть ``code``/``error``,
        и там ``state`` сверяется строго.
        """
        port = _free_port()
        outcome = self._await_code(port, "STATE123", timeout=20)
        thread: threading.Thread = outcome.pop("_thread")  # type: ignore[assignment]
        _wait_until_listening(port, thread, outcome)

        assert _get(port, "/?code=EVIL&state=ATTACKER") == 400
        thread.join(timeout=15)
        assert "state_mismatch" in outcome.get("error", ""), outcome

    def test_timeout_actually_waits(self) -> None:
        """Таймаут отсчитывается по монотонным часам, а не срабатывает мгновенно.

        До фикса сообщение «код не получен за 120с» печаталось за 0.0 секунды —
        оно описывало ожидание, которого не было.
        """
        port = _free_port()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            stepik_client.wait_for_auth_code(
                host=_LOOPBACK, port=port, path="/", expected_state="S", timeout=2
            )
        assert time.monotonic() - started >= 1.5


class TestTokenPayloadValidation:
    """Ответ 200 без пригодных полей не сохраняется в secrets (#943, ADD-2-02)."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"expires_in": 3600},  # нет access_token вовсе
            {"access_token": "", "expires_in": 3600},  # пустой
            {"access_token": "   ", "expires_in": 3600},  # из одних пробелов
            {"access_token": 42, "expires_in": 3600},  # не строка
            {"access_token": "AT"},  # нет expires_in → float(None) ниже по пути
            {"access_token": "AT", "expires_in": None},
            {"access_token": "AT", "expires_in": "скоро"},
            {"access_token": "AT", "expires_in": True},  # bool — не срок жизни
        ],
    )
    def test_unusable_payload_rejected(self, payload: dict) -> None:
        with pytest.raises(ValueError):
            stepik_client._validate_token_payload(payload)

    def test_valid_payload_accepted(self) -> None:
        stepik_client._validate_token_payload({"access_token": "AT", "expires_in": 3600})
        stepik_client._validate_token_payload({"access_token": "AT", "expires_in": 3600.5})

    def test_refresh_rejects_200_without_access_token(self) -> None:
        """``refresh_access_token`` не отдаёт наружу ответ без токена.

        ``raise_for_status`` пропускает любой ``200``, поэтому проверка полей —
        единственное, что отделяет мусорный ответ от рабочего.
        """
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"expires_in": 3600}
        with patch("stepik_grader.core.stepik_client.requests.post", return_value=response):
            with pytest.raises(ValueError, match="access_token"):
                stepik_client.refresh_access_token("cid", "csecret", "RT")
