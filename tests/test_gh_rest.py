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

import ast
import email.message
import importlib.util
import io
import json
import pathlib
import ssl
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


def _raising(error: BaseException) -> Any:
    """Подмена, которая всегда падает заданной ошибкой (сигнатура ``urlopen``)."""

    def _call(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return _call


def _returning(result: Any) -> Any:
    """Подмена, которая всегда отдаёт один и тот же ответ."""

    def _call(*_args: Any, **_kwargs: Any) -> Any:
        return result

    return _call


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
        assert "Forbidden" in str(caught.value), "причина от сервера важнее нашей догадки"

    def test_403_without_a_message_falls_back_to_the_token_hint(self, module: ModuleType) -> None:
        """Догадка про права токена остаётся — но только когда сказать нечего.

        Прежде она печаталась ВСЕГДА и вытесняла настоящую причину из тела
        ответа: у `403` их много, и права токена — лишь одна из них (#1273).
        """
        error = _http_error(403, headers={"x-ratelimit-remaining": "4000"})

        with pytest.raises(module.GitHubError) as caught:
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)

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


class TestCertificateCheckIsNeverWeakenedSilently:
    """Ослабление проверки — решение окружения, а не модуля (issue #1259).

    Прежняя редакция при отказе TLS **сама** повторяла запрос со снятым
    `VERIFY_X509_STRICT`. Флаг узкий (придирчивость к форме CA, а не доверие),
    и повод был настоящий: Python 3.13 включил его по умолчанию, а у
    перехватывающих корпоративных прокси CA часто без `keyUsage` — на 3.11
    модуль работал, на 3.13 падал при том же бандле и исправной сети.

    Но инвариант проекта говорит иначе: невыполнимая гарантия — громкий отказ,
    а не автоматический обход. Так уже записано про песочницу («недоступный
    backend — `parser.error`, а не молчаливый откат на `LocalRunner`»), и TLS
    ничем не отличается. Поэтому откат остался возможен, но стал явным:
    `GH_REST_RELAXED_CA=1` плюс предупреждение на каждый запрос.

    Настоящая проверка сертификата — против локального HTTPS-сервера в
    `test_gh_rest_tls.py`; здесь проверяются решения модуля.
    """

    def _verification_error(self, text: str) -> urllib.error.URLError:
        reason = ssl.SSLCertVerificationError(text)
        return urllib.error.URLError(reason)

    _STRICT_TEXT = (
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "CA cert does not include key usage extension"
    )

    def test_key_usage_rejection_is_recognised(self, module: ModuleType) -> None:
        assert module._is_strict_ca_rejection(self._verification_error(self._STRICT_TEXT))

    @pytest.mark.parametrize(
        "text",
        [
            "certificate verify failed: self-signed certificate in chain",
            "certificate verify failed: certificate has expired",
            "certificate verify failed: Hostname mismatch",
        ],
    )
    def test_real_distrust_is_not_recognised(self, module: ModuleType, text: str) -> None:
        """Просроченный, самоподписанный, чужой — это отказ по существу.

        Различение осталось нужным и без отката: подсказка у этих отказов
        другая. «Форму CA» лечит только осознанный opt-in, а недоверие —
        штатная `SSL_CERT_FILE`, и путать их значит слать читателя не туда.
        """
        assert not module._is_strict_ca_rejection(self._verification_error(text))

    def test_plain_network_failure_is_not_recognised(self, module: ModuleType) -> None:
        assert not module._is_strict_ca_rejection(urllib.error.URLError(OSError("сеть недоступна")))

    def test_strict_rejection_does_not_retry(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Главное свойство правки: попытка ровно одна, ослабленной второй нет."""
        calls: list[Any] = []

        def _urlopen(request: Any, timeout: float = 0, context: Any = None) -> Any:
            calls.append(context)
            raise self._verification_error(self._STRICT_TEXT)

        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)
        monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)

        with pytest.raises(module.TlsVerificationError):
            module._default_opener(urllib.request.Request("https://api.github.com/x"))
        assert len(calls) == 1
        assert calls[0].verify_flags == ssl.create_default_context().verify_flags

    def test_strict_rejection_explains_the_real_cause(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Подсказка про `SSL_CERT_FILE` здесь была бы ложным следом.

        Бандл уже тот, что нужно, — не проходит его оформление. Замер в
        облачной сессии: на 3.11 и 3.13 один и тот же `SSL_CERT_FILE`, один
        набор из 152 CA, и разница ровно в значении `VERIFY_X509_STRICT`.
        """
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)
        monkeypatch.setattr(
            module.urllib.request,
            "urlopen",
            _raising(self._verification_error(self._STRICT_TEXT)),
        )

        with pytest.raises(module.TlsVerificationError) as caught:
            module._default_opener(urllib.request.Request("https://api.github.com/x"))

        text = str(caught.value)
        assert "VERIFY_X509_STRICT" in text
        assert module.ENV_RELAXED_CA in text
        assert "SECURITY.md" in text

    def test_ordinary_distrust_points_at_ssl_cert_file(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """А вот здесь переменная и есть лечение — штатное, без ослаблений."""
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)
        monkeypatch.setattr(
            module.urllib.request,
            "urlopen",
            _raising(self._verification_error("verify failed: self-signed certificate")),
        )

        with pytest.raises(module.TlsVerificationError) as caught:
            module._default_opener(urllib.request.Request("https://api.github.com/x"))

        assert "SSL_CERT_FILE" in str(caught.value)

    def test_network_failure_is_not_dressed_as_tls(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Оборванная сеть — не сбой проверки: подсказка про CA увела бы вбок."""
        monkeypatch.setattr(
            module.urllib.request, "urlopen", _raising(urllib.error.URLError(OSError("нет сети")))
        )

        with pytest.raises(urllib.error.URLError) as caught:
            module._default_opener(urllib.request.Request("https://api.github.com/x"))
        assert not isinstance(caught.value, module.TlsVerificationError)

    @pytest.mark.parametrize("value", ["1"])
    def test_opt_in_uses_the_relaxed_context(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        calls: list[Any] = []
        answer = _FakeResponse({"ok": True})

        def _urlopen(request: Any, timeout: float = 0, context: Any = None) -> Any:
            calls.append(context)
            return answer

        monkeypatch.setenv(module.ENV_RELAXED_CA, value)
        monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)

        assert module._default_opener(urllib.request.Request("https://api.github.com/x")) is answer
        assert not calls[0].verify_flags & ssl.VERIFY_X509_STRICT

    @pytest.mark.parametrize("junk", ["", " ", "0", "true", "TRUE", "yes", "on", "да", "11"])
    def test_only_the_documented_value_switches_it(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch, junk: str
    ) -> None:
        """Опечатка в переключателе безопасности означает «выключено».

        Догадываться о намерении тут нельзя: цена ошибочного «включено» —
        принятый недоверенный сертификат, цена ошибочного «выключено» — ещё
        один запуск с правильным значением, которое ошибка и называет.
        """
        monkeypatch.setenv(module.ENV_RELAXED_CA, junk)

        assert not module.relaxed_ca_enabled()

    def test_relaxed_context_keeps_verification_on(self, module: ModuleType) -> None:
        """Снят ровно один флаг — иначе это было бы отключением проверки."""
        context = module._relaxed_context()

        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT

    def test_default_context_is_the_system_one(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Модуль не подменяет доверенный набор — ни `certifi`, ни `cafile`."""
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)
        context = module._request_context()

        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert bool(context.verify_flags & ssl.VERIFY_X509_STRICT) == bool(
            ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT
        )

    def test_module_does_not_pin_its_own_trust_store(self) -> None:
        """Условие приёмки #1259 — по коду, а не по тексту вокруг него.

        Разбираем `ast`, а не ищем подстроку: слова `certifi` и `cafile` есть в
        докстрингах именно потому, что там объясняется, почему их тут нет.
        """
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        keywords = {kw.arg for node in calls for kw in node.keywords}
        called = {node.func.attr for node in calls if isinstance(node.func, ast.Attribute)}

        assert "certifi" not in imported, "доверенный набор снова свой, а не системный"
        assert not {"cafile", "capath", "cadata"} & keywords
        assert "load_verify_locations" not in called

    def test_opt_in_warns_on_every_request(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Раз за процесс — мало: ослабленный запрос не должен выглядеть обычным."""
        monkeypatch.setenv(module.ENV_RELAXED_CA, "1")
        monkeypatch.setattr(module.urllib.request, "urlopen", _returning(_FakeResponse({})))

        for _ in range(3):
            module._default_opener(urllib.request.Request("https://api.github.com/x"))

        assert capsys.readouterr().err.count(module.ENV_RELAXED_CA) == 3

    def test_cli_prints_the_tls_hint_without_the_github_prefix(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Провод до CLI: до GitHub запрос не дошёл, префикс про него — ложь."""
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)
        monkeypatch.setattr(
            module, "_default_opener", _raising(module.TlsVerificationError("TLS: подробности"))
        )

        assert module.main(["pulls"]) == module.EXIT_FAIL
        err = capsys.readouterr().err
        assert err.startswith("TLS: подробности")
        assert "Ошибка GitHub" not in err


