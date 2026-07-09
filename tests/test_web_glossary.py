"""Tests for web/glossary_adapter.py — Глоссарий web-эндпоинты (issue #125).

Direct function tests (glossary_search/get/missing) plus HTTP-level tests via
a real ThreadingHTTPServer on an ephemeral port, mirroring tests/test_web.py's
established pattern.
"""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from stepik_grader import web
from stepik_grader.glossary.models import GlossaryCard, GlossaryMissingEntry
from stepik_grader.web import glossary_adapter

# ---------------------------------------------------------------------------
# glossary_search / glossary_get — direct function tests
# ---------------------------------------------------------------------------


class TestGlossarySearchNoStoreConfigured:
    """store_path=None (and no CONFIG.glossary_store) → compact core/glossary.py fallback."""

    def test_search_empty_query_returns_all_fallback_cards(self) -> None:
        cards = glossary_adapter.glossary_search("")
        assert len(cards) > 0
        assert all(c["status"] == "ready" for c in cards)

    def test_search_matches_known_exception(self) -> None:
        cards = glossary_adapter.glossary_search("RecursionError")
        assert any(c["id"] == "recursionerror" for c in cards)

    def test_search_no_match_returns_empty(self) -> None:
        assert glossary_adapter.glossary_search("TotallyMadeUpTerm") == []

    def test_get_known_id_returns_card(self) -> None:
        card = glossary_adapter.glossary_get("keyerror")
        assert card is not None
        assert card["title"] == "KeyError"

    def test_get_unknown_id_returns_none(self) -> None:
        assert glossary_adapter.glossary_get("not-a-real-id") is None


class TestGlossarySearchWithConfiguredStore:
    """store_path pointing at a real JSON card file."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(id="functools-reduce", title="functools.reduce", kind="function"),
            GlossaryCard(id="match-case", title="match/case", kind="construct"),
        ]
        path = tmp_path / "glossary.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_search_returns_configured_cards_not_fallback(self, store_path: pathlib.Path) -> None:
        cards = glossary_adapter.glossary_search("", store_path=str(store_path))
        ids = {c["id"] for c in cards}
        assert ids == {"functools-reduce", "match-case"}

    def test_get_returns_configured_card(self, store_path: pathlib.Path) -> None:
        card = glossary_adapter.glossary_get("match-case", store_path=str(store_path))
        assert card is not None
        assert card["kind"] == "construct"

    def test_missing_store_file_falls_back_gracefully(self, tmp_path: pathlib.Path) -> None:
        cards = glossary_adapter.glossary_search("", store_path=str(tmp_path / "nope.json"))
        assert len(cards) > 0  # fell back to core/glossary.py, didn't raise


# ---------------------------------------------------------------------------
# glossary_missing — очередь пополнения (J7)
# ---------------------------------------------------------------------------


class TestGlossaryMissing:
    def test_missing_queue_absent_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert glossary_adapter.glossary_missing(queue_path=str(tmp_path / "nope.json")) == []

    def test_missing_queue_returns_entries(self, tmp_path: pathlib.Path) -> None:
        from stepik_grader.glossary.json_provider import save_missing_queue

        queue_path = tmp_path / "missing.json"
        save_missing_queue(
            queue_path,
            [GlossaryMissingEntry(concept="functools.reduce", kind="function")],
        )

        entries = glossary_adapter.glossary_missing(queue_path=str(queue_path))

        assert len(entries) == 1
        assert entries[0]["concept"] == "functools.reduce"


# ---------------------------------------------------------------------------
# HTTP-level — real server on an ephemeral port (same pattern as test_web.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: pathlib.Path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (localhost only)
        return resp.status, resp.read()


class TestGlossaryHttpEndpoints:
    def test_api_glossary_search_returns_json_list(self, server: str) -> None:
        status, body = _get(server + "/api/glossary?" + urllib.parse.urlencode({"q": "KeyError"}))
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        assert any(c["id"] == "keyerror" for c in data)

    def test_api_glossary_search_without_q_lists_everything(self, server: str) -> None:
        status, body = _get(server + "/api/glossary")
        assert status == 200
        assert len(json.loads(body)) > 0

    def test_api_glossary_get_known_id(self, server: str) -> None:
        status, body = _get(server + "/api/glossary/keyerror")
        assert status == 200
        assert json.loads(body)["title"] == "KeyError"

    def test_api_glossary_get_unknown_id_is_404(self, server: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(server + "/api/glossary/not-a-real-id")
        assert exc.value.code == 404

    def test_api_glossary_missing_empty_by_default(self, server: str) -> None:
        # CONFIG.glossary_missing_queue defaults to a relative path that
        # doesn't exist in the test's cwd -- graceful empty list, not a 500.
        status, body = _get(server + "/api/glossary/missing")
        assert status == 200
        assert json.loads(body) == []
