"""Tests for scripts/open_agent_prs.py — конвейер открывает PR сам.

Guard-the-guard: сеть не трогается вовсе. Проверяются решения, а не запросы —
кого выбрать, что положить в заголовок и тело, когда промолчать. Именно они и
ломаются молча: второй PR на ту же ветку или трейлер, уехавший в тело.

Скрипт лежит в ``scripts/`` (не на sys.path) — грузим по пути, тем же приёмом,
что ``test_check_docs_guardrails.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "open_agent_prs.py"

_MESSAGE = """docs(contrib): англоязычный вклад доходит до мержа

Барьеры стояли на выходе, а не на входе.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Co-Authored-By: Artem Markitanov <86671904+ArtVsMark@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01
"""


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_open_agent_prs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBranchSelection:
    def test_only_agent_branches_without_a_pull(self) -> None:
        module = _load_module()
        branches = ["main", "agent/pr-opener", "agent/уже-с-pr", "fix/чужая-ветка"]

        chosen = module.branches_without_pull(branches, {"agent/уже-с-pr"})

        assert chosen == ["agent/pr-opener"]

    def test_prefix_is_configurable(self) -> None:
        module = _load_module()

        assert module.branches_without_pull(["bot/one", "agent/two"], set(), "bot/") == ["bot/one"]

    def test_branch_with_an_open_pull_is_skipped(self) -> None:
        """Скрипт зовётся и по пушу, и по расписанию — второй PR гарантирован без этого."""
        module = _load_module()

        assert module.branches_without_pull(["agent/one"], {"agent/one"}) == []


class TestTitleAndBody:
    def test_first_line_is_the_title(self) -> None:
        module = _load_module()

        title, _body = module.title_and_body(_MESSAGE)

        assert title == "docs(contrib): англоязычный вклад доходит до мержа"

    def test_trailers_do_not_reach_the_body(self) -> None:
        """Авторство живёт в коммите; в теле PR оно избыточно и ломает чтение."""
        module = _load_module()

        _title, body = module.title_and_body(_MESSAGE)

        assert "Co-Authored-By" not in body
        assert "Claude-Session" not in body
        assert "Барьеры стояли на выходе" in body

    def test_attribution_is_appended_once(self) -> None:
        module = _load_module()

        _title, body = module.title_and_body(_MESSAGE, "Работа сделана вместе.")

        assert body.count("Работа сделана вместе.") == 1
        assert body.endswith("Работа сделана вместе.")

    def test_empty_message_gives_empty_title(self) -> None:
        module = _load_module()

        assert module.title_and_body("") == ("", "")


class TestAttributionLine:
    def test_read_from_settings(self, tmp_path: Path) -> None:
        module = _load_module()
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"attribution": {"pr": "Работа сделана вместе."}}), encoding="utf-8"
        )

        assert module.attribution_line(settings) == "Работа сделана вместе."

    def test_missing_file_gives_empty_string(self, tmp_path: Path) -> None:
        """Выдуманный текст хуже отсутствующего: источник один — settings.json."""
        module = _load_module()

        assert module.attribution_line(tmp_path / "нет.json") == ""

    def test_broken_json_gives_empty_string(self, tmp_path: Path) -> None:
        module = _load_module()
        settings = tmp_path / "settings.json"
        settings.write_text("{не json", encoding="utf-8")

        assert module.attribution_line(settings) == ""

    def test_real_settings_have_the_line(self) -> None:
        """И живой файл проекта: подпись PR берётся именно оттуда."""
        module = _load_module()

        assert "Claude" in module.attribution_line()


def _compare(ahead: int, *messages: str, merge_last: bool = False):
    """Подделка ответа `compare`: сколько впереди и какие там коммиты."""
    commits = [{"commit": {"message": text}, "parents": [{"sha": "1"}]} for text in messages]
    if merge_last:
        commits.append(
            {
                "commit": {"message": "merge: подтянуть main"},
                "parents": [{"sha": "1"}, {"sha": "2"}],
            }
        )
    return SimpleNamespace(data={"ahead_by": ahead, "commits": commits})


class TestDescribingMessage:
    def test_merge_commit_in_the_tip_does_not_become_the_title(self) -> None:
        """Базу в ветку подтягивают регулярно — merge в вершине случай штатный."""
        module = _load_module()
        commits = _compare(2, _MESSAGE, merge_last=True).data["commits"]

        assert module.describing_message(commits).startswith("docs(contrib):")

    def test_last_meaningful_commit_wins(self) -> None:
        module = _load_module()
        commits = _compare(2, "первый", "второй").data["commits"]

        assert module.describing_message(commits) == "второй"

    def test_no_commits_gives_empty_string(self) -> None:
        module = _load_module()

        assert module.describing_message(None) == ""


class TestOpenForBranch:
    def test_branch_without_commits_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PR из нуля изменений создать можно — и он будет висеть вечно."""
        module = _load_module()
        monkeypatch.setattr(module.gh_rest, "request", lambda *_a, **_k: _compare(0))

        report = module.open_for_branch("agent/пустая")

        assert "нет коммитов" in report

    def test_dry_run_creates_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module()
        monkeypatch.setattr(module.gh_rest, "request", lambda *_a, **_k: _compare(1, _MESSAGE))

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("--dry-run не должен создавать PR")

        monkeypatch.setattr(module.gh_rest, "create_pull", _explode)

        report = module.open_for_branch("agent/ветка", dry_run=True)

        assert "открыл бы PR" in report

    def test_auto_merge_is_enabled_for_the_new_pull(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Без авто-мержа человек снова нужен — уже на кнопке «Merge»."""
        module = _load_module()
        enabled: list[int] = []
        monkeypatch.setattr(module.gh_rest, "request", lambda *_a, **_k: _compare(2, _MESSAGE))
        monkeypatch.setattr(module.gh_rest, "create_pull", lambda *_a, **_k: {"number": 77})
        monkeypatch.setattr(
            module.gh_rest,
            "enable_auto_merge",
            lambda _repo, number, **_k: enabled.append(number) or {},
        )

        report = module.open_for_branch("agent/ветка")

        assert enabled == [77]
        assert "#77" in report

    def test_failed_auto_merge_is_reported_not_hidden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        module = _load_module()
        monkeypatch.setattr(module.gh_rest, "request", lambda *_a, **_k: _compare(1, _MESSAGE))
        monkeypatch.setattr(module.gh_rest, "create_pull", lambda *_a, **_k: {"number": 78})

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise module.gh_rest.GitHubError("auto-merge выключен в настройках")

        monkeypatch.setattr(module.gh_rest, "enable_auto_merge", _boom)

        report = module.open_for_branch("agent/ветка")

        assert "#78 открыт" in report
        assert "авто-мерж не включился" in report


class TestMain:
    def test_nothing_to_do_is_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load_module()
        monkeypatch.setattr(module.gh_rest, "list_pulls", lambda *_a, **_k: [])
        monkeypatch.setattr(module, "branch_names", lambda *_a, **_k: ["main"])

        assert module.main([]) == 0
        assert "без открытого PR нет" in capsys.readouterr().out

    def test_unreachable_github_is_retry_later_not_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пустой ответ читался бы как «делать нечего» — та же ошибка, что у гейтов на пустоте.

        Код именно третий (2), а не тот же, что у находки: «веток без PR нет» и
        «состояние не прочитано» — разные вещи с разными действиями (правило 039).
        """
        module = _load_module()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise module.gh_rest.GitHubError("GitHub отказал")

        monkeypatch.setattr(module.gh_rest, "list_pulls", _boom)

        assert module.main([]) == 2

    def test_exhausted_quota_says_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Квота — «ждать», а не «сломалось»: повтор отодвигает сброс (правило 058)."""
        module = _load_module()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise module.gh_rest.RateLimited("лимит", reset_at=0, resource="core")

        monkeypatch.setattr(module.gh_rest, "list_pulls", _boom)

        assert module.main([]) == module.gh_rest.EXIT_WAIT

    def test_one_failing_branch_does_not_stop_the_rest(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load_module()
        monkeypatch.setattr(module.gh_rest, "list_pulls", lambda *_a, **_k: [])
        monkeypatch.setattr(
            module, "branch_names", lambda *_a, **_k: ["agent/первая", "agent/вторая"]
        )

        def _open(branch: str, *_args: object, **_kwargs: object) -> str:
            if branch == "agent/первая":
                raise module.gh_rest.GitHubError("GitHub отказал")
            return f"{branch}: PR #9 открыт"

        monkeypatch.setattr(module, "open_for_branch", _open)

        assert module.main([]) == 0
        assert "PR #9 открыт" in capsys.readouterr().out