class TestIssueOperations:
    """Работа с issue тоже есть в REST — и до этого гоняла окно на GraphQL.

    Закрыть issue, оставить комментарий, посмотреть состояние — рутина ничуть
    не реже мержа PR, и каждая такая операция через MCP стоила ~300 points из
    5000. Здесь она стоит один запрос из пятнадцати тысяч.
    """

    def test_close_sends_state_and_reason(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"state": "closed", "state_reason": "completed"}))

        module.close_issue("x/y", 42, opener=opener, use_cache=False)

        request = opener.captured[0]
        assert request.get_method() == "PATCH"
        assert json.loads(request.data) == {"state": "closed", "state_reason": "completed"}

    def test_reason_is_carried_through(self, module: ModuleType) -> None:
        """«Сделано» и «не будем делать» — разные исходы, трекер их различает."""
        opener = _opener(_FakeResponse({"state": "closed"}))

        module.close_issue("x/y", 42, reason="not_planned", opener=opener, use_cache=False)

        assert json.loads(opener.captured[0].data)["state_reason"] == "not_planned"

    def test_unknown_reason_is_refused_before_the_request(self, module: ModuleType) -> None:
        """Отказ здесь дешевле, чем `422` после отправки — и понятнее."""
        opener = _opener(_FakeResponse({}))

        with pytest.raises(ValueError, match="причина закрытия"):
            module.close_issue("x/y", 42, reason="потому что", opener=opener, use_cache=False)
        assert opener.captured == [], "запрос ушёл, хотя причина заведомо неверна"

    def test_comment_posts_the_body(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"html_url": "https://github.com/x/y/issues/42#c1"}))

        module.comment_issue("x/y", 42, "текст", opener=opener, use_cache=False)

        request = opener.captured[0]
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/issues/42/comments")
        assert json.loads(request.data) == {"body": "текст"}

    def test_issue_read_is_a_plain_get(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"number": 42, "state": "open", "title": "тема"}))

        found = module.issue("x/y", 42, opener=opener, use_cache=False)

        assert opener.captured[0].get_method() == "GET"
        assert found["title"] == "тема"

    def test_rate_limit_is_recognised_here_too(self, module: ModuleType) -> None:
        """Квота одна на всё: закрытие issue обязано давать «ждать», а не «упало»."""
        error = _http_error(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})

        with pytest.raises(module.RateLimited):
            module.close_issue("x/y", 42, opener=_opener(error), use_cache=False)


