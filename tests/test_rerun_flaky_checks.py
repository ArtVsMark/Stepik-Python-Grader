"""Тесты scripts/rerun_flaky_checks.py — мигнувшую проверку перезапускает механизм.

Предыстория (issue #1362). Частичный перезапуск был сделан, но позвать его мог
только тот, у кого есть права на запись в Actions, — а у облачной сессии их
нет. Дежурное окно видело мигнувшую проверку и упиралось в клик человека.

Здесь проверяется не «перезапускает», а **где он останавливается**: список
разрешённых закрыт, попытка одна, соседняя красная проверка отменяет всё. Без
этих границ механизм превратился бы в глушилку красноты — то самое, из-за чего
перезапуск и считается опасной автоматикой.

Сеть подделывается на уровне модуля ``gh_rest``: тесты описывают решения
механизма, а не HTTP.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_SCRIPT = _SCRIPTS / "rerun_flaky_checks.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "rerun-flaky-checks.yml"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_rerun_flaky_checks", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rerun() -> ModuleType:
    """Свежий модуль механизма на каждый тест."""
    return _load_module()


def _check(
    name: str,
    conclusion: str | None,
    *,
    suite: int = 1,
    status: str = "completed",
    started: str = "2026-08-22T12:00:00Z",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "check_suite": {"id": suite},
        "started_at": started,
    }


def _run(
    run_id: int,
    *,
    suite: int = 1,
    attempt: int = 1,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "check_suite_id": suite,
        "run_attempt": attempt,
        "status": status,
        "conclusion": "failure",
    }


class _FakeApi:
    """Подделка ``gh_rest`` ровно в тех функциях, которые зовёт механизм."""

    def __init__(
        self,
        pulls: list[Any],
        checks: dict[str, list[dict[str, Any]]],
        runs: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.pulls = pulls
        self.checks = checks
        self.runs = runs
        self.rerun_calls: list[int] = []

    def install(self, module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        gh = module.gh_rest
        monkeypatch.setattr(gh, "list_pulls", lambda *a, **k: self.pulls)
        monkeypatch.setattr(
            gh,
            "pull_checks",
            lambda _repo, sha, **_k: {"check_runs": self.checks.get(sha, [])},
        )
        monkeypatch.setattr(
            gh,
            "workflow_runs",
            lambda _repo, sha, **_k: {"workflow_runs": self.runs.get(sha, [])},
        )
        monkeypatch.setattr(gh, "rerun_failed_jobs", self._rerun)

    def _rerun(self, _repo: str, run_id: int, **_kwargs: Any) -> bool:
        self.rerun_calls.append(run_id)
        return True


def _pull(
    module: ModuleType,
    number: int,
    *,
    sha: str = "sha",
    draft: bool = False,
    fork: bool = False,
    labels: tuple[str, ...] = (),
) -> Any:
    return module.gh_rest.PullSummary(
        number=number,
        title=f"PR {number}",
        branch=f"agent/pr-{number}",
        base="main",
        author="ArtVsMark",
        draft=draft,
        updated_at="2026-08-22T12:00:00Z",
        sha=sha,
        fork=fork,
        labels=labels,
    )


# --- что механизм перезапускает -----------------------------------------------


def test_lone_allowed_failure_is_rerun(rerun: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Единственная красная и разрешённая проверка — ровно тот случай, ради которого всё."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={"sha": [_check("claude-review", "failure"), _check("test", "success", suite=2)]},
        runs={"sha": [_run(4242)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == [4242]
    assert outcome.rerun == [4242]
    assert "4242" in outcome.report


def test_dry_run_touches_nothing(rerun: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run`` показывает решение, не трогая Actions."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={"sha": [_check("claude-review", "failure")]},
        runs={"sha": [_run(4242)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo", dry_run=True)

    assert api.rerun_calls == []
    assert outcome.rerun == [4242]


# --- где он останавливается ---------------------------------------------------


def test_failure_outside_the_list_is_never_rerun(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обязательную проверку механизм не трогает: её чинят, а не перезапускают."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1357)],
        checks={"sha": [_check("test (macos-latest, 3.12)", "failure")]},
        runs={"sha": [_run(4242)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []
    assert "вне списка" in outcome.report


def test_allowed_failure_next_to_a_real_one_is_skipped(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Рядом красна настоящая проверка — перезапуск соседки ничего не изменит."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1357)],
        checks={
            "sha": [
                _check("claude-review", "failure"),
                _check("static", "failure", suite=2),
            ]
        },
        runs={"sha": [_run(4242), _run(4243, suite=2)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []
    assert "static" in outcome.report


def test_second_attempt_is_left_alone(rerun: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Перезапускали — значит это уже не мигание, а дефект."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={"sha": [_check("claude-review", "failure")]},
        runs={"sha": [_run(4242, attempt=2)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []
    assert "не мигание" in outcome.report


def test_running_run_is_left_alone(rerun: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Идущий прогон не трогаем: его исход ещё не известен."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={"sha": [_check("claude-review", "failure")]},
        runs={"sha": [_run(4242, status="in_progress")]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []
    assert "ещё идёт" in outcome.report


def test_unfinished_check_is_not_a_failure(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Ещё идёт» — не «упало»: незавершённая проверка кандидатом не считается."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={"sha": [_check("claude-review", None, status="in_progress")]},
        runs={"sha": [_run(4242)]},
    )
    api.install(rerun, monkeypatch)

    rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []


def test_stale_red_next_to_a_fresh_green_is_ignored(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уже перезапущенное не перезапускается снова.

    При перезапуске старая красная запись check-run никуда не девается. Без
    отбора свежайшей на имя механизм видел бы её рядом с новой зелёной — и
    крутил бы перезапуск по кругу.
    """
    api = _FakeApi(
        pulls=[_pull(rerun, 1359)],
        checks={
            "sha": [
                _check("claude-review", "failure", started="2026-08-22T11:00:00Z"),
                _check("claude-review", "success", started="2026-08-22T13:00:00Z"),
            ]
        },
        runs={"sha": [_run(4242, attempt=2)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == []
    assert outcome.rerun == []


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"draft": True}, "черновик"),
        ({"fork": True}, "форк"),
        ({"labels": ("hold",)}, "придержан меткой"),
    ],
)
def test_pulls_out_of_scope_are_skipped(
    rerun: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
    why: str,
) -> None:
    """Черновик, форк и придержанный PR механизм не рассматривает вовсе."""
    api = _FakeApi(
        pulls=[_pull(rerun, 1359, **kwargs)],
        checks={"sha": [_check("claude-review", "failure")]},
        runs={"sha": [_run(4242)]},
    )
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert api.rerun_calls == [], why
    assert outcome.rerun == []


