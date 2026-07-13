"""Tests for web/glossary_adapter.py — Глоссарий web-эндпоинты (issue #125).

Direct function tests (glossary_search/get/missing) plus HTTP-level tests via
a real ThreadingHTTPServer on an ephemeral port, mirroring tests/test_web.py's
established pattern.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.glossary.models import GlossaryCard, GlossaryMissingEntry
from stepik_grader.web import glossary_adapter
from stepik_grader.web.commands import COMMANDS, filter_commands

# ---------------------------------------------------------------------------
# glossary_search / glossary_get — direct function tests
# ---------------------------------------------------------------------------


class TestGlossarySearchNoStoreConfigured:
    """store_path=None (and no CONFIG.glossary_store) → комплектная база (#326);
    при её отсутствии — компактный ``core/glossary.py`` fallback."""

    def test_search_empty_query_returns_all_cards(self) -> None:
        cards = glossary_adapter.glossary_search("")
        # база — импортированные ready-карточки (#326) + автогенерированные
        # черновики (#328); есть и те, и другие статусы.
        assert len(cards) > 500
        assert any(c["status"] == "ready" for c in cards)
        assert any(c["status"] == "draft" for c in cards)

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

    def test_default_serves_bundled_base_not_only_exceptions(self) -> None:
        # issue #326: комплектная база даёт не только исключения — напр.
        # встроенную функцию input(), которой нет в компактном fallback.
        card = glossary_adapter.glossary_get("input")
        assert card is not None
        assert card["kind"] == "function"

    def test_true_fallback_when_bundled_absent(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # issue #326: если комплектной базы нет — деградируем на ~28 исключений
        # core/glossary.py (KeyError есть, builtin input() — нет).
        monkeypatch.setattr(glossary_adapter, "BUNDLED_GLOSSARY_DIR", tmp_path / "nope")
        cards = glossary_adapter.glossary_search("")
        ids = {c["id"] for c in cards}
        assert "keyerror" in ids
        assert "input" not in ids


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


class TestCardCache:
    """issue #339: memoization карточек по mtime — без репарсинга на каждый вызов."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        glossary_adapter._CARDS_CACHE.clear()
        yield
        glossary_adapter._CARDS_CACHE.clear()

    @staticmethod
    def _write(path: pathlib.Path, cards: list[GlossaryCard]) -> None:
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_repeated_load_hits_cache(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A")])
        first = glossary_adapter._all_cards(p)
        second = glossary_adapter._all_cards(p)
        assert second is first  # тот же объект → файл не перечитывался

    def test_mtime_change_invalidates_cache(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A")])
        first = glossary_adapter._all_cards(p)
        assert {c.id for c in first} == {"a"}
        # переписать с новой карточкой и сдвинуть mtime вперёд (детерминированно)
        self._write(p, [GlossaryCard(id="a", title="A"), GlossaryCard(id="b", title="B")])
        st = p.stat()
        os.utime(p, (st.st_atime + 10, st.st_mtime + 10))
        second = glossary_adapter._all_cards(p)
        assert second is not first
        assert {c.id for c in second} == {"a", "b"}  # правка подхвачена

    def test_bundled_base_cached_across_calls(self) -> None:
        a = glossary_adapter._all_cards(None)
        b = glossary_adapter._all_cards(None)
        assert a is b and len(a) > 500  # комплектная база кешируется


class TestGlossaryFilterAndSort:
    """issue #329: фильтры section/kind/status (без объединения типов) + sort."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(
                id="list-append",
                title="list.append()",
                kind="function",
                section="Списки (list)",
                status="ready",
            ),
            GlossaryCard(
                id="tuple-count",
                title="tuple.count()",
                kind="function",
                section="Кортежи (tuple)",
                status="ready",
            ),
            GlossaryCard(
                id="match-case",
                title="match/case",
                kind="construct",
                section="Условный оператор",
                status="draft",
                version="3.10",
            ),
            GlossaryCard(
                id="zzz-term",
                title="zzz",
                kind="term",
                section="Списки (list)",
                status="ready",
            ),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_section_filter_does_not_merge_list_and_tuple(self, store_path: pathlib.Path) -> None:
        # Ключевое требование владельца: «Кортежи» не показывают списки.
        tuples = glossary_adapter.glossary_search(
            "", section="Кортежи (tuple)", store_path=str(store_path)
        )
        assert {c["id"] for c in tuples} == {"tuple-count"}
        lists = glossary_adapter.glossary_search(
            "", section="Списки (list)", store_path=str(store_path)
        )
        assert {c["id"] for c in lists} == {"list-append", "zzz-term"}

    def test_kind_filter(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", kind="construct", store_path=str(store_path))
        assert {c["id"] for c in res} == {"match-case"}

    def test_status_filter(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", status="draft", store_path=str(store_path))
        assert {c["id"] for c in res} == {"match-case"}

    def test_query_and_section_combine(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search(
            "append", section="Списки (list)", store_path=str(store_path)
        )
        assert {c["id"] for c in res} == {"list-append"}

    def test_sort_az(self, store_path: pathlib.Path) -> None:
        titles = [
            c["title"]
            for c in glossary_adapter.glossary_search("", sort="az", store_path=str(store_path))
        ]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_section(self, store_path: pathlib.Path) -> None:
        sections = [
            c["section"]
            for c in glossary_adapter.glossary_search(
                "", sort="section", store_path=str(store_path)
            )
        ]
        assert sections == sorted(sections, key=str.lower)

    def test_sort_version_orders_versioned_first(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", sort="version", store_path=str(store_path))
        assert res[0]["version"] == "3.10"  # версионированные — вперёд
        assert all(c["version"] == "" for c in res[1:])  # без версии — в конец


# ---------------------------------------------------------------------------
# code_terms — мини-карточки функций из кода песочницы (issue #321)
# ---------------------------------------------------------------------------


class TestCodeTerms:
    """issue #321/#322: концепции кода → карточки (+has_card, методы, очередь)."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(
                id="sorted",
                title="sorted()",
                kind="function",
                status="ready",
                summary="Новый отсортированный список из итерируемого.",
            ),
            # id без префикса модуля — проверяет матч по «хвосту» (math.sqrt → sqrt)
            GlossaryCard(id="sqrt", title="math.sqrt()", kind="function", status="ready"),
            # метод встроенного типа — матчится с голого имени split (issue #322)
            GlossaryCard(id="str.split", title="str.split()", kind="function", status="ready"),
            GlossaryCard(id="match/case", title="match/case", kind="construct", status="draft"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_notable_builtin_maps_to_card(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms("xs = sorted([3, 1, 2])", store_path=str(store_path))
        assert [t["id"] for t in terms] == ["sorted"]
        assert terms[0]["has_card"] is True
        assert terms[0]["summary"]  # покрытый термин несёт summary для мини-карточки
        assert terms[0]["confidence"] == "high"

    def test_dotted_concept_matches_by_tail(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "import math\nx = math.sqrt(4)\n", store_path=str(store_path)
        )
        assert any(t["id"] == "sqrt" and t["has_card"] for t in terms)  # math.sqrt → карта sqrt

    def test_method_detected_with_low_confidence(self, store_path: pathlib.Path) -> None:
        # issue #322: s.split() → карта str.split; confidence=low (тип получателя неизвестен)
        terms = glossary_adapter.code_terms(
            "s = 'a,b'\nparts = s.split(',')\n", store_path=str(store_path)
        )
        split = next(t for t in terms if t["id"] == "str.split")
        assert split["has_card"] is True
        assert split["confidence"] == "low"

    def test_match_case_construct_detected(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "match x:\n    case 1:\n        pass\n", store_path=str(store_path)
        )
        assert any(t["id"] == "match/case" for t in terms)

    def test_recognized_builtin_without_card_is_dimmed(self, store_path: pathlib.Path) -> None:
        # issue #322: повседневные builtin'ы теперь распознаются; без карточки в
        # базе возвращаются has_card=False (панель рисует их приглушённо)
        terms = {
            t["id"]: t
            for t in glossary_adapter.code_terms("print(len([1]))", store_path=str(store_path))
        }
        assert terms["print"]["has_card"] is False
        assert terms["len"]["has_card"] is False

    def test_covered_terms_sorted_before_uncovered(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "xs = sorted([1])\nprint(xs)\n", store_path=str(store_path)
        )
        assert terms[0]["id"] == "sorted" and terms[0]["has_card"]  # покрытые вперёд
        assert any(not t["has_card"] for t in terms)  # print — без карточки, ниже

    def test_user_defined_names_excluded(self, store_path: pathlib.Path) -> None:
        code = "def helper():\n    return 1\nhelper()\n"
        assert glossary_adapter.code_terms(code, store_path=str(store_path)) == []

    def test_syntax_error_returns_empty(self, store_path: pathlib.Path) -> None:
        assert glossary_adapter.code_terms("def (:", store_path=str(store_path)) == []

    def test_no_duplicate_card_for_repeated_concept(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms("sorted([]); sorted([1])", store_path=str(store_path))
        assert [t["id"] for t in terms] == ["sorted"]

    def test_queue_code_gaps_appends_uncovered_concept(
        self, store_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        # issue #322 practice-канал: заметная функция без карточки → очередь
        queue = tmp_path / "missing.json"
        code = "import functools\nfunctools.reduce(lambda a, b: a + b, [1])\n"
        glossary_adapter.queue_code_gaps(
            code, source="sol.py", queue_path=queue, store_path=str(store_path)
        )
        concepts = {e["concept"] for e in glossary_adapter.glossary_missing(queue_path=queue)}
        assert "functools.reduce" in concepts

    def test_queue_code_gaps_bad_path_is_silent(self, store_path: pathlib.Path) -> None:
        # defensive: незаписываемый путь очереди не роняет (best-effort)
        glossary_adapter.queue_code_gaps(
            "xs = sorted([1])", queue_path=pathlib.Path("/nonexistent/dir/q.json")
        )


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
    # issue #261: this file only hits /api/glossary*/api/commands (no `path`
    # param) — workspace just has to exist for the handler's incidental uses.
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
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


def _post_json(url: str, body: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (localhost only)
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

    def test_api_code_terms_returns_matched_cards(self, server: str) -> None:
        # issue #321: POST кода → мини-карточки функций (bundled-база несёт sorted).
        status, body = _post_json(server + "/api/code-terms", {"code": "xs = sorted([1])"})
        assert status == 200
        terms = json.loads(body)["terms"]
        assert any(t["id"] == "sorted" for t in terms)

    def test_api_code_terms_empty_code_returns_empty(self, server: str) -> None:
        status, body = _post_json(server + "/api/code-terms", {"code": "   "})
        assert status == 200
        assert json.loads(body)["terms"] == []

    def test_api_code_terms_by_path_reads_confined_file(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        # issue #322: тело {path} — сервер читает файл из workspace (confine) и
        # возвращает термины его кода (режим 2, по выбранному решению).
        (tmp_path / "sol.py").write_text("xs = sorted([3, 1, 2])\n", encoding="utf-8")
        status, body = _post_json(server + "/api/code-terms", {"path": "sol.py"})
        assert status == 200
        assert any(t["id"] == "sorted" for t in json.loads(body)["terms"])

    def test_api_glossary_kind_param_filters(self, server: str) -> None:
        # issue #329: сервер прокидывает kind в glossary_search.
        status, body = _get(
            server + "/api/glossary?" + urllib.parse.urlencode({"kind": "exception"})
        )
        assert status == 200
        cards = json.loads(body)
        assert cards and all(c["kind"] == "exception" for c in cards)

    def test_api_glossary_sort_az_orders_titles(self, server: str) -> None:
        status, body = _get(server + "/api/glossary?" + urllib.parse.urlencode({"sort": "az"}))
        assert status == 200
        titles = [c["title"] for c in json.loads(body)]
        assert titles == sorted(titles, key=str.lower)

    def test_api_glossary_missing_empty_by_default(self, server: str) -> None:
        # CONFIG.glossary_missing_queue defaults to a relative path that
        # doesn't exist in the test's cwd -- graceful empty list, not a 500.
        status, body = _get(server + "/api/glossary/missing")
        assert status == 200
        assert json.loads(body) == []


# ---------------------------------------------------------------------------
# commands.py — единый реестр команд (issue #125)
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    def test_registry_has_exactly_the_mvp_ids(self) -> None:
        ids = {c["id"] for c in COMMANDS}
        assert ids == {
            "run_again",
            "copy_input",
            "copy_output",
            "explain_error",
            "open_glossary",
            "toggle_theme",
            "switch_section",
        }

    def test_registry_never_includes_out_of_scope_actions(self) -> None:
        """Regression guard: create_test/compare_solutions are design-only for
        this issue (docs/web-design.md § Action cards, отложенные) — never register them."""
        ids = {c["id"] for c in COMMANDS}
        assert "create_test" not in ids
        assert "compare_solutions" not in ids

    def test_every_command_has_bilingual_title(self) -> None:
        for cmd in COMMANDS:
            assert set(cmd["title"]) == {"ru", "en"}
            assert cmd["title"]["ru"] and cmd["title"]["en"]

    def test_filter_commands_none_returns_everything(self) -> None:
        assert filter_commands(None) == COMMANDS

    def test_filter_commands_always_tag_is_never_excluded(self) -> None:
        result = filter_commands(set())
        ids = {c["id"] for c in result}
        assert {"run_again", "toggle_theme", "switch_section"} <= ids

    def test_filter_commands_context_tag_includes_matching_command(self) -> None:
        result = filter_commands({"has_glossary"})
        ids = {c["id"] for c in result}
        assert "open_glossary" in ids
        assert "copy_input" not in ids  # has_stdin not in context


class TestCommandsHttpEndpoint:
    def test_api_commands_without_context_returns_full_registry(self, server: str) -> None:
        status, body = _get(server + "/api/commands")
        assert status == 200
        assert len(json.loads(body)) == len(COMMANDS)

    def test_api_commands_with_context_filters(self, server: str) -> None:
        status, body = _get(server + "/api/commands?context=has_stdin,is_failure")
        assert status == 200
        ids = {c["id"] for c in json.loads(body)}
        assert "copy_input" in ids
        assert "explain_error" in ids
        assert "open_glossary" not in ids