class TestIssueEditing:
    """Завести, поправить, разметить — остальная рутина трекера (issue #1255)."""

    def test_create_sends_title_body_and_labels(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"number": 7}))

        module.create_issue(
            "x/y", title="тема", body="текст", labels=["bug"], opener=opener, use_cache=False
        )

        sent = json.loads(opener.captured[0].data)
        assert sent == {"title": "тема", "body": "текст", "labels": ["bug"]}

    def test_create_without_labels_omits_the_key(self, module: ModuleType) -> None:
        """Пустой список меток и отсутствие меток — для GitHub разные вещи."""
        opener = _opener(_FakeResponse({"number": 7}))

        module.create_issue("x/y", title="тема", opener=opener, use_cache=False)

        assert "labels" not in json.loads(opener.captured[0].data)

    def test_update_sends_only_given_fields(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({"number": 7}))

        module.update_issue("x/y", 7, body="новое тело", opener=opener, use_cache=False)

        assert json.loads(opener.captured[0].data) == {"body": "новое тело"}

    def test_empty_update_is_refused_before_the_request(self, module: ModuleType) -> None:
        """Пустой `PATCH` потратил бы запрос и ничего не изменил."""
        opener = _opener(_FakeResponse({}))

        with pytest.raises(ValueError, match="нечего обновлять"):
            module.update_issue("x/y", 7, opener=opener, use_cache=False)
        assert opener.captured == []

    def test_add_labels_returns_the_resulting_set(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse([{"name": "bug"}, {"name": "area/ci"}]))

        assert module.add_labels("x/y", 7, ["area/ci"], opener=opener, use_cache=False) == [
            "bug",
            "area/ci",
        ]

    def test_removing_an_absent_label_is_not_an_error(self, module: ModuleType) -> None:
        """«Уже снята» — не сбой: иначе уборка меток падала бы на повторе."""
        opener = _opener(_http_error(404, message="Label does not exist"))

        assert module.remove_label("x/y", 7, "нет-такой", opener=opener, use_cache=False) is False

    def test_label_name_is_escaped_in_the_path(self, module: ModuleType) -> None:
        """`good first issue` содержит пробелы — без экранирования URL сломан."""
        opener = _opener(_FakeResponse([]))

        module.remove_label("x/y", 7, "good first issue", opener=opener, use_cache=False)

        assert "good%20first%20issue" in opener.captured[0].full_url