def test_failed_rerun_does_not_break_the_pass(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ по одному PR не роняет проход: остальные должны быть обслужены."""
    gh = rerun.gh_rest

    def _explode(_repo: str, run_id: int, **_kwargs: Any) -> bool:
        if run_id == 4242:
            raise gh.GitHubError("403 Resource not accessible by integration")
        calls.append(run_id)
        return True

    calls: list[int] = []
    monkeypatch.setattr(
        gh,
        "list_pulls",
        lambda *a, **k: [_pull(rerun, 1, sha="a"), _pull(rerun, 2, sha="b")],
    )
    monkeypatch.setattr(
        gh,
        "pull_checks",
        lambda _repo, sha, **_k: {"check_runs": [_check("claude-review", "failure")]},
    )
    monkeypatch.setattr(
        gh,
        "workflow_runs",
        lambda _repo, sha, **_k: {
            "workflow_runs": [_run(4242 if sha == "a" else 4243)],
        },
    )
    monkeypatch.setattr(gh, "rerun_failed_jobs", _explode)

    outcome = rerun.rerun_flaky("owner/repo")

    assert calls == [4243], "второй PR обязан быть обслужен после отказа по первому"
    assert "не прошёл" in outcome.report


def test_empty_pass_says_so(rerun: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие кандидатов — обычный исход, и он говорит о себе вслух."""
    api = _FakeApi(pulls=[], checks={}, runs={})
    api.install(rerun, monkeypatch)

    outcome = rerun.rerun_flaky("owner/repo")

    assert outcome.rerun == []
    assert "перезапускать нечего" in outcome.report


def test_quota_exhaustion_is_not_a_red_run(
    rerun: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кончившаяся квота — состояние аккаунта, а не поломка механизма."""

    def _rate_limited(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise rerun.gh_rest.RateLimited("лимит исчерпан, сброс в 13:00")

    monkeypatch.setattr(rerun.gh_rest, "list_pulls", _rate_limited)

    assert rerun.main([]) == rerun.gh_rest.EXIT_OK


# --- границы, заданные списком ------------------------------------------------


def test_the_allow_list_is_narrow(rerun: ModuleType) -> None:
    """Список закрыт и содержит только то, чей красный не означает дефект кода.

    Тест намеренно перечисляет содержимое: расширение списка обязано быть
    осознанным изменением с обоснованием в PR, а не строкой, дописанной заодно.
    """
    assert rerun.AUTO_RERUN == frozenset({"claude-review"})


def test_required_checks_are_never_allowed(rerun: ModuleType) -> None:
    """Ни одна обязательная проверка ruleset не может попасть в список."""
    required = {
        "docs-guardrails",
        "static",
        "supply-chain",
        "sandbox-linux",
        "e2e",
        "test",
    }
    assert not (rerun.AUTO_RERUN & required)


# --- исполнитель --------------------------------------------------------------


def test_workflow_runs_on_a_schedule_and_by_hand() -> None:
    """Механизм без исполнителя повторил бы issue #1348 — гард, который никто не зовёт."""
    assert _WORKFLOW.exists(), "нет workflow, запускающего перезапуск"

    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text, "без расписания механизм снова ждёт, что кто-то вспомнит"
    assert "workflow_dispatch:" in text, "нужен ручной запуск: проверить, не дожидаясь получаса"
    assert "rerun_flaky_checks" in text


def test_workflow_asks_for_the_permission_it_needs() -> None:
    """`actions: write` — то единственное, чего не хватает облачной сессии."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "actions: write" in text


def test_workflow_reports_into_the_run_summary() -> None:
    """Решение механизма должно читаться с первого экрана прогона, а не из лога."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_STEP_SUMMARY" in text
