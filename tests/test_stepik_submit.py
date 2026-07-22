"""Тесты core-потока отправки решения на Stepik (issue #683).

Всё на моках сессии — НИ ОДНОГО реального запроса к Stepik (сабмит — необратимое
действие на платформе; сквозной живой прогон возможен только у пользователя с
его токеном). Проверяются: сборка attempt/submission-запросов, разбор ответов,
poll до вердикта и по таймауту, чтение step_id из meta.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stepik_grader.core import stepik_client
from stepik_grader.core.stepik_client import (
    SubmissionResult,
    _pick_python_language,
    create_attempt,
    fetch_step_languages,
    poll_submission,
    read_step_id,
    submit_and_wait,
    submit_solution,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Утиная замена requests.Session: очереди ответов на post/get.

    Для get: пока в очереди >1 элемента — pop по одному; на последнем —
    повторяет его (удобно для poll, который опрашивает один и тот же URL).
    """

    def __init__(
        self,
        post_payloads: list[dict[str, Any]] | None = None,
        get_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self._post = list(post_payloads or [])
        self._get = list(get_payloads or [])
        self.post_calls: list[tuple[str, Any]] = []
        self.get_calls: list[str] = []

    def post(self, url: str, json: Any = None, timeout: int | None = None) -> _FakeResponse:
        self.post_calls.append((url, json))
        return _FakeResponse(self._post.pop(0))

    def get(self, url: str, params: Any = None, timeout: int | None = None) -> _FakeResponse:
        self.get_calls.append(url)
        payload = self._get[0] if len(self._get) == 1 else self._get.pop(0)
        return _FakeResponse(payload)


class TestCreateAttempt:
    def test_returns_attempt_id_and_posts_step(self) -> None:
        session = _FakeSession(post_payloads=[{"attempts": [{"id": 42}]}])
        attempt_id = create_attempt(session, step_id=123)  # type: ignore[arg-type]
        assert attempt_id == 42
        url, body = session.post_calls[0]
        assert url.endswith("/api/attempts")
        assert body == {"attempt": {"step": 123}}

    def test_empty_attempts_raises(self) -> None:
        session = _FakeSession(post_payloads=[{"attempts": []}])
        with pytest.raises(ValueError, match="attempt"):
            create_attempt(session, step_id=1)  # type: ignore[arg-type]


class TestSubmitSolution:
    def test_returns_submission_id_and_builds_reply(self) -> None:
        session = _FakeSession(post_payloads=[{"submissions": [{"id": 7}]}])
        sid = submit_solution(session, attempt_id=42, code="print(1)")  # type: ignore[arg-type]
        assert sid == 7
        url, body = session.post_calls[0]
        assert url.endswith("/api/submissions")
        assert body["submission"]["attempt"] == 42
        assert body["submission"]["reply"] == {"language": "python3", "code": "print(1)"}

    def test_empty_submissions_raises(self) -> None:
        session = _FakeSession(post_payloads=[{"submissions": []}])
        with pytest.raises(ValueError, match="submission"):
            submit_solution(session, attempt_id=1, code="x")  # type: ignore[arg-type]


class TestPollSubmission:
    def test_evaluation_then_correct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stepik_client.time, "sleep", lambda _s: None)
        session = _FakeSession(
            get_payloads=[
                {"submissions": [{"id": 7, "status": "evaluation"}]},
                {"submissions": [{"id": 7, "status": "correct", "hint": "OK", "score": "1"}]},
            ]
        )
        result = poll_submission(session, submission_id=7)  # type: ignore[arg-type]
        assert isinstance(result, SubmissionResult)
        assert result.status == "correct"
        assert result.hint == "OK"
        assert result.score == "1"
        assert result.submission_id == 7
        assert len(session.get_calls) == 2

    def test_wrong_verdict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stepik_client.time, "sleep", lambda _s: None)
        session = _FakeSession(get_payloads=[{"submissions": [{"id": 9, "status": "wrong"}]}])
        result = poll_submission(session, submission_id=9)  # type: ignore[arg-type]
        assert result.status == "wrong"

    def test_timeout_stays_evaluation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stepik_client.time, "sleep", lambda _s: None)
        session = _FakeSession(get_payloads=[{"submissions": [{"id": 5, "status": "evaluation"}]}])
        result = poll_submission(session, submission_id=5, timeout=0.05, interval=0.0)  # type: ignore[arg-type]
        assert result.status == "evaluation"


_TEMPLATES = {
    "steps": [{"block": {"options": {"code_templates": {"python3.10": "", "python3.12": ""}}}}]
}


class TestSubmitAndWait:
    def test_full_flow_explicit_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stepik_client.time, "sleep", lambda _s: None)
        session = _FakeSession(
            post_payloads=[{"attempts": [{"id": 42}]}, {"submissions": [{"id": 7}]}],
            get_payloads=[{"submissions": [{"id": 7, "status": "correct"}]}],
        )
        result = submit_and_wait(session, step_id=123, code="print(1)", language="python3.12")  # type: ignore[arg-type]
        assert result.status == "correct"
        assert result.submission_id == 7
        assert session.post_calls[0][0].endswith("/api/attempts")
        assert session.post_calls[1][0].endswith("/api/submissions")
        assert session.post_calls[1][1]["submission"]["reply"]["language"] == "python3.12"

    def test_autodetects_language_from_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """language=None → берётся из code_templates шага (не хардкод python3)."""
        monkeypatch.setattr(stepik_client.time, "sleep", lambda _s: None)
        session = _FakeSession(
            post_payloads=[{"attempts": [{"id": 1}]}, {"submissions": [{"id": 2}]}],
            get_payloads=[_TEMPLATES, {"submissions": [{"id": 2, "status": "correct"}]}],
        )
        result = submit_and_wait(session, step_id=123, code="x")  # type: ignore[arg-type]
        assert result.status == "correct"
        assert session.post_calls[1][1]["submission"]["reply"]["language"] == "python3.12"


class TestFetchStepLanguages:
    def test_returns_code_template_keys(self) -> None:
        session = _FakeSession(get_payloads=[_TEMPLATES])
        assert fetch_step_languages(session, 2321459) == ["python3.10", "python3.12"]  # type: ignore[arg-type]

    def test_empty_when_no_steps(self) -> None:
        session = _FakeSession(get_payloads=[{"steps": []}])
        assert fetch_step_languages(session, 1) == []  # type: ignore[arg-type]


class TestPickPythonLanguage:
    def test_prefers_newest_python(self) -> None:
        assert _pick_python_language(["python3.10", "python3.12"]) == "python3.12"

    def test_fallback_when_no_python(self) -> None:
        assert _pick_python_language(["java", "c++"]) == "python3"


class TestReadStepId:
    def test_reads_step_id(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text(json.dumps({"step_id": 569749}), encoding="utf-8")
        assert read_step_id(tmp_path) == 569749

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_step_id(tmp_path) is None

    def test_broken_json_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text("{not json", encoding="utf-8")
        assert read_step_id(tmp_path) is None

    def test_missing_field_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text(json.dumps({"lesson_id": 1}), encoding="utf-8")
        assert read_step_id(tmp_path) is None

    def test_non_int_step_id_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text(json.dumps({"step_id": "569749"}), encoding="utf-8")
        assert read_step_id(tmp_path) is None