class TestCiRuns:
    """Прогоны CI: зависший держит очередь мержей, и увидеть его надо отсюда."""

    def test_branch_runs_filter_on_the_server(self, module: ModuleType) -> None:
        """Фильтрует GitHub — иначе пришлось бы тянуть все прогоны репозитория."""
        opener = _opener(_FakeResponse({"workflow_runs": [{"id": 1}]}))

        module.branch_runs("x/y", branch="main", event="push", opener=opener, use_cache=False)

        url = opener.captured[0].full_url
        assert "branch=main" in url and "event=push" in url

    def test_run_jobs_are_listed(self, module: ModuleType) -> None:
        opener = _opener(
            _FakeResponse({"jobs": [{"id": 5, "name": "test", "conclusion": "failure"}]})
        )

        jobs = module.run_jobs("x/y", 99, opener=opener, use_cache=False)

        assert [job["name"] for job in jobs] == ["test"]

    def test_cancel_reports_success(self, module: ModuleType) -> None:
        opener = _opener(_FakeResponse({}))

        assert module.cancel_run("x/y", 99, opener=opener, use_cache=False) is True
        assert opener.captured[0].get_method() == "POST"

    def test_cancelling_a_finished_run_is_not_an_error(self, module: ModuleType) -> None:
        """`409` здесь означает «отменять нечего», а не поломку."""
        opener = _opener(_http_error(409, message="Cannot cancel a completed workflow run"))

        assert module.cancel_run("x/y", 99, opener=opener, use_cache=False) is False

    def test_other_failures_still_raise(self, module: ModuleType) -> None:
        """Иначе «нет прав отменять» читалось бы как «уже завершён»."""
        opener = _opener(_http_error(403, message="Resource not accessible"))

        with pytest.raises(module.GitHubError):
            module.cancel_run("x/y", 99, opener=opener, use_cache=False)


class TestCreatePullWarnsAboutAuthorship:
    """Токен окружения принадлежит `claude[bot]` — и это меняет судьбу PR."""

    def test_docstring_names_the_consequence(self, module: ModuleType) -> None:
        """Ловушка тихая: PR создаётся, а обязательная проверка краснеет.

        Поймано на живом PR — «Workflow initiated by non-human actor: claude
        (type: Bot)». Тот же набор изменений, созданный от человека, ревью
        проходит.
        """
        doc = module.create_pull.__doc__ or ""

        assert "claude[bot]" in doc
        assert "MCP" in doc


class TestCreatePrTellsTheTruthAboutAuthorship:
    """Бот-авторство ломает ревью, и узнать об этом надо сразу, а не из CI.

    Заранее не проверить: в облачной сессии токены проксированы — `GET /user`
    отвечает человеком, а запись атрибутируется приложению. Поэтому смотрим на
    автора в ответе на создание; лишнего запроса это не стоит.
    """

    def _args(self, module: ModuleType, **over: Any) -> Any:
        import argparse

        base = {
            "repo": "x/y",
            "title": "тема",
            "head": "ветка",
            "base": "main",
            "body": "",
            "draft": False,
            "json": False,
        }
        base.update(over)
        return argparse.Namespace(**base)

    def test_human_author_is_success(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            module,
            "create_pull",
            lambda *a, **k: {"number": 7, "user": {"login": "ArtVsMark", "type": "User"}},
        )

        assert module._cmd_create_pr(self._args(module)) == module.EXIT_OK

    def test_bot_author_is_a_failure_with_the_remedy(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            module,
            "create_pull",
            lambda *a, **k: {"number": 7, "user": {"login": "claude[bot]", "type": "Bot"}},
        )

        code = module._cmd_create_pr(self._args(module))

        assert code == module.EXIT_FAIL
        err = capsys.readouterr().err
        assert "claude[bot]" in err
        assert "MCP" in err, "сказано «плохо», но не сказано как правильно"


