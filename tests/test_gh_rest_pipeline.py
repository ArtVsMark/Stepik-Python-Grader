"""Тесты конвейерных дополнений ``scripts/gh_rest.py`` (issue #1280, #1281).

Здесь проверяется то, что появилось вместе с механизмом запрета GraphQL:
стоп-кран по остатку квоты, единственная оставшаяся GraphQL-операция
(авто-мерж), sub-issues по REST и единый способ передать тело сообщения.

Сеть не нужна: ``urlopen`` подменяется открывателем, отдающим заготовленные
ответы по очереди.
"""

from __future__ import annotations

import email.message
import importlib.util
import io
import json
import pathlib
import sys
import time
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gh_rest.py"


def _load_module() -> ModuleType:
    """Загрузить скрипт как модуль — он не пакет и импортируется по пути."""
    spec = importlib.util.spec_from_file_location("_gh_rest_pipeline", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Свежий модуль с кэшем во временном каталоге и токеном из окружения."""
    monkeypatch.setenv("GH_REST_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GH_REST_QUOTA_FLOOR", raising=False)
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
        """Тело ответа байтами — как у настоящего ``HTTPResponse``."""
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _opener(*results: Any) -> Any:
    """Открыватель, отдающий заготовленные ответы по очереди."""
    queue = list(results)
    captured: list[Any] = []

    def _open(request: Any) -> Any:
        captured.append(request)
        return queue.pop(0) if queue else _FakeResponse({})

    _open.captured = captured  # type: ignore[attr-defined]
    return _open


def _returning(result: Any) -> Any:
    """Подмена, которая всегда отдаёт один и тот же ответ."""

    def _call(*_args: Any, **_kwargs: Any) -> Any:
        return result

    return _call


def _quota_payload(*, graphql_remaining: int = 4900, core_remaining: int = 4900) -> dict[str, Any]:
    """Ответ ``/rate_limit`` с заданными остатками."""
    reset = int(time.time()) + 600
    return {
        "resources": {
            "core": {
                "limit": 5000,
                "remaining": core_remaining,
                "used": 5000 - core_remaining,
                "reset": reset,
            },
            "graphql": {
                "limit": 5000,
                "remaining": graphql_remaining,
                "used": 5000 - graphql_remaining,
                "reset": reset,
            },
        }
    }


class TestQuotaBrake:
    """Стоп-кран: конвейер встаёт ДО нуля, а не на нуле (issue #1280)."""

    def test_floor_comes_from_environment(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Порог настраивается переменной окружения."""
        monkeypatch.setenv("GH_REST_QUOTA_FLOOR", "1200")

        assert module.quota_floor() == 1200

    def test_garbage_floor_falls_back_to_default(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Опечатка в переменной не должна молча снимать защиту."""
        monkeypatch.setenv("GH_REST_QUOTA_FLOOR", "почти ноль")

        assert module.quota_floor() == module.DEFAULT_QUOTA_FLOOR

    def test_low_remaining_stops_before_the_operation(self, module: ModuleType) -> None:
        """Остаток ниже порога — это «ждать», а не «попробуем и посмотрим»."""
        opener = _opener(_FakeResponse(_quota_payload(graphql_remaining=10)))

        with pytest.raises(module.RateLimited) as failure:
            module.ensure_quota("graphql", opener=opener)

        assert "стоп-кран" in str(failure.value)

    def test_enough_quota_returns_the_remainder(self, module: ModuleType) -> None:
        """Запаса хватает — операция разрешена, остаток возвращён вызывающему."""
        opener = _opener(_FakeResponse(_quota_payload(graphql_remaining=4000)))

        quota = module.ensure_quota("graphql", opener=opener)

        assert quota is not None and quota.remaining == 4000

    def test_unknown_resource_is_not_a_refusal(self, module: ModuleType) -> None:
        """Про ресурс не сказано — это «неизвестно», а не «ноль» (issue #1273)."""
        opener = _opener(_FakeResponse({"resources": {}}))

        assert module.ensure_quota("graphql", opener=opener) is None

    def test_low_remaining_warns_once_per_process(
        self, module: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Предупреждение из заголовков — сигнал, а не шум на каждый запрос."""
        low = {"x-ratelimit-remaining": "5", "x-ratelimit-reset": str(int(time.time()) + 60)}
        opener = _opener(
            _FakeResponse({"ok": 1}, headers=low),
            _FakeResponse({"ok": 2}, headers=low),
        )

        module.request("GET", "repos/x/y/pulls", opener=opener, use_cache=False)
        module.request("GET", "repos/x/y/pulls", opener=opener, use_cache=False)

        assert capsys.readouterr().err.count("квота GitHub") == 1

    def test_rate_command_returns_wait_code_below_floor(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``rate`` годится как гейт: ниже порога — «ждать», а не «успех»."""
        monkeypatch.setattr(
            module,
            "_default_opener",
            _returning(_FakeResponse(_quota_payload(graphql_remaining=100))),
        )

        assert module.main(["rate"]) == module.EXIT_WAIT
        assert "Стоп-кран" in capsys.readouterr().err

    def test_rate_command_is_green_above_floor(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Запаса хватает — обычный успех."""
        monkeypatch.setattr(module, "_default_opener", _returning(_FakeResponse(_quota_payload())))

        assert module.main(["rate"]) == module.EXIT_OK


class TestGraphQLException:
    """Единственная операция без REST-эквивалента — авто-мерж."""

    def test_graphql_errors_are_raised_not_returned(self, module: ModuleType) -> None:
        """``errors`` в теле — это отказ, хотя HTTP-статус 200."""
        opener = _opener(
            _FakeResponse(_quota_payload()),
            _FakeResponse({"errors": [{"message": "Pull request is not mergeable"}]}),
        )

        with pytest.raises(module.GitHubError) as failure:
            module.graphql("mutation {}", opener=opener)

        assert "not mergeable" in str(failure.value)

    def test_rate_limited_type_is_a_wait(self, module: ModuleType) -> None:
        """GraphQL сообщает о лимите телом ответа — это «ждать», а не «упало»."""
        opener = _opener(
            _FakeResponse(_quota_payload()),
            _FakeResponse({"errors": [{"type": "RATE_LIMITED", "message": "limit"}]}),
        )

        with pytest.raises(module.RateLimited):
            module.graphql("mutation {}", opener=opener)

    def test_auto_merge_sends_node_id_and_method(self, module: ModuleType) -> None:
        """Мутация уходит с ``node_id`` из REST и методом в верхнем регистре."""
        opener = _opener(
            _FakeResponse({"node_id": "PR_node", "number": 1280}),
            _FakeResponse(_quota_payload()),
            _FakeResponse(
                {
                    "data": {
                        "enablePullRequestAutoMerge": {
                            "pullRequest": {
                                "number": 1280,
                                "autoMergeRequest": {"mergeMethod": "SQUASH"},
                            }
                        }
                    }
                }
            ),
        )

        result = module.enable_auto_merge("o/r", 1280, opener=opener, use_cache=False)

        assert result["number"] == 1280
        sent = json.loads(opener.captured[-1].data.decode("utf-8"))
        assert sent["variables"] == {"pullRequestId": "PR_node", "mergeMethod": "SQUASH"}
        assert opener.captured[-1].full_url.endswith("/graphql")

    def test_auto_merge_without_node_id_is_an_error(self, module: ModuleType) -> None:
        """Нет ``node_id`` — мутации нечем отвечать, и молчать об этом нельзя."""
        opener = _opener(_FakeResponse({"number": 1280}))

        with pytest.raises(module.GitHubError):
            module.enable_auto_merge("o/r", 1280, opener=opener, use_cache=False)


class TestSubIssues:
    """Sub-issues выбыли из исключений: у них появился REST."""

    def test_children_are_read_over_rest(self, module: ModuleType) -> None:
        """Список дочерних issue — обычный GET, без единого points GraphQL."""
        opener = _opener(_FakeResponse([{"number": 996, "title": "Ядро", "state": "open"}]))

        children = module.sub_issues("o/r", 915, opener=opener, use_cache=False)

        assert [item["number"] for item in children] == [996]
        assert "issues/915/sub_issues" in opener.captured[0].full_url

    def test_child_number_is_translated_into_id(self, module: ModuleType) -> None:
        """Человек называет номер, GitHub ждёт ``id`` — перевод делает модуль."""
        opener = _opener(
            _FakeResponse({"number": 996, "id": 424242}),
            _FakeResponse({"number": 915}),
        )

        module.add_sub_issue("o/r", 915, 996, opener=opener, use_cache=False)

        sent = json.loads(opener.captured[-1].data.decode("utf-8"))
        assert sent == {"sub_issue_id": 424242}


class TestBodySources:
    """Одно понятие — один способ его передать (issue #1281)."""

    def test_comment_accepts_body_flag(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--body`` работает там же, где раньше был только позиционный текст."""
        monkeypatch.setattr(module, "_default_opener", _returning(_FakeResponse({"html_url": "u"})))

        assert module.main(["comment", "1280", "--body", "текст"]) == module.EXIT_OK

    def test_positional_text_still_works(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Старый вызов не сломан: он задокументирован и им пользуются."""
        monkeypatch.setattr(module, "_default_opener", _returning(_FakeResponse({"html_url": "u"})))

        assert module.main(["comment", "1280", "текст"]) == module.EXIT_OK

    def test_two_sources_are_refused(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Два источника разом — отказ: молчаливый выбор публикует не тот текст."""
        monkeypatch.setattr(module, "_default_opener", _returning(_FakeResponse({})))

        assert module.main(["comment", "1280", "текст", "--body", "другой"]) == module.EXIT_FAIL

    def test_body_file_is_read_as_utf8(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Кириллица из файла доезжает без оглядки на кодовую страницу консоли."""
        note = tmp_path / "body.md"
        note.write_text("## Заголовок\n\nтело на кириллице\n", encoding="utf-8")
        captured: list[Any] = []

        def _open(request: Any) -> Any:
            captured.append(request)
            return _FakeResponse({"html_url": "u"})

        monkeypatch.setattr(module, "_default_opener", _open)

        assert module.main(["comment", "1280", "--body-file", str(note)]) == module.EXIT_OK
        sent = json.loads(captured[-1].data.decode("utf-8"))
        assert "тело на кириллице" in sent["body"]

    def test_body_file_dash_reads_stdin(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``-`` читает стандартный ввод — так тело отдают из скрипта."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("многострочное\nтело\n"))
        captured: list[Any] = []

        def _open(request: Any) -> Any:
            captured.append(request)
            return _FakeResponse({"html_url": "u"})

        monkeypatch.setattr(module, "_default_opener", _open)

        assert module.main(["comment", "1280", "--body-file", "-"]) == module.EXIT_OK
        assert "многострочное" in json.loads(captured[-1].data.decode("utf-8"))["body"]

    def test_missing_body_file_is_a_plain_error(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Файла нет — понятная ошибка, а не трассировка."""
        missing = str(tmp_path / "нет.md")

        assert module.main(["comment", "1280", "--body-file", missing]) == module.EXIT_FAIL

    def test_empty_comment_is_not_posted(self, module: ModuleType) -> None:
        """Пустой комментарий не отправляется вовсе — отправлять нечего."""
        assert module.main(["comment", "1280"]) == module.EXIT_FAIL

    def test_close_issue_comments_before_closing(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Исход называется до закрытия — комментарий уходит первым запросом."""
        captured: list[Any] = []

        def _open(request: Any) -> Any:
            captured.append(request)
            return _FakeResponse({"state_reason": "completed"})

        monkeypatch.setattr(module, "_default_opener", _open)

        assert module.main(["close-issue", "1280", "--comment", "сделано в PR"]) == module.EXIT_OK
        assert captured[0].full_url.endswith("/issues/1280/comments")
        assert captured[1].full_url.endswith("/issues/1280")

    def test_create_pr_labels_the_new_pull(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Метка ставится тем же вызовом, а не вторым заходом."""
        captured: list[Any] = []

        def _open(request: Any) -> Any:
            captured.append(request)
            if request.full_url.endswith("/labels"):
                return _FakeResponse([{"name": "area/ci"}])
            return _FakeResponse({"number": 7, "html_url": "u", "user": {"type": "User"}})

        monkeypatch.setattr(module, "_default_opener", _open)

        code = module.main(["create-pr", "--title", "T", "--head", "b", "--label", "area/ci"])

        assert code == module.EXIT_OK
        assert captured[-1].full_url.endswith("/issues/7/labels")
