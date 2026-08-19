"""Тесты scripts/gh_rest.py — конвейер PR по REST (issue #1242).

Сеть здесь не нужна и делала бы набор флаки: ``urlopen`` подменяется через
параметр ``opener``, а для CLI — через ``_default_opener`` модуля. Проверяется
не «дошёл ли запрос», а три вещи, ради которых модуль и написан:

1. **Исчерпанная квота отличается от поломки.** ``403`` с
   ``x-ratelimit-remaining: 0`` обязан давать «ждать» с временем сброса, а не
   трассировку: агент, читающий это как ошибку, начинает повторять — и
   счётчик попыток растёт уже после нуля.
2. **Условный запрос отдаёт данные из кэша.** ``304`` приходит в urllib
   *ошибкой*; если её не поймать, самый дешёвый ответ GitHub (он не расходует
   лимит вовсе) выглядел бы сбоем.
3. **Токена нет — это внятное сообщение,** а не ``KeyError`` изнутри.
"""

from __future__ import annotations

import email.message
import importlib.util
import io
import json
import pathlib
import sys
import time
import urllib.error
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "gh_rest.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_gh_rest", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Модуль скрипта с кэшем во временном каталоге.

    Кэш переопределяется всегда: без этого тест писал бы ETag'и в настоящий
    ``~/.cache`` разработчика и подмешивал их в следующий прогон.
    """
    monkeypatch.setenv("GH_REST_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("GH_TOKEN", "test-token")
    yield _load_module()


def _headers(mapping: dict[str, Any]) -> email.message.Message:
    """Заголовки ответа — настоящий ``Message``, он регистронезависим."""
    message = email.message.Message()
    for key, value in mapping.items():
        message[key] = str(value)
    return message


class _FakeResponse:
    """Ответ ``urlopen``: контекст-менеджер с телом, статусом и заголовками."""

    def __init__(
        self, payload: Any, *, status: int = 200, headers: dict[str, Any] | None = None
    ) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = _headers(headers or {})

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _http_error(code: int, *, headers: dict[str, Any] | None = None, message: str = "") -> Any:
    """Готовый ``HTTPError`` с телом и заголовками, как у настоящего."""
    body = io.BytesIO(json.dumps({"message": message}).encode("utf-8"))
    return urllib.error.HTTPError(
        "https://api.github.com/x", code, message, _headers(headers or {}), body
    )


def _opener(*results: Any) -> Any:
    """Открыватель, отдающий заготовленные ответы по очереди."""
    queue = list(results)
    captured: list[Any] = []

    def _open(request: Any) -> Any:
        captured.append(request)
        result = queue.pop(0) if queue else _FakeResponse({})
        if isinstance(result, urllib.error.HTTPError):
            raise result
        return result

    _open.captured = captured  # type: ignore[attr-defined]
    return _open


class TestToken:
    """Откуда берётся токен и что происходит без него."""

    def test_env_token_wins(self, module: ModuleType) -> None:
        assert module.resolve_token(env={"GH_TOKEN": "abc"}) == "abc"

    def test_github_token_is_a_fallback(self, module: ModuleType) -> None:
        assert module.resolve_token(env={"GITHUB_TOKEN": "xyz"}) == "xyz"

    def test_blank_env_value_is_ignored(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(module.shutil, "which", lambda _name: None)
        with pytest.raises(module.MissingToken):
            module.resolve_token(env={"GH_TOKEN": "   "})

    def test_missing_token_explains_what_to_do(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(module.shutil, "which", lambda _name: None)
        with pytest.raises(module.MissingToken) as caught:
            module.resolve_token(env={})
        text = str(caught.value)
        assert "GH_TOKEN" in text
        assert "gh auth login" in text

    def test_gh_cli_is_asked_when_env_is_empty(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/gh")
        monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **k: "gho_from_cli\n")
        assert module.resolve_token(env={}) == "gho_from_cli"


class TestRateLimit:
    """Квота исчерпана — это «ждать», а не «упало»."""

    def test_exhausted_quota_raises_rate_limited(self, module: ModuleType) -> None:
        reset = int(time.time()) + 900
        error = _http_error(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(reset),
                "x-ratelimit-resource": "core",
            },
            message="API rate limit exceeded",
        )
        with pytest.raises(module.RateLimited) as caught:
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)
        assert caught.value.reset_at == reset
        assert caught.value.resource == "core"

    def test_wait_seconds_counts_down_to_reset(self, module: ModuleType) -> None:
        limited = module.RateLimited("x", reset_at=1000)
        assert limited.wait_seconds(now=400) == 600
        assert limited.wait_seconds(now=2000) == 0

    def test_description_names_the_reset_and_forbids_retry(self, module: ModuleType) -> None:
        text = module.RateLimited("x", reset_at=1000, resource="core").describe(now=400)
        assert "core" in text
        assert "Ждать, а не повторять" in text

    def test_secondary_limit_with_retry_after_is_a_wait(self, module: ModuleType) -> None:
        error = _http_error(429, headers={"retry-after": "60"}, message="secondary rate limit")
        with pytest.raises(module.RateLimited) as caught:
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)
        assert caught.value.wait_seconds() > 0

    def test_plain_403_is_not_a_wait(self, module: ModuleType) -> None:
        error = _http_error(403, headers={"x-ratelimit-remaining": "4000"}, message="Forbidden")
        with pytest.raises(module.GitHubError) as caught:
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)
        assert not isinstance(caught.value, module.RateLimited)
        assert "токен" in str(caught.value)

    def test_other_errors_carry_github_message(self, module: ModuleType) -> None:
        error = _http_error(404, message="Not Found")
        with pytest.raises(module.GitHubError) as caught:
            module.request("GET", "repos/x/y/pulls/9", opener=_opener(error), use_cache=False)
        assert "404" in str(caught.value)
        assert "Not Found" in str(caught.value)

    def test_network_failure_is_a_github_error(self, module: ModuleType) -> None:
        def _boom(_request: Any) -> Any:
            raise urllib.error.URLError("сеть недоступна")

        with pytest.raises(module.GitHubError):
            module.request("GET", "repos/x/y", opener=_boom, use_cache=False)


class TestConditionalRequests:
    """``304`` — самый дешёвый ответ: он не расходует лимит вовсе."""

    def test_etag_is_stored_and_reused(self, module: ModuleType) -> None:
        first = _FakeResponse({"number": 7}, headers={"etag": 'W/"abc"'})
        opener = _opener(first)
        module.request("GET", "repos/x/y/pulls/7", opener=opener)

        second = _opener(_http_error(304))
        response = module.request("GET", "repos/x/y/pulls/7", opener=second)
        assert response.status == 304
        assert response.from_cache is True
        assert response.data == {"number": 7}

    def test_if_none_match_is_sent_when_cache_exists(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"a": 1}, headers={"etag": 'W/"tag"'}))
        module.request("GET", "repos/x/y/z", opener=opener)
        second = _opener(_FakeResponse({"a": 1}, headers={"etag": 'W/"tag"'}))
        module.request("GET", "repos/x/y/z", opener=second)
        sent = second.captured[0]
        assert sent.get_header("If-none-match") == 'W/"tag"'

    def test_304_without_cache_is_an_error_not_silence(self, module: ModuleType) -> None:
        with pytest.raises(module.GitHubError):
            module.request("GET", "repos/x/y/fresh", opener=_opener(_http_error(304)))

    def test_cache_is_separated_by_token(self, module: ModuleType) -> None:
        """Чужой токен не читает кэш соседа — ``304`` отдал бы данные без спроса."""
        first = _opener(_FakeResponse({"secret": 1}, headers={"etag": 'W/"a"'}))
        module.request("GET", "repos/x/y/private", token="token-a", opener=first)

        second = _opener(_FakeResponse({"secret": 2}, headers={"etag": 'W/"b"'}))
        module.request("GET", "repos/x/y/private", token="token-b", opener=second)
        assert second.captured[0].get_header("If-none-match") is None

    def test_cache_is_skipped_for_writes(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"merged": True}, headers={"etag": 'W/"m"'}))
        module.request("PUT", "repos/x/y/pulls/1/merge", body={}, opener=opener)
        assert opener.captured[0].get_header("If-none-match") is None


class TestRequestShape:
    """Что именно уходит в GitHub."""

    def test_authorization_and_version_headers(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({}))
        module.request("GET", "rate_limit", token="tok", opener=opener, use_cache=False)
        sent = opener.captured[0]
        assert sent.get_header("Authorization") == "Bearer tok"
        assert sent.get_header("X-github-api-version")

    def test_body_is_json_encoded(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"number": 3}))
        module.request("POST", "repos/x/y/pulls", body={"title": "тема"}, opener=opener)
        sent = opener.captured[0]
        assert json.loads(sent.data.decode("utf-8")) == {"title": "тема"}
        assert sent.get_method() == "POST"

    def test_empty_body_response_is_none(self, module: ModuleType) -> None:
        response = module.request(
            "PUT", "repos/x/y/pulls/1/merge", body={}, opener=_opener(_FakeResponse(None))
        )
        assert response.data is None


class TestPipelineOperations:
    """Операции конвейера: список, сравнение, создание, обновление, мерж."""

    def test_list_pulls_reads_the_fields_the_queue_needs(self, module: ModuleType) -> None:
        payload = [
            {
                "number": 1242,
                "title": "REST-обёртка",
                "head": {"ref": "feat/gh-rest"},
                "base": {"ref": "main"},
                "user": {"login": "ArtVsMark"},
                "draft": True,
                "updated_at": "2026-08-19T10:00:00Z",
            }
        ]
        pulls = module.list_pulls("x/y", opener=_opener(_FakeResponse(payload)))
        assert len(pulls) == 1
        item = pulls[0]
        assert (item.number, item.branch, item.base) == (1242, "feat/gh-rest", "main")
        assert item.author == "ArtVsMark"
        assert item.draft is True
        assert "черновик" in item.describe()

    def test_list_pulls_survives_unexpected_payload(self, module: ModuleType) -> None:
        assert module.list_pulls("x/y", opener=_opener(_FakeResponse({"message": "нет"}))) == []

    def test_compare_reports_staleness(self, module: ModuleType) -> None:
        divergence = module.compare(
            "x/y", "main", "sha", opener=_opener(_FakeResponse({"ahead_by": 2, "behind_by": 5}))
        )
        assert (divergence.ahead, divergence.behind) == (2, 5)
        assert divergence.stale is True

    def test_up_to_date_branch_is_not_stale(self, module: ModuleType) -> None:
        divergence = module.compare(
            "x/y", "main", "sha", opener=_opener(_FakeResponse({"ahead_by": 3, "behind_by": 0}))
        )
        assert divergence.stale is False

    def test_missing_compare_fields_are_not_stale(self, module: ModuleType) -> None:
        divergence = module.compare("x/y", "main", "sha", opener=_opener(_FakeResponse({})))
        assert (divergence.ahead, divergence.behind, divergence.stale) == (0, 0, False)

    def test_create_pull_posts_the_expected_body(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"number": 5, "html_url": "u"}))
        created = module.create_pull(
            "x/y", title="тема", head="branch", body="тело", draft=True, opener=opener
        )
        sent = json.loads(opener.captured[0].data.decode("utf-8"))
        assert sent == {
            "title": "тема",
            "head": "branch",
            "base": "main",
            "body": "тело",
            "draft": True,
        }
        assert created["number"] == 5

    def test_update_branch_uses_put(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"message": "Updating pull request branch."}))
        module.update_branch("x/y", 1242, opener=opener)
        request = opener.captured[0]
        assert request.get_method() == "PUT"
        assert request.full_url.endswith("/pulls/1242/update-branch")

    def test_merge_defaults_to_squash(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"merged": True}))
        module.merge_pull("x/y", 1242, opener=opener)
        sent = json.loads(opener.captured[0].data.decode("utf-8"))
        assert sent["merge_method"] == "squash"

    def test_merge_method_is_configurable(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"merged": True}))
        module.merge_pull("x/y", 1242, method="rebase", opener=opener)
        assert json.loads(opener.captured[0].data.decode("utf-8"))["merge_method"] == "rebase"

    def test_rate_limit_is_parsed(self, module: ModuleType) -> None:
        payload = {
            "resources": {
                "core": {"limit": 5000, "remaining": 4993, "used": 7, "reset": 1000},
                "graphql": {"limit": 5000, "remaining": 0, "used": 10724, "reset": 1000},
            }
        }
        quotas = module.rate_limit(opener=_opener(_FakeResponse(payload)))
        assert quotas["core"].remaining == 4993
        assert quotas["graphql"].used == 10724
        assert "5000" in quotas["core"].describe()

    def test_summarize_checks_counts_and_names_red(self, module: ModuleType) -> None:
        checks = {
            "check_runs": [
                {"name": "a", "status": "completed", "conclusion": "success"},
                {"name": "b", "status": "completed", "conclusion": "failure"},
                {"name": "c", "status": "queued", "conclusion": None},
                {"name": "d", "status": "completed", "conclusion": "skipped"},
            ]
        }
        total, completed, red = module.summarize_checks(checks)
        assert (total, completed) == (4, 3)
        assert red == ["b"]


class TestCli:
    """Коды возврата: 0 — успех, 1 — ошибка, 2 — ждать сброса квоты."""

    def test_rate_command_prints_quota(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = {"resources": {"core": {"limit": 5000, "remaining": 4999, "used": 1, "reset": 0}}}
        monkeypatch.setattr(module, "_default_opener", _opener(_FakeResponse(payload)))
        assert module.main(["rate"]) == module.EXIT_OK
        assert "core: 4999/5000" in capsys.readouterr().out

    def test_exhausted_quota_returns_wait_code(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        error = _http_error(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 60)},
            message="API rate limit exceeded",
        )
        monkeypatch.setattr(module, "_default_opener", _opener(error))
        assert module.main(["pulls"]) == module.EXIT_WAIT
        assert "квота GitHub" in capsys.readouterr().err

    def test_missing_token_is_a_plain_message(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(module.shutil, "which", lambda _name: None)
        assert module.main(["pulls"]) == module.EXIT_FAIL
        assert "GH_TOKEN" in capsys.readouterr().err

    def test_pulls_command_lists_open_pull_requests(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = [
            {
                "number": 1242,
                "title": "REST",
                "head": {"ref": "feat/gh-rest"},
                "base": {"ref": "main"},
                "user": {"login": "ArtVsMark"},
                "draft": False,
                "updated_at": "2026-08-19T10:00:00Z",
            }
        ]
        monkeypatch.setattr(module, "_default_opener", _opener(_FakeResponse(payload)))
        assert module.main(["pulls"]) == module.EXIT_OK
        assert "#1242" in capsys.readouterr().out

    def test_json_output_is_machine_readable(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(module, "_default_opener", _opener(_FakeResponse([])))
        assert module.main(["--json", "pulls"]) == module.EXIT_OK
        assert json.loads(capsys.readouterr().out) == []

    def test_compare_command_blocks_on_stale_branch(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            module,
            "_default_opener",
            _opener(
                _FakeResponse({"head": {"sha": "abc"}, "base": {"ref": "main"}}),
                _FakeResponse({"ahead_by": 1, "behind_by": 4}),
            ),
        )
        assert module.main(["compare", "1242"]) == module.EXIT_FAIL
        assert "отстала" in capsys.readouterr().out

    def test_merge_command_reports_success(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(module, "_default_opener", _opener(_FakeResponse({"merged": True})))
        assert module.main(["merge", "1242"]) == module.EXIT_OK
        assert "смержен" in capsys.readouterr().out

    def test_merge_command_reports_refusal(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            module,
            "_default_opener",
            _opener(_FakeResponse({"merged": False, "message": "Pull Request is not mergeable"})),
        )
        assert module.main(["merge", "1242"]) == module.EXIT_FAIL
        assert "не смержен" in capsys.readouterr().out
