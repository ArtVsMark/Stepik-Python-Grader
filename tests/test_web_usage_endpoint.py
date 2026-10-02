"""`GET /api/v1/usage` — журнал прогонов соседнему инструменту (issue #1365).

Пункт 3 задачи: локальный HTTP-эндпоинт под `--serve`, за флагом. Главное
свойство здесь не формат, а **умолчание**: без явного `--expose-usage`
эндпоинта нет. Журнал человек копил для себя (`--stats`), и решение поделиться
им с соседним инструментом принимает он, а не умолчание, — поэтому первым идёт
тест выключенного состояния.

Сервер не поднимается: предмет — решение маршрута, а не сокет. Живой прогон
через сервер держит `tests/test_web.py`.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from stepik_grader.web import api_routes, usage_adapter


class _Recorder(api_routes._ApiRoutesMixin):
    """Хендлер без сокета: запоминает, чем ответили."""

    def __init__(self, *, expose_usage: bool) -> None:
        self.server = type("_S", (), {"expose_usage": expose_usage})()
        self.status: int | None = None
        self.payload: dict[str, Any] = {}

    def _send(self, status: int, _content_type: str, body: bytes) -> None:
        self.status = status
        self.payload = json.loads(body.decode("utf-8"))


def _parsed(query: str = ""):
    from urllib.parse import urlparse

    return urlparse(f"/api/v1/usage?{query}" if query else "/api/v1/usage")


@pytest.fixture
def journal(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Журнал прогонов с тремя записями — по одной на каждую отметку времени."""
    path = tmp_path / "stats.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"v": 1, "ts": ts, "mode": mode, "os": "Linux"})
            for ts, mode in ((10.0, 1), (20.0, 2), (30.0, 3))
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(usage_adapter, "collect_events", lambda **kwargs: _collect(path, **kwargs))
    return path


def _collect(path: pathlib.Path, **kwargs: Any) -> Any:
    from stepik_grader.core import usage_export

    return usage_export.collect_events(stats_path=path, **kwargs)


class TestDisabledByDefault:
    def test_without_the_flag_the_endpoint_looks_absent(self) -> None:
        """404, а не 403: выключенный эндпоинт снаружи и должен выглядеть отсутствующим."""
        handler = _Recorder(expose_usage=False)

        handler._get_usage(_parsed(), "ru")

        assert handler.status == 404
        assert handler.payload["message_id"] == "usage_endpoint_disabled"

    def test_refusal_names_the_flag(self) -> None:
        """Отказ без способа его снять — это тупик, а не ответ."""
        handler = _Recorder(expose_usage=False)

        handler._get_usage(_parsed(), "ru")

        assert "--expose-usage" in handler.payload["message"]

    def test_nothing_is_read_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Выключено — значит журнал даже не открывается."""

        def refuse(**_kwargs: object) -> None:
            raise AssertionError("журнал прочитан при выключенном эндпоинте")

        monkeypatch.setattr(usage_adapter, "collect_events", refuse)
        handler = _Recorder(expose_usage=False)

        handler._get_usage(_parsed(), "ru")

        assert handler.status == 404


class TestEnabled:
    def test_events_are_returned_in_the_declared_schema(self, journal: pathlib.Path) -> None:
        handler = _Recorder(expose_usage=True)

        handler._get_usage(_parsed(), "ru")

        assert handler.status == 200
        assert handler.payload["schema"] == "stepik-grader/usage/1"
        assert [event["mode"] for event in handler.payload["events"]] == [1, 2, 3]

    def test_skipped_lines_are_reported(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Пусто» и «всё побилось» не должны выглядеть одинаково."""
        path = tmp_path / "stats.jsonl"
        path.write_text('}{ не json\n{"ts": 1.0, "mode": 1}\n', encoding="utf-8")
        monkeypatch.setattr(
            usage_adapter, "collect_events", lambda **kwargs: _collect(path, **kwargs)
        )
        handler = _Recorder(expose_usage=True)

        handler._get_usage(_parsed(), "ru")

        assert handler.payload["skipped"] == 1

    def test_since_filters_older_records(self, journal: pathlib.Path) -> None:
        handler = _Recorder(expose_usage=True)

        handler._get_usage(_parsed("since=20"), "ru")

        assert [event["mode"] for event in handler.payload["events"]] == [2, 3]

    def test_garbage_since_means_no_boundary(self, journal: pathlib.Path) -> None:
        """Опечатка в необязательном параметре не повод отвечать ошибкой."""
        handler = _Recorder(expose_usage=True)

        handler._get_usage(_parsed("since=вчера"), "ru")

        assert handler.status == 200
        assert len(handler.payload["events"]) == 3

    def test_no_extra_fields_leave_the_journal(self, journal: pathlib.Path) -> None:
        """Тот же закрытый список полей, что у CLI: эндпоинт ничего не добавляет."""
        handler = _Recorder(expose_usage=True)

        handler._get_usage(_parsed(), "ru")

        for event in handler.payload["events"]:
            assert set(event) <= {
                "schema",
                "ts",
                "mode",
                "os",
                "verdicts",
                "total_time",
                "isolation",
            }


class TestSinceParsing:
    def test_positive_number_is_taken(self) -> None:
        assert api_routes._since_from_query({"since": ["12.5"]}) == 12.5

    def test_absent_parameter_is_none(self) -> None:
        assert api_routes._since_from_query({}) is None

    def test_zero_and_negative_are_no_boundary(self) -> None:
        """Ноль и отрицательное значат «всё» — это и есть отсутствие границы."""
        assert api_routes._since_from_query({"since": ["0"]}) is None
        assert api_routes._since_from_query({"since": ["-5"]}) is None


def test_route_is_registered() -> None:
    """Маршрут объявлен: обработчик без маршрута недостижим."""
    assert api_routes._ApiRoutesMixin._API_GET_EXACT["/api/v1/usage"] == "_get_usage"
