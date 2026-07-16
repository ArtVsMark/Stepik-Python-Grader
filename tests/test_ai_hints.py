"""Tests for core/ai_hints.py — opt-in AI-подсказки WA/RE (issue #435, ADR-0003).

Мок-HTTP (без сети): happy path, таймаут, невалидный ключ, битый ответ — канал
НИКОГДА не роняет грейдинг (всё → None). Плюс: ключ из env + редакция секрета,
дефолт-off skip, пометка/усечение ответа.
"""

from __future__ import annotations

import dataclasses

import pytest
import requests

from stepik_grader.config import CONFIG
from stepik_grader.core import ai_hints, diag_log


def _cfg(**over: object) -> object:
    """GraderConfig с включённым AI-каналом по умолчанию (для тестов)."""
    base = {"ai_base_url": "http://test.local/v1", "ai_model": "test-model"}
    base.update(over)
    return dataclasses.replace(CONFIG, **base)  # type: ignore[arg-type]


class _FakeResponse:
    def __init__(self, *, json_data: object = None, raise_exc: Exception | None = None) -> None:
        self._json = json_data
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self) -> object:
        return self._json


def _ctx() -> ai_hints.FailureContext:
    return ai_hints.FailureContext(verdict="WA", diff="- 5\n+ 6", failure_kind="wrong-answer")


def _patch_post(monkeypatch: pytest.MonkeyPatch, fn: object) -> list[dict[str, object]]:
    """Заменить requests.post на fn, вернуть список перехваченных kwargs."""
    calls: list[dict[str, object]] = []

    def _post(url: str, **kwargs: object) -> object:
        calls.append({"url": url, **kwargs})
        return fn(url, **kwargs)

    monkeypatch.setattr(requests, "post", _post)
    return calls


def _ok(*_a: object, **_k: object) -> _FakeResponse:
    return _FakeResponse(
        json_data={"choices": [{"message": {"content": "Ты прибавил 2 вместо 1."}}]}
    )


def test_skip_when_not_configured() -> None:
    """Дефолт (нет ai_base_url) → is_configured ложно, explain_failure → None."""
    assert ai_hints.is_configured(CONFIG) is False
    assert ai_hints.explain_failure(_ctx(), CONFIG) is None


def test_happy_path_returns_marked_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _ok)
    hint = ai_hints.explain_failure(_ctx(), _cfg())
    assert hint is not None
    assert ai_hints.AI_MARKER_RU in hint
    assert "прибавил 2" in hint


def test_english_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _ok)
    ctx = dataclasses.replace(_ctx(), lang="en")
    hint = ai_hints.explain_failure(ctx, _cfg())
    assert hint is not None and ai_hints.AI_MARKER_EN in hint


def test_timeout_skips_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise requests.exceptions.Timeout("timed out")

    _patch_post(monkeypatch, _boom)
    assert ai_hints.explain_failure(_ctx(), _cfg()) is None  # грейдинг не падает


def test_invalid_key_http_error_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unauth(*_a: object, **_k: object) -> _FakeResponse:
        return _FakeResponse(raise_exc=requests.exceptions.HTTPError("401"))

    _patch_post(monkeypatch, _unauth)
    assert ai_hints.explain_failure(_ctx(), _cfg()) is None


def test_malformed_response_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def _weird(*_a: object, **_k: object) -> _FakeResponse:
        return _FakeResponse(json_data={"unexpected": True})  # нет choices

    _patch_post(monkeypatch, _weird)
    assert ai_hints.explain_failure(_ctx(), _cfg()) is None


def test_empty_content_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    def _empty(*_a: object, **_k: object) -> _FakeResponse:
        return _FakeResponse(json_data={"choices": [{"message": {"content": "   "}}]})

    _patch_post(monkeypatch, _empty)
    assert ai_hints.explain_failure(_ctx(), _cfg()) is None


def test_key_from_env_sets_auth_and_registers_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_AI_KEY", "supersecretkey123")
    calls = _patch_post(monkeypatch, _ok)
    ai_hints.explain_failure(_ctx(), _cfg(ai_api_key_env="TEST_AI_KEY"))
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer supersecretkey123"  # type: ignore[index]
    # секрет зарегистрирован в diag_log → редактируется в логах
    assert diag_log.redact("key=supersecretkey123") == "key=***redacted***"


def test_local_provider_no_key_no_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_AI_KEY", raising=False)
    calls = _patch_post(monkeypatch, _ok)
    ai_hints.explain_failure(_ctx(), _cfg(ai_api_key_env="TEST_AI_KEY"))
    assert "Authorization" not in calls[0]["headers"]  # type: ignore[operator]


def test_long_response_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    def _long(*_a: object, **_k: object) -> _FakeResponse:
        return _FakeResponse(json_data={"choices": [{"message": {"content": "x" * 5000}}]})

    _patch_post(monkeypatch, _long)
    hint = ai_hints.explain_failure(_ctx(), _cfg())
    assert hint is not None and len(hint) < 2000  # marker + clip < 5000


def test_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST на {base_url}/chat/completions с model/messages/max_tokens."""
    import json as _json

    calls = _patch_post(monkeypatch, _ok)
    ai_hints.explain_failure(_ctx(), _cfg(ai_max_tokens=222))
    assert calls[0]["url"] == "http://test.local/v1/chat/completions"
    payload = _json.loads(calls[0]["data"])  # type: ignore[arg-type]
    assert payload["model"] == "test-model"
    assert payload["max_tokens"] == 222
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
