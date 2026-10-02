"""Веб-слой обязан ОТВЕЧАТЬ, а не рвать соединение (issue #922).

Находка ``RUN-3-04`` подэпика: NUL-байт в ``path`` роняет обработчик
``ValueError``-ом, и `http.server` закрывает соединение молча. Клиент видит не
ошибку, а обрыв — одностраничный интерфейс остаётся в состоянии «идёт запрос»,
и единственным выходом становится перезагрузка страницы. Ровно это критерий
приёмки подэпика запрещает: «ни один раздел не должен требовать перезагрузки
как единственного выхода».

Проверяется два рубежа:

1. **Конкретная причина названа.** NUL-байт — это 400 с текстом, а не 500 и не
   обрыв: у известного дефекта обязан быть свой код и своё сообщение.
2. **Неизвестная причина не роняет связь.** Любое необработанное исключение
   хендлера становится ответом 500 с текстом, а не разрывом.

Первый рубеж проверяется и при ``confine=False``: резолв там идёт без единой
проверки, и режим «доступ к любому пути» не должен оказываться режимом
«падение без ответа».
"""

from __future__ import annotations

import http.client
import json
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from stepik_grader.web import api_routes
from stepik_grader.web import server as web_server
from stepik_grader.web.http_guards import path_is_usable

#: Путь с NUL-байтом. Литералом, а не через конструктор: важно, что это ровно
#: те байты, которые доедут до `pathlib`.
NUL_PATH = "solution\x00.py"


def _serve(workspace: pathlib.Path, *, confine: bool) -> Iterator[str]:
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0),
        web_server._Handler,
        workspace=workspace,
        confine=confine,
        sandbox=False,
        record_history=False,
        lang="ru",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()


@pytest.fixture
def server(tmp_path: pathlib.Path) -> Iterator[str]:
    """Сервер с конфайнментом — обычный режим."""
    yield from _serve(tmp_path, confine=True)


@pytest.fixture
def open_server(tmp_path: pathlib.Path) -> Iterator[str]:
    """Сервер без конфайнмента (``--no-root-confinement``)."""
    yield from _serve(tmp_path, confine=False)


def _get(base: str, route: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """GET, где обрыв соединения — это провал теста, а не исключение мимо.

    ``RemoteDisconnected`` перехватывается намеренно и превращается в понятный
    ``pytest.fail``: иначе отчёт назвал бы симптом («клиент не смог прочитать
    ответ») вместо дефекта («сервер не ответил вовсе»).
    """
    url = f"{base}{route}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    except (http.client.RemoteDisconnected, ConnectionError) as exc:
        pytest.fail(f"сервер оборвал соединение вместо ответа: {type(exc).__name__}")


def _post(base: str, route: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{base}{route}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": base},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    except (http.client.RemoteDisconnected, ConnectionError) as exc:
        pytest.fail(f"сервер оборвал соединение вместо ответа: {type(exc).__name__}")


class TestPathIsUsable:
    """Чистая функция: что именно считается непригодным путём."""

    def test_a_nul_byte_is_named(self) -> None:
        assert "NUL" in path_is_usable(NUL_PATH)

    def test_an_ordinary_path_passes(self) -> None:
        assert path_is_usable("tasks/task1_1.py") == ""

    def test_a_path_that_merely_leaves_the_workspace_is_not_this_check(self) -> None:
        """Выход за корень — вопрос конфайнмента, и ответ на него другой."""
        assert path_is_usable("../../etc/passwd") == ""


class TestNulByteInPath:
    """Тот самый обрыв: до правки соединение закрывалось без ответа."""

    def test_get_answers_with_a_reason(self, server: str) -> None:
        status, data = _get(server, "/api/source", {"path": NUL_PATH})

        assert status == 400
        assert data["message_id"] == "path_not_usable"

    def test_post_answers_with_a_reason(self, server: str, tmp_path: pathlib.Path) -> None:
        """Через тело POST — тот же путь резолвится тем же гардом."""
        status, data = _post(
            server,
            "/api/save-solution",
            {"folder": str(tmp_path), "path": NUL_PATH, "code": "x = 1\n"},
        )

        assert status == 400
        assert data["message_id"] == "path_not_usable"

    def test_the_check_does_not_depend_on_confinement(self, open_server: str) -> None:
        """С ``--no-root-confinement`` резолв идёт без проверок — и всё равно отвечает.

        Проверка стоит ДО ветки конфайнмента именно поэтому: иначе режим
        «доступ к любому пути» оказался бы режимом «падение без ответа».
        """
        status, data = _get(open_server, "/api/source", {"path": NUL_PATH})

        assert status == 400
        assert data["message_id"] == "path_not_usable"

    def test_the_file_is_never_touched(self, server: str, tmp_path: pathlib.Path) -> None:
        """Отказ идёт до файловой системы: ничего не создано и не прочитано."""
        before = sorted(p.name for p in tmp_path.iterdir())

        _post(
            server,
            "/api/save-solution",
            {"folder": str(tmp_path), "path": NUL_PATH, "code": "x = 1\n"},
        )

        assert sorted(p.name for p in tmp_path.iterdir()) == before


class TestUnknownFailureStillAnswers:
    """Последний рубеж: неизвестное исключение — это 500, а не обрыв."""

    def test_a_handler_that_raises_gets_an_answer(
        self, server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Гейт запускается против того, что обязан поймать (правило 140).

        Хендлер подменяется на падающий: без рубежа `http.server` напечатал бы
        трейсбек и закрыл соединение, и тест упал бы на `RemoteDisconnected`.
        """

        def _boom(self: Any, parsed: Any, lang: str) -> None:
            raise RuntimeError("что-то пошло не так внутри")

        monkeypatch.setattr(api_routes._ApiRoutesMixin, "_get_source", _boom, raising=True)

        status, data = _get(server, "/api/source", {"path": "solution.py"})

        assert status == 500
        assert data["message_id"] == "server_internal_error"
        assert "RuntimeError" in data["message"]

    def test_a_started_response_is_not_overwritten(
        self, server: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Если ответ уже начат, второго не будет: дописать статус поверх нельзя.

        Хендлер отвечает и падает следом. Клиент обязан получить ПЕРВЫЙ ответ
        целиком — попытка приклеить к нему 500 испортила бы поток сильнее самой
        ошибки.
        """

        def _answer_then_boom(self: Any, parsed: Any, lang: str) -> None:
            self._send(200, "application/json; charset=utf-8", b'{"kind": "source"}')
            raise RuntimeError("падение после отправки")

        monkeypatch.setattr(
            api_routes._ApiRoutesMixin, "_get_source", _answer_then_boom, raising=True
        )

        status, data = _get(server, "/api/source", {"path": "solution.py"})

        assert status == 200
        assert data["kind"] == "source"
