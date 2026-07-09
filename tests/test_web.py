"""Tests for web.py — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1)."""

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


def _make_task(tmp_path: pathlib.Path, body: str, *, with_tests: bool = True) -> pathlib.Path:
    """Создать task.py и (опционально) папку tests/ с одним кейсом 4 -> 5."""
    sol = tmp_path / "task.py"
    sol.write_text(body, encoding="utf-8")
    if with_tests:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "1").write_text("4", encoding="utf-8")
        (tests / "1.clue").write_text("5", encoding="utf-8")
    return sol


# ---------------------------------------------------------------------------
# grade_path
# ---------------------------------------------------------------------------


class TestGradePath:
    def test_passing_file(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_path(str(sol))
        assert data["kind"] == "file"
        assert len(data["rows"]) == 1
        row = data["rows"][0]
        assert row["status"] == "OK"
        assert row["passed"] == row["total"] == 1
        assert row["cases"][0]["verdict"] == "AC"
        assert row["cases"][0]["diff"] == ""

    def test_failing_file_has_diff(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4 -> 6, ждём 5
        row = web.grade_path(str(sol))["rows"][0]
        assert row["status"] == "FAIL"
        assert row["cases"][0]["verdict"] == "WA"
        assert row["cases"][0]["diff"]  # непустой diff

    def test_directory(self, tmp_path: pathlib.Path) -> None:
        _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_path(str(tmp_path))
        assert data["kind"] == "dir"
        assert data["rows"][0]["status"] == "OK"

    def test_nonexistent_path(self) -> None:
        data = web.grade_path("/no/such/path.py")
        assert data["kind"] == "error"
        assert "не найден" in data["message"].lower()
        assert data["rows"] == []

    def test_empty_directory(self, tmp_path: pathlib.Path) -> None:
        data = web.grade_path(str(tmp_path))
        assert data["kind"] == "error"
        assert "не найден" in data["message"].lower()

    def test_file_without_tests_marked_no_tests(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        row = web.grade_path(str(sol))["rows"][0]
        assert row["status"] == "NO TESTS"
        assert row["total"] == 0

    def test_wa_case_carries_stdin_from_test_case(self, tmp_path: pathlib.Path) -> None:
        """grade_path() wires stdin through to the case's ErrorCard (issue #125)."""
        sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4 -> 6, ждём 5
        case = web.grade_path(str(sol))["rows"][0]["cases"][0]
        assert case["stdin"] == "4"
        assert case["actual"] == "6"
        assert case["expected"] == "5"

    def test_re_case_carries_exit_code_from_core(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "raise ValueError('boom')\n")
        case = web.grade_path(str(sol))["rows"][0]["cases"][0]
        assert case["verdict"] == "RE"
        assert case["exit_code"] not in (0, None)
        assert case["stderr"] == case["error"]


# ---------------------------------------------------------------------------
# ErrorCard fields on _case_view — issue #125 (web-mvp.md § Модель error cards)
# ---------------------------------------------------------------------------


class TestErrorCardFields:
    def test_ac_case_has_minimal_fields_only(self) -> None:
        case = web._case_view(
            1, {"passed": True, "verdict": "AC", "time": 0.01, "output": ["5"]}, stdin="4"
        )
        assert case["case_n"] == 1
        assert case["actions"] == ["run_again", "copy_input", "copy_output"]
        for key in ("severity", "suggestions", "glossary_ids", "expected", "stderr", "timeout_s"):
            assert key not in case

    def test_wa_case_error_card_fields(self) -> None:
        case = web._case_view(
            2,
            {
                "passed": False,
                "verdict": "WA",
                "time": 0.02,
                "output": ["6"],
                "expected": ["5"],
                "diff": "- 5\n+ 6",
                "error": "",
            },
            stdin="4",
        )
        assert case["severity"] == "error"
        assert case["expected"] == "5"
        assert case["actual"] == "6"
        assert case["diff"]
        assert "glossary_ids" not in case  # WA never gets glossary_ids (RE only)
        assert set(case["actions"]) == {"run_again", "copy_input", "copy_output", "explain_error"}

    def test_re_case_known_exception_has_glossary_ids_and_suggestion(self) -> None:
        case = web._case_view(
            3,
            {
                "passed": False,
                "verdict": "RE",
                "time": 0.03,
                "output": [],
                "error": "KeyError: 'x'",
                "exit_code": 1,
            },
            stdin="4",
        )
        assert case["severity"] == "error"
        assert case["stderr"] == "KeyError: 'x'"
        assert case["exit_code"] == 1
        assert case["glossary_ids"] == ["keyerror"]
        assert case["suggestions"]  # non-empty — curated hint from core/glossary.py
        assert "open_glossary" in case["actions"]

    def test_re_case_unknown_exception_has_empty_glossary_ids(self) -> None:
        case = web._case_view(
            4,
            {
                "passed": False,
                "verdict": "RE",
                "time": 0.01,
                "output": [],
                "error": "CustomProjectError: boom",
                "exit_code": 1,
            },
        )
        assert case["glossary_ids"] == []
        assert case["suggestions"] == []
        assert "open_glossary" not in case["actions"]

    def test_tle_case_error_card_fields(self) -> None:
        from stepik_grader.config import CONFIG

        case = web._case_view(
            5,
            {
                "passed": False,
                "verdict": "TLE",
                "time": CONFIG.timeout_seconds,
                "output": [],
                "error": f"Timeout after {CONFIG.timeout_seconds}s",
                "exit_code": None,
                "timed_out": True,
            },
        )
        assert case["severity"] == "warning"
        assert case["timeout_s"] == CONFIG.timeout_seconds
        assert case["exit_code"] is None
        assert case["suggestions"]
        assert "glossary_ids" not in case  # TLE never links glossary content
        assert "expected" not in case


# ---------------------------------------------------------------------------
# grade_benchmark (режим бенчмарка)
# ---------------------------------------------------------------------------


class TestGradeBenchmark:
    def test_benchmark_file(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        data = web.grade_benchmark(str(sol), repeats=3)
        assert data["mode"] == "bench"
        row = data["rows"][0]
        assert row["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}
        assert row["runs"] >= 1
        assert isinstance(row["median"], str)  # отформатировано fmt_time

    def test_benchmark_dir_ranks_all_solutions(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        data = web.grade_benchmark(str(tmp_path), repeats=3)
        assert data["kind"] == "dir"
        assert len(data["rows"]) == 2
        # Строки отсортированы по возрастанию медианы — самый быстрый первым.
        assert all("verdict" in r for r in data["rows"])

    def test_benchmark_error_row_for_missing_tests(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(1)\n", with_tests=False)
        row = web.grade_benchmark(str(sol))["rows"][0]
        assert row["verdict"] == "ERR"
        assert row["error"]

    def test_benchmark_nonexistent_path(self) -> None:
        assert web.grade_benchmark("/no/such/dir")["kind"] == "error"


# ---------------------------------------------------------------------------
# HTTP-хендлер (интеграционно: реальный сервер на эфемерном порту)
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: pathlib.Path):
    """Поднять сервер на 127.0.0.1:0 в отдельном потоке, вернуть базовый URL."""
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


class TestHttpHandler:
    def test_index_serves_html(self, server: str) -> None:
        status, body = _get(server + "/")
        assert status == 200
        assert b"<!doctype html>" in body.lower()
        assert b"Stepik Python Grader" in body

    def test_api_grade_returns_json(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        status, body = _get(server + "/api/grade?path=" + urllib.parse.quote(str(sol)))
        assert status == 200
        data = json.loads(body)
        assert data["kind"] == "file"
        assert data["rows"][0]["status"] == "OK"

    def test_api_grade_without_path_is_error(self, server: str) -> None:
        status, body = _get(server + "/api/grade")
        assert status == 200
        assert json.loads(body)["kind"] == "error"

    def test_api_grade_bench_mode(self, server: str, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n")
        q = urllib.parse.urlencode({"path": str(sol), "mode": "bench", "repeats": "3"})
        status, body = _get(server + "/api/grade?" + q)
        assert status == 200
        data = json.loads(body)
        assert data["mode"] == "bench"
        assert data["rows"][0]["verdict"] in {"SIMILAR", "SLOWER", "MUCH_SLOWER"}

    def test_index_injects_default_path(self, server: str) -> None:
        # Плейсхолдер __DEFAULT_PATH__ должен быть заменён на реальный cwd.
        _, body = _get(server + "/")
        assert b"__DEFAULT_PATH__" not in body

    def test_unknown_path_404(self, server: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(server + "/nope")
        assert exc.value.code == 404

    # -- static routes (issue #125 — JS/CSS extracted from _INDEX_HTML) ------

    def test_static_app_css_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.css", timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
            assert "text/css" in resp.headers["Content-Type"]
            assert b":root" in resp.read()

    def test_static_app_js_served(self, server: str) -> None:
        with urllib.request.urlopen(server + "/static/app.js", timeout=5) as resp:  # noqa: S310
            assert resp.status == 200
            assert "javascript" in resp.headers["Content-Type"]
            assert b"function grade" in resp.read()


# ---------------------------------------------------------------------------
# Client-side esc() — HTML-attribute hardening (issue #214)
# ---------------------------------------------------------------------------
#
# esc() is embedded JS (no JS runtime in this Python test suite), so these are
# source-level regression checks: they pin down the escape table/regex that
# errorCard() relies on when inserting glossary.url into href="...". A quote
# character reaching that attribute unescaped would let it be broken out of.
#
# issue #125: the JS moved from an inline <script> in _INDEX_HTML to its own
# static/app.js file (re-exported as web._APP_JS) — these regressions now grep
# that instead.


def _ht_table_source() -> str:
    start = web._APP_JS.index("const HT = {")
    end = web._APP_JS.index("};", start)
    return web._APP_JS[start:end]


def test_client_esc_table_covers_html_and_attribute_special_chars() -> None:
    table_src = _ht_table_source()
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert f'"{entity}"' in table_src, f"{entity!r} missing from client-side HT map"


def test_client_esc_regex_includes_quote_chars() -> None:
    # The replace() char class must include both quote characters, or esc()
    # would keep stripping only &/</> and leave href="...' open to breakout.
    assert "replace(/[&<>\"']/g" in web._APP_JS


def test_error_card_url_field_is_passed_through_esc() -> None:
    # errorCard() must run g.url through esc() before inserting it into
    # href="..." -- if a future edit inlines g.url directly, this fails.
    assert r'href="' + "'" + " + esc(g.url) + " + "'" + '"' in web._APP_JS
