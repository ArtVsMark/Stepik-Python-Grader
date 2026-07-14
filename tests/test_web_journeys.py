"""Cross-adapter user-journey tests for the web UI (issue #129).

`tests/test_web.py`/`tests/test_web_downloader.py`/`tests/test_web_glossary.py`
already cover most journeys from `docs/web-current.md § User journeys` in
isolation (added incrementally alongside #125/#186/#187):

- J1 (AC)                    -> test_web.py::TestGradePath.test_passing_file
- J2 (WA + diff)              -> test_web.py::TestGradePath/TestErrorCardFields
- J3 (RE + glossary block)    -> test_web.py::TestGradePath/TestErrorCardFields
- J4 (TLE)                    -> test_web.py::TestErrorCardFields (mapping only;
  core TLE detection itself is covered by
  test_grader_core.py::test_run_single_test_exit_code_none_on_tle)
- J6 (microbench)             -> test_web.py::TestGradeMicrobench + HTTP test
- J7 (missing-glossary queue) -> test_web.py::TestGradePath +
  test_web_glossary.py::TestGlossaryMissing
- Glossary card view          -> test_web_glossary.py::TestGlossaryHttpEndpoints
- Command palette registry    -> test_web_glossary.py::TestCommandRegistry/TestCommandsHttpEndpoint

What's still missing (per the issue #129 comment after PR #185, which
explicitly says not to close #129 using only the original 3-item checklist):
the above adapters were never proven to work *together*. This file closes
that gap with three end-to-end chains, each linking two independently-tested
adapters:

- TestDownloaderToGradeJourney       — download_task() -> grade_path() on the
  same path (J0 -> J1 handoff).
- TestErrorCardToGlossaryNavigation  — an RE case's glossary_ids resolve to a
  real card via glossary_adapter/HTTP (the "open_glossary" action's target
  actually exists, not a 404).
- TestMissingGlossaryDraftJourney    — an entry queued by grade_path() is
  visible through glossary_adapter.glossary_missing(), the same read path
  /api/glossary/missing uses (not just the lower-level json_provider).

Command-palette keyboard flows (Ctrl+K/arrows/Enter/Escape) are pure
client-side JS with no server-side behavior to assert on; per the project's
non-goals (no Selenium/Playwright) they're verified manually through a
running server, the same tradeoff #125 documented.
"""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from stepik_grader import web
from stepik_grader.web import downloader_adapter, glossary_adapter

_VALID_SECRETS = {
    "client_id": "cid",
    "client_secret": "csecret",
    "redirect_uri": "http://localhost:8080/callback",
}


def _write_secrets(path: pathlib.Path) -> None:
    path.write_text(json.dumps(_VALID_SECRETS), encoding="utf-8")


@pytest.fixture
def server(tmp_path: pathlib.Path):
    # issue #261: server confines request paths to workspace=tmp_path — every
    # path this file's tests grade/read already lives under tmp_path.
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


# ---------------------------------------------------------------------------
# J0 -> J1: a downloaded task is immediately gradable at the path
# download_task() returned (docs/web-current.md J0: "путь скачанной папки
# автоматически подставляется -> пользователь сразу нажимает ▶").
# ---------------------------------------------------------------------------


class TestDownloaderToGradeJourney:
    def test_downloaded_task_is_immediately_gradable(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_secrets(tmp_path / "secrets.json")
        task_dir = tmp_path / "course" / "section" / "lesson" / "04-step"
        task_dir.mkdir(parents=True)
        (task_dir / "task4_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (task_dir / "meta.json").write_text("{}", encoding="utf-8")
        tests_dir = task_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "1").write_text("4", encoding="utf-8")
        (tests_dir / "1.clue").write_text("5", encoding="utf-8")

        with (
            patch(
                "stepik_grader.web.downloader_adapter.try_create_session_without_browser",
                return_value=MagicMock(),
            ),
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(task_dir, 1, "html_table"),
            ),
        ):
            downloaded = downloader_adapter.download_task("https://stepik.org/lesson/1/step/4")
        assert downloaded["ok"] is True

        graded = web.grade_path(pathlib.Path(downloaded["path"]))

        assert graded["kind"] == "dir"
        row = graded["rows"][0]
        assert row["status"] == "OK"
        assert row["passed"] == row["total"] == 1


# ---------------------------------------------------------------------------
# Error-card -> glossary-card navigation: the glossary_ids surfaced on an RE
# case must resolve to a real card, not a dead link (docs/web-current.md J3
# "Action card Open glossary -> раздел «Глоссарий», карточка ... без ухода
# из оболочки").
# ---------------------------------------------------------------------------


def _make_re_task(tmp_path: pathlib.Path, exc: str) -> pathlib.Path:
    sol = tmp_path / "task.py"
    sol.write_text(f"raise {exc}\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")
    return sol


class TestErrorCardToGlossaryNavigation:
    def test_re_case_glossary_id_resolves_to_real_card(self, tmp_path: pathlib.Path) -> None:
        sol = _make_re_task(tmp_path, "KeyError('x')")
        case = web.grade_path(sol)["rows"][0]["cases"][0]
        assert case["verdict"] == "RE"
        assert case["glossary_ids"] == ["keyerror"]

        card = glossary_adapter.glossary_get(case["glossary_ids"][0])

        assert card is not None
        assert card["title"] == "KeyError"

    def test_http_grade_then_glossary_lookup_chain(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        sol = _make_re_task(tmp_path, "RecursionError('too deep')")
        status, body = _get(server + "/api/grade?" + urllib.parse.urlencode({"path": str(sol)}))
        assert status == 200
        case = json.loads(body)["rows"][0]["cases"][0]
        assert case["glossary_ids"], case

        status, body = _get(server + "/api/glossary/" + case["glossary_ids"][0])

        assert status == 200
        assert json.loads(body)["title"] == "RecursionError"


# ---------------------------------------------------------------------------
# Missing-glossary-draft journey: an entry queued while grading must be
# visible through glossary_adapter.glossary_missing() -- the same read path
# GET /api/glossary/missing uses -- not just the lower-level json_provider
# (docs/web-current.md J7).
# ---------------------------------------------------------------------------


class TestMissingGlossaryDraftJourney:
    def test_queued_entry_visible_through_glossary_missing_adapter(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Since #356 the RE hint resolver also consults the bundled JSON base
        # (~140 stdlib exceptions), so a genuine glossary gap must be absent from
        # BOTH sources — a user-defined (non-stdlib) exception qualifies. Built
        # inline via type() so _make_re_task's single `raise {exc}` still works.
        sol = _make_re_task(tmp_path, "type('UnlistedError', (Exception,), {})('unusual')")
        queue_path = tmp_path / "missing.json"

        web.grade_path(sol, missing_queue_path=queue_path)
        entries = glossary_adapter.glossary_missing(queue_path=queue_path)

        assert len(entries) == 1
        assert entries[0]["concept"] == "UnlistedError"
