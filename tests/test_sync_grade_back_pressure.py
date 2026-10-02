"""Синхронный грейд считается вместе с асинхронным (issue #922, ``ARCH-2-02``).

``GET /api/grade`` держит HTTP-запрос открытым всю длительность прогона и
job'у не заводит — а значит не попадал в учёт активных прогонов вовсе. Лимит
``max_active_runs`` его не видел, и наоборот: десять вкладок поднимали десять
подпроцессов Python при любом значении настройки. Настройка была, а половина
трафика шла мимо неё.

Проверяется учёт, а не отмена и не TTL: у синхронного запроса их не может быть
по природе (отменять нечего, результат никуда не складывается), и находка
называет три разных свойства, а не одно.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from stepik_grader.web import runs
from stepik_grader.web import server as web_server

_TIMEOUT_S = 30


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Учёт — глобальный, значит его надо возвращать в исходное состояние.

    Иначе занятый слот утёк бы в соседние тесты и уменьшил лимит незаметно —
    ровно тот отказ, от которого защищает `finally` в самом `sync_slot`.
    """
    yield
    assert runs._SYNC_ACTIVE == 0, "слот синхронного грейда не освобождён"


@pytest.fixture
def server(tmp_path: pathlib.Path) -> Iterator[str]:
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0),
        web_server._Handler,
        workspace=tmp_path,
        confine=True,
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


def _get(base: str, route: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(base + route, timeout=_TIMEOUT_S) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestTheSlotIsCounted:
    """Один учёт на сервер, а не один на способ запуска."""

    def test_a_sync_run_is_visible_to_the_async_limit(self) -> None:
        """Пока идёт синхронный грейд, асинхронный видит занятое место."""
        before = runs._count_active_locked()

        with runs.sync_slot():
            during = runs._count_active_locked()

        assert during == before + 1
        assert runs._count_active_locked() == before

    def test_the_slot_is_freed_even_when_the_run_explodes(self) -> None:
        """Исключение внутри не оставляет слот занятым.

        Иначе одна ошибка тихо уменьшала бы лимит, и сервер переставал бы
        принимать прогоны без единого сообщения о причине.
        """
        before = runs._count_active_locked()

        with pytest.raises(RuntimeError), runs.sync_slot():
            raise RuntimeError("грейд упал")

        assert runs._count_active_locked() == before

    def test_over_the_limit_it_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Достигнут лимит — отказ, а не молчаливое превышение."""
        monkeypatch.setattr(runs, "CONFIG", dataclasses.replace(runs.CONFIG, max_active_runs=1))

        with runs.sync_slot(), pytest.raises(runs.TooManyRunsError):
            with runs.sync_slot():
                pass

    def test_the_slot_is_released_for_the_next_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Отказ — не приговор: следующий прогон проходит.

        Лимит ограничивает одновременность, а не общее число прогонов; если бы
        слот не освобождался, первый же грейд закрывал бы сервер навсегда.
        """
        monkeypatch.setattr(runs, "CONFIG", dataclasses.replace(runs.CONFIG, max_active_runs=1))

        with runs.sync_slot():
            with pytest.raises(runs.TooManyRunsError), runs.sync_slot():
                pass

        with runs.sync_slot():
            pass  # место снова свободно


class TestTheEndpointAnswers:
    """Отказ доезжает до клиента тем же кодом, что и у асинхронного пути."""

    def test_a_busy_server_answers_429(
        self, server: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "task.py").write_text("print(1)\n", encoding="utf-8")
        monkeypatch.setattr(runs, "CONFIG", dataclasses.replace(runs.CONFIG, max_active_runs=1))

        with runs.sync_slot():  # место занято — как будто идёт соседний прогон
            status, data = _get(server, f"/api/grade?path={tmp_path / 'task.py'}")

        assert status == 429
        assert data["message_id"] == "too_many_runs"

    def test_a_free_server_still_grades(self, server: str, tmp_path: pathlib.Path) -> None:
        """Граница с другой стороны: обычный запрос работает как раньше."""
        (tmp_path / "task.py").write_text("print(1)\n", encoding="utf-8")

        status, data = _get(server, f"/api/grade?path={tmp_path / 'task.py'}")

        assert status == 200
        assert "rows" in data