class TestPolicyRefusalIsNotAQuota:
    """`403` без заголовков лимита — не исчерпанная квота (issue #1273).

    Прежде отсутствующий `x-ratelimit-remaining` читался как ноль, и **любой**
    `403` объявлялся исчерпанной квотой. Поймано на живом отказе: попытка
    смержить PR из агентской сессии вернула

        {"message": "Merging into a protected base branch is not permitted
                     for this session type."}

    а модуль напечатал «квота GitHub (core) исчерпана — сброс в ?, через 0 мин
    0 с», хотя `rate` в ту же секунду показывал 14997/15000.

    Разница дорогая: отказ по политике и кончившаяся квота требуют
    противоположных действий — «делай иначе» против «жди и не трогай». Следы
    вранья были на виду («сброс в ?»), но совет звучал уверенно.
    """

    _POLICY = "Merging into a protected base branch is not permitted for this session type."

    def test_missing_headers_are_not_zero(self, module: ModuleType) -> None:
        """«Неизвестно» и «ноль» — разные ответы, и различать их обязан парсер."""
        remaining, _reset, _resource = module._quota_from_headers(_headers({}))

        assert remaining is None

    def test_present_zero_is_still_zero(self, module: ModuleType) -> None:
        remaining, _reset, _resource = module._quota_from_headers(
            _headers({"x-ratelimit-remaining": "0"})
        )

        assert remaining == 0

    def test_policy_refusal_is_a_plain_error(self, module: ModuleType) -> None:
        error = _http_error(403, message=self._POLICY)

        with pytest.raises(module.GitHubError) as caught:
            module.request("PUT", "repos/x/y/pulls/1/merge", opener=_opener(error), use_cache=False)

        assert not isinstance(caught.value, module.RateLimited), "отказ выдан за исчерпанную квоту"

    def test_the_real_reason_reaches_the_message(self, module: ModuleType) -> None:
        """Ради этого всё: строка из тела — единственное, что объясняет отказ."""
        error = _http_error(403, message=self._POLICY)

        with pytest.raises(module.GitHubError) as caught:
            module.request("PUT", "repos/x/y/pulls/1/merge", opener=_opener(error), use_cache=False)

        assert "protected base branch" in str(caught.value)

    def test_exhausted_quota_still_recognised(self, module: ModuleType) -> None:
        """Контроль: настоящая квота по-прежнему распознаётся как «ждать»."""
        error = _http_error(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 60)},
            message="API rate limit exceeded",
        )

        with pytest.raises(module.RateLimited):
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)

    def test_secondary_limit_without_remaining_is_still_a_wait(self, module: ModuleType) -> None:
        """`retry-after` — тоже слово сервера про лимит, и оно остаётся в силе."""
        error = _http_error(429, headers={"retry-after": "30"}, message="secondary rate limit")

        with pytest.raises(module.RateLimited):
            module.request("GET", "repos/x/y/pulls", opener=_opener(error), use_cache=False)

    def test_cli_reports_failure_not_waiting(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Провод до кода возврата: «нельзя» — это 1, а не 2 («подожди»)."""
        monkeypatch.setattr(
            module, "_default_opener", _raising(_http_error(403, message=self._POLICY))
        )

        assert module.main(["merge", "1266"]) == module.EXIT_FAIL
        assert "protected base branch" in capsys.readouterr().err


class TestQueueOrder:
    """Порядок очереди — чистая функция, поэтому проверяется без сети (issue #1282)."""

    def test_overlapping_pulls_go_first(self, module: ModuleType) -> None:
        """Пересекающиеся по файлам мержатся раньше — их конфликт вскроется всё равно."""
        entries = [
            module.QueueEntry(10, "один", True, files=("a.py",)),
            module.QueueEntry(11, "два", True, files=("b.py",)),
            module.QueueEntry(12, "три", True, files=("a.py",)),
        ]

        order = [entry.number for entry in module.queue_order(entries)]

        assert order == [10, 12, 11]

    def test_overlaps_are_named_both_ways(self, module: ModuleType) -> None:
        """Пересечение видно с обеих сторон — иначе второй PR не узнает о первом."""
        entries = [
            module.QueueEntry(10, "один", True, files=("shared.py", "a.py")),
            module.QueueEntry(11, "два", True, files=("shared.py",)),
        ]

        marked = {entry.number: entry.overlaps for entry in module.queue_order(entries)}

        assert marked == {10: (11,), 11: (10,)}

    def test_ties_break_by_number(self, module: ModuleType) -> None:
        """Без пересечений порядок стабилен: кто раньше пришёл, тот раньше и мержится."""
        entries = [
            module.QueueEntry(30, "поздний", True, files=("c.py",)),
            module.QueueEntry(20, "ранний", True, files=("d.py",)),
        ]

        assert [entry.number for entry in module.queue_order(entries)] == [20, 30]

    def test_empty_queue_is_not_an_error(self, module: ModuleType) -> None:
        assert module.queue_order([]) == []

    def test_priority_outranks_overlap_and_number(self, module: ModuleType) -> None:
        """issue #1326: приоритет — первый ключ, иначе срочное стоит наравне с косметикой."""
        entries = [
            module.QueueEntry(10, "пересекается", True, files=("a.py",)),
            module.QueueEntry(11, "тоже", True, files=("a.py",)),
            module.QueueEntry(12, "срочный", True, files=("b.py",), priority=0),
        ]

        order = [entry.number for entry in module.queue_order(entries)]

        assert order == [12, 10, 11], "PR с приоритетом идёт первым, даже без пересечений"

    def test_priority_scale_order(self, module: ModuleType) -> None:
        """Шкала исполняется целиком, а не только на крайних значениях."""
        entries = [
            module.QueueEntry(number, "x", True, priority=module.priority_rank([label])[0])
            for number, label in enumerate(module.PRIORITY_LABELS, start=1)
        ]

        assert [entry.number for entry in module.queue_order(entries)] == list(
            range(1, len(module.PRIORITY_LABELS) + 1)
        )


class TestPriorityLabels:
    """Приоритет читается с меток и наследуется от задачи (issue #1326)."""

    def test_blocker_is_the_highest(self, module: ModuleType) -> None:
        rank, why = module.priority_rank(["blocker", "P3"])

        assert rank == 0
        assert "blocker" in why

    def test_unlabelled_goes_by_readiness(self, module: ModuleType) -> None:
        rank, why = module.priority_rank(["area/cli", "bug"])

        assert rank == len(module.PRIORITY_LABELS)
        assert why == "по готовности"

    def test_case_does_not_matter(self, module: ModuleType) -> None:
        """`p0` и `P0` — одна и та же метка: регистр не должен решать порядок."""
        assert module.priority_rank(["p0"])[0] == module.priority_rank(["P0"])[0]

    def test_closes_is_parsed_in_every_form(self, module: ModuleType) -> None:
        """GitHub понимает несколько глаголов — приоритет наследуется по тем же."""
        body = "Closes #12\nfixes #34, resolved #56\nсм. также #78"

        assert module.closes_issues(body) == [12, 34, 56]

    def test_body_without_closes_yields_nothing(self, module: ModuleType) -> None:
        assert module.closes_issues("Просто описание без связи") == []


class TestQueueReport:
    """Отчёт отвечает на три вопроса: кто голова, кто где стоит, кто перед кем."""

    def _report(self, module: ModuleType) -> Any:
        return module.QueueReport(
            ready=(
                module.QueueEntry(10, "первый", True),
                module.QueueEntry(11, "второй", True),
                module.QueueEntry(12, "третий", True),
            ),
            waiting=(),
            main_busy=False,
            main_red=False,
        )

    def test_head_is_the_only_one_to_update(self, module: ModuleType) -> None:
        assert self._report(module).head.number == 10

    def test_position_counts_from_one(self, module: ModuleType) -> None:
        report = self._report(module)

        assert report.position(10) == 1
        assert report.position(12) == 3
        assert report.position(99) == 0, "чужой номер — не место в очереди, а его отсутствие"

    def test_ahead_lists_everyone_in_front(self, module: ModuleType) -> None:
        report = self._report(module)

        assert report.ahead_of(12) == [10, 11]
        assert report.ahead_of(10) == []
        assert report.ahead_of(99) == []

    def test_empty_queue_has_no_head(self, module: ModuleType) -> None:
        empty = module.QueueReport(ready=(), waiting=(), main_busy=False, main_red=False)

        assert empty.head is None


class TestMergeQueueFromApi:
    """Сборка очереди из ответов API — сеть подменена, проверяется разбор."""

    def _pull(self, number: int, sha: str, *, draft: bool = False) -> dict[str, Any]:
        return {
            "number": number,
            "title": f"PR {number}",
            "head": {"ref": f"branch-{number}", "sha": sha},
            "base": {"ref": "main"},
            "user": {"login": "someone"},
            "draft": draft,
            "updated_at": "2026-08-20T00:00:00Z",
        }

    def _checks(self, *runs: tuple[str, str, str]) -> dict[str, Any]:
        return {
            "check_runs": [
                {"name": name, "status": status, "conclusion": conclusion}
                for name, status, conclusion in runs
            ]
        }

    def test_only_green_pulls_enter_the_queue(self, module: ModuleType) -> None:
        """Готов — значит все проверки завершены и зелёные; остальные ждут отдельно."""
        opener = _opener(
            _FakeResponse([self._pull(10, "aaa"), self._pull(11, "bbb")]),
            _FakeResponse(self._checks(("test", "completed", "success"))),
            _FakeResponse([{"filename": "one.py"}]),
            _FakeResponse(self._checks(("test", "in_progress", None))),
            _FakeResponse({"workflow_runs": [{"status": "completed", "conclusion": "success"}]}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert [entry.number for entry in report.ready] == [10]
        assert [entry.number for entry in report.waiting] == [11]
        assert "проверки идут" in report.waiting[0].reason

    def test_red_pull_is_named_by_its_red_check(self, module: ModuleType) -> None:
        opener = _opener(
            _FakeResponse([self._pull(10, "aaa")]),
            _FakeResponse(self._checks(("static", "completed", "failure"))),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert report.ready == ()
        assert report.waiting[0].reason == "красные: static"

    def test_missing_checks_are_not_green(self, module: ModuleType) -> None:
        """Пустой список проверок — «CI не стартовал», а не «зелено» (issue #1105)."""
        opener = _opener(
            _FakeResponse([self._pull(10, "aaa")]),
            _FakeResponse({"check_runs": []}),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert report.ready == ()
        assert "не стартовал" in report.waiting[0].reason

    def test_draft_is_not_in_the_queue(self, module: ModuleType) -> None:
        """Черновик не мержится, значит и очередь им не занимает."""
        opener = _opener(
            _FakeResponse([self._pull(10, "aaa", draft=True)]),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert report.ready == ()
        assert report.waiting == ()

    def test_main_state_is_reported(self, module: ModuleType) -> None:
        """Красная и занятая main — отдельные факты отчёта, а не молчание."""
        opener = _opener(
            _FakeResponse([]),
            _FakeResponse(
                {
                    "workflow_runs": [
                        {"status": "completed", "conclusion": "failure"},
                        {"status": "completed", "conclusion": "success"},
                    ]
                }
            ),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert report.main_red is True
        assert report.main_busy is False


class TestCancelledPredecessor:
    """Отменённый предшественник не делает PR красным (issue #1115, тот же класс).

    Снятие черновика и повторный пуш создают вторую запись с тем же именем на
    том же коммите, а concurrency-группа гасит первую. Обе лежат в ответе рядом,
    и без отбора по свежести зелёный PR выпадал бы из очереди мержа навсегда —
    гейт врал бы ровно там, где должен помогать.
    """

    def _pair(self, conclusions: tuple[str, str]) -> dict[str, Any]:
        old_conclusion, new_conclusion = conclusions
        return {
            "check_runs": [
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": old_conclusion,
                    "started_at": "2026-08-20T09:00:00Z",
                    "id": 1,
                },
                {
                    "name": "test",
                    "status": "completed",
                    "conclusion": new_conclusion,
                    "started_at": "2026-08-20T09:40:00Z",
                    "id": 2,
                },
            ]
        }

    def test_cancelled_run_is_replaced_by_the_fresh_green(self, module: ModuleType) -> None:
        total, completed, red = module.summarize_checks(self._pair(("cancelled", "success")))

        assert (total, completed, red) == (1, 1, []), "судим по свежей записи, а не по обеим"

    def test_fresh_red_still_counts(self, module: ModuleType) -> None:
        """Обратная сторона: свежий провал не прячется за старым успехом."""
        total, completed, red = module.summarize_checks(self._pair(("success", "failure")))

        assert (total, completed, red) == (1, 1, ["test"])

    def test_queue_keeps_a_pull_whose_predecessor_was_cancelled(self, module: ModuleType) -> None:
        """И очередь мержа его не теряет — ради этого отбор и нужен."""
        opener = _opener(
            _FakeResponse(
                [
                    {
                        "number": 10,
                        "title": "PR 10",
                        "head": {"ref": "branch", "sha": "aaa"},
                        "base": {"ref": "main"},
                        "user": {"login": "someone"},
                        "draft": False,
                        "updated_at": "2026-08-20T09:40:00Z",
                    }
                ]
            ),
            _FakeResponse(self._pair(("cancelled", "success"))),
            _FakeResponse([{"filename": "one.py"}]),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert [entry.number for entry in report.ready] == [10]
        assert report.waiting == ()


class TestForkInTheQueue:
    """PR из форка стоит в очереди как все, но его ветку нам не обновить (#1287)."""

    def _pull(self, number: int, repo: str) -> dict[str, Any]:
        return {
            "number": number,
            "title": f"PR {number}",
            "head": {"ref": "branch", "sha": f"sha{number}", "repo": {"full_name": repo}},
            "base": {"ref": "main"},
            "user": {"login": "someone"},
            "draft": False,
            "updated_at": "2026-08-20T00:00:00Z",
        }

    def _green(self) -> dict[str, Any]:
        return {"check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]}

    def test_fork_is_marked_but_stays_in_the_queue(self, module: ModuleType) -> None:
        """Место в очереди обычное — иначе форковый PR не смержится никогда."""
        opener = _opener(
            _FakeResponse([self._pull(10, "someone/fork")]),
            _FakeResponse(self._green()),
            _FakeResponse([{"filename": "one.py"}]),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert [entry.number for entry in report.ready] == [10]
        assert report.ready[0].fork is True

    def test_own_branch_is_not_a_fork(self, module: ModuleType) -> None:
        opener = _opener(
            _FakeResponse([self._pull(10, "owner/repo")]),
            _FakeResponse(self._green()),
            _FakeResponse([{"filename": "one.py"}]),
            _FakeResponse({"workflow_runs": []}),
        )

        report = module.merge_queue("owner/repo", opener=opener)

        assert report.ready[0].fork is False


class TestRerunFailedJobs:
    """Перезапуск только упавших — и обязательный след (issue #1344)."""

    def test_only_failed_jobs_are_rerun(self, module: ModuleType) -> None:
        """Полный перезапуск ради одной ячейки стоит десятков минут очереди."""
        opener = _opener(_FakeResponse({}))

        assert module.rerun_failed_jobs("x/y", 42, opener=opener, use_cache=False) is True

        request = opener.captured[0]
        assert request.full_url.endswith("/actions/runs/42/rerun-failed-jobs"), request.full_url
        assert request.get_method() == "POST"

    def test_nothing_to_rerun_is_not_a_failure(self, module: ModuleType) -> None:
        """Прогон без упавших джобов — «нечего», а не сбой транспорта."""
        opener = _opener(_http_error(403, message="no failed jobs"))

        assert module.rerun_failed_jobs("x/y", 42, opener=opener, use_cache=False) is False

    def test_missing_write_rights_are_not_nothing_to_rerun(self, module: ModuleType) -> None:
        """403 «прав нет» ≠ 403 «нечего»: иначе команда врёт о состоянии.

        Воспроизведено на живом прогоне: у облачной сессии закрыта запись в
        Actions, GitHub ответил «Resource not accessible by integration», а
        команда напечатала «упавших джобов нет» — на прогоне, где джоб только
        что упал. Окно ушло бы чинить не то.
        """
        opener = _opener(*[_http_error(403, message="Resource not accessible by integration")] * 6)

        with pytest.raises(module.GitHubError) as exc:
            module.rerun_failed_jobs("x/y", 42, opener=opener, use_cache=False)

        text = str(exc.value)
        assert "прав" in text, text
        assert "кнопкой" in text, "сообщение обязано называть выход, а не только отказ"

    def test_other_errors_still_raise(self, module: ModuleType) -> None:
        """Молчать о настоящем отказе нельзя — иначе «перезапустил» будет ложью."""
        opener = _opener(*[_http_error(500, message="boom")] * 6)

        with pytest.raises(module.GitHubError):
            module.rerun_failed_jobs("x/y", 42, opener=opener, use_cache=False)

    def test_flake_note_creates_the_log_with_its_header(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Журнал заводится сам: пустой файл никто не создаст вручную вовремя."""
        log = tmp_path / "docs" / "agent" / "flaky-runs.md"

        module.append_flake_note(77, "SQLite lock на windows", log=log)

        written = log.read_text(encoding="utf-8")
        assert "Журнал нестабильности" in written
        assert "| 77 | SQLite lock на windows |" in written

    def test_flake_notes_accumulate(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Повторяющийся сюжет виден только на нескольких записях."""
        log = tmp_path / "flaky.md"

        module.append_flake_note(1, "первое", log=log)
        module.append_flake_note(2, "второе", log=log)

        written = log.read_text(encoding="utf-8")
        assert "| 1 | первое |" in written and "| 2 | второе |" in written
        assert written.count("Журнал нестабильности") == 1, "шапка пишется один раз"


class TestRerunCliRequiresATrace:
    """Без записи о нестабильности перезапуск не выполняется (issue #1344)."""

    def test_why_is_required(self, module: ModuleType) -> None:
        """Приёмка: «удобная кнопка» без учёта невозможна по построению.

        Перезапуск не чинит — он меняет исход, не меняя причины. Через полгода
        без этого «перезапусти, оно иногда падает» стало бы нормальным ответом.
        """
        with pytest.raises(SystemExit) as exc:
            module.main(["rerun-failed", "42"])

        assert exc.value.code != 0, "перезапуск без --why обязан отклоняться"

    def test_third_attempt_is_refused(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Третья попытка означает дефект, а не мигание — и API не трогается."""
        called: list[int] = []
        monkeypatch.setattr(module, "rerun_failed_jobs", lambda *a, **k: called.append(1) or True)

        code = module.main(["rerun-failed", "42", "--why", "снова", "--attempt", "3"])

        assert code != 0
        assert not called, "на третьей попытке запрос отправлять не за чем"
        assert "дефект" in capsys.readouterr().err

    def test_successful_rerun_writes_the_note(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Запись — часть операции, а не отдельная дисциплина."""
        log = tmp_path / "flaky.md"
        monkeypatch.setattr(module, "rerun_failed_jobs", lambda *a, **k: True)
        monkeypatch.setattr(module, "FLAKE_LOG", log)

        code = module.main(["rerun-failed", "42", "--why", "дедлайн запуска процесса"])

        assert code == 0
        assert "| 42 | дедлайн запуска процесса |" in log.read_text(encoding="utf-8")

    def test_nothing_to_rerun_writes_nothing(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Не мигало — не записываем: журнал должен оставаться правдой."""
        log = tmp_path / "flaky.md"
        monkeypatch.setattr(module, "rerun_failed_jobs", lambda *a, **k: False)
        monkeypatch.setattr(module, "FLAKE_LOG", log)

        assert module.main(["rerun-failed", "42", "--why", "показалось"]) == 0
        assert not log.exists(), "запись о перезапуске, которого не было, врала бы о системе"
