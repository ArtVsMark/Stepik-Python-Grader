"""Переход в глоссарий из ошибки — эндпоинт и его границы (issue #1220).

Метрика заведена, чтобы ответить на один вопрос: работает ли связка «упал →
понял». Поэтому пишется **только** deep-link из разбора ошибки; открытие
раздела «Глоссарий» руками не пишется, иначе показатель превратился бы в
«просмотрел N карточек» и перестал отвечать на этот вопрос.

Метод POST, а не параметр к `GET /api/glossary/<id>`: у записи есть побочный
эффект, а GET браузер волен повторить или предзагрузить — тогда «пришёл из
ошибки» считало бы кэш и префетчи.
"""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from stepik_grader.core import history
from stepik_grader.web import server as web_server
from stepik_grader.web import viewmodels


@pytest.fixture
def hit_server(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, pathlib.Path]]:
    """Сервер с включённой историей; отдаёт URL и путь к базе."""
    db = tmp_path / history.HISTORY_DB_NAME
    monkeypatch.setenv("STEPIK_GRADER_HISTORY_DB", str(db))
    monkeypatch.setattr(viewmodels, "_web_record_history", True)
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0), web_server._Handler, workspace=tmp_path, confine=True
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}", db
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(url: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


class TestEndpoint:
    def test_hit_is_recorded(self, hit_server: tuple[str, pathlib.Path]) -> None:
        url, db = hit_server
        status, data = _post(
            url + "/api/glossary/hit",
            {
                "card_id": "indexerror",
                "failure_kind": "runtime-error:IndexError",
                "error_class": "IndexError",
            },
        )

        assert (status, data["recorded"]) == (200, True)
        rows = history.read_glossary_hits(db)
        assert [(r["card_id"], r["error_class"]) for r in rows] == [("indexerror", "IndexError")]

    def test_optional_fields_may_be_absent(self, hit_server: tuple[str, pathlib.Path]) -> None:
        """Подсказка есть, таксономия не сработала — переход всё равно ценен."""
        url, db = hit_server
        _post(url + "/api/glossary/hit", {"card_id": "keyerror"})
        row = history.read_glossary_hits(db)[0]

        assert (row["failure_kind"], row["error_class"]) == (None, None)

    def test_empty_card_id_is_rejected(self, hit_server: tuple[str, pathlib.Path]) -> None:
        url, _ = hit_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(url + "/api/glossary/hit", {"card_id": "  "})

        assert exc.value.code == 400
        assert json.loads(exc.value.read())["message_id"] == "glossary_no_card_id"

    def test_reading_a_card_does_not_record_anything(
        self, hit_server: tuple[str, pathlib.Path]
    ) -> None:
        """`GET /api/glossary/<id>` — просто чтение: листание не метрика."""
        url, db = hit_server
        with urllib.request.urlopen(url + "/api/glossary/keyerror", timeout=5) as resp:
            assert resp.status == 200

        assert history.read_glossary_hits(db) == []


class TestConsent:
    def test_history_off_records_nothing_and_says_so(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отдельного тумблера у этих данных нет — согласие одно (ADR-0002)."""
        db = tmp_path / history.HISTORY_DB_NAME
        monkeypatch.setenv("STEPIK_GRADER_HISTORY_DB", str(db))
        monkeypatch.setattr(viewmodels, "_web_record_history", False)
        httpd = web_server._GraderServer(
            ("127.0.0.1", 0), web_server._Handler, workspace=tmp_path, confine=True
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address[0], httpd.server_address[1]
        try:
            status, data = _post(
                f"http://{host}:{port}/api/glossary/hit", {"card_id": "indexerror"}
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        assert (status, data["recorded"]) == (200, False)
        assert not db.exists()


class TestCaseCarriesTheErrorKey:
    """Браузеру нужен тот же ключ ошибки, под которым кейс лёг в историю."""

    def test_failing_case_reports_failure_kind(self) -> None:
        from stepik_grader.web.viewmodels import _case_view

        view = _case_view(
            1,
            {"verdict": "RE", "error": "IndexError: list index out of range", "passed": False},
        )

        assert view["failure_kind"] == "runtime-error:IndexError"

    def test_passing_case_has_no_error_key(self) -> None:
        from stepik_grader.web.viewmodels import _case_view

        view = _case_view(1, {"verdict": "AC", "passed": True, "output": ["1"]})

        assert "failure_kind" not in view
