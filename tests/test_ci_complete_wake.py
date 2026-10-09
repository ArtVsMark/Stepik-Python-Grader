"""Тесты scripts/ci_complete_wake.py — будильник сводного гейта (issue #1554).

Проверяются обе половины: что будильник перезапускает (красную сводку,
вынесенную раньше конца ``CI``) и чего не трогает — идущую, зелёную,
отменённую, вынесенную после конца ``CI`` и исчерпавшую попытки. Плюс условия
workflow: без них будильник просыпался бы на прогонах ``main`` и на красном
``CI``, где перезапуск холостой.

Сеть подделывается на уровне ``gh_rest``: тесты описывают решения механизма,
а не HTTP.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_SCRIPT = _SCRIPTS / "ci_complete_wake.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci-complete-wake.yml"

CI_DONE = "2026-10-04T12:00:00Z"
SHA = "a" * 40


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_ci_complete_wake", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wake() -> ModuleType:
    """Свежий модуль механизма на каждый тест."""
    return _load_module()


def _run(**fields: Any) -> dict[str, Any]:
    """Прогон сводки: по умолчанию красный, завершён до конца CI, первая попытка."""
    base: dict[str, Any] = {
        "id": 7,
        "status": "completed",
        "conclusion": "failure",
        "created_at": "2026-10-04T11:30:00Z",
        "updated_at": "2026-10-04T11:45:00Z",
        "run_attempt": 1,
    }
    return {**base, **fields}


def test_a_red_summary_judged_before_ci_ended_is_stale(wake: ModuleType) -> None:
    """Красная сводка, вынесенная раньше конца CI, — устаревшая."""
    assert wake.stale_summary([_run()], CI_DONE) == _run()


def test_the_verdict_tail_covers_the_job_epilogue(wake: ModuleType) -> None:
    """Завершилась через минуту после CI — вердикт всё равно вынесен до него."""
    late = _run(updated_at="2026-10-04T12:01:00Z")
    assert wake.stale_summary([late], CI_DONE) == late


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"status": "in_progress", "conclusion": None}, id="идёт"),
        pytest.param({"conclusion": "success"}, id="зелёная"),
        pytest.param({"conclusion": "cancelled"}, id="отменена"),
        pytest.param({"updated_at": "2026-10-04T12:05:00Z"}, id="после-конца-CI"),
        pytest.param({"run_attempt": 3}, id="попытки-исчерпаны"),
    ],
)
def test_untouched_summaries(wake: ModuleType, fields: dict[str, Any]) -> None:
    """Идущая, зелёная, отменённая, поздняя и исчерпавшая попытки — не трогаются."""
    assert wake.stale_summary([_run(**fields)], CI_DONE) is None


def test_only_the_latest_summary_counts(wake: ModuleType) -> None:
    """Старая красная заменена новой зелёной — перезапускать её нельзя."""
    old = _run(id=1, created_at="2026-10-04T10:00:00Z")
    new = _run(id=2, created_at="2026-10-04T11:50:00Z", conclusion="success")
    assert wake.stale_summary([old, new], CI_DONE) is None


def test_no_runs_means_nothing_to_wake(wake: ModuleType) -> None:
    """Сводки на голове нет — будить нечего."""
    assert wake.stale_summary([], CI_DONE) is None


def test_unreadable_time_is_a_broken_run(wake: ModuleType) -> None:
    """Непрочитанное время площадки — отказ захода, а не «не устарела»."""
    with pytest.raises(wake.NotRun):
        wake.stale_summary([_run(updated_at="вчера")], CI_DONE)


def test_manual_run_end_of_calendar_does_not_overflow(wake: ModuleType) -> None:
    """Ручной заход: конец CI — последний миг календаря, сравнение не переполняется."""
    assert wake.stale_summary([_run()], "9999-12-31T23:59:59Z") == _run()


class _FakeApi:
    """Подделка тех и только тех вызовов, которые делает механизм."""

    def __init__(self, runs: list[dict[str, Any]], *, restarted: bool = True) -> None:
        self.runs = runs
        self.restarted = restarted
        self.paths: list[str] = []
        self.rerun: list[int] = []

    def install(self, module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module.gh_rest, "request", self.request)
        monkeypatch.setattr(module.gh_rest, "rerun_failed_jobs", self.rerun_failed_jobs)

    def request(self, method: str, path: str, **_: Any) -> Any:
        assert method == "GET"
        self.paths.append(path)
        return SimpleNamespace(data={"workflow_runs": self.runs})

    def rerun_failed_jobs(self, repo: str, run_id: int, **_: Any) -> bool:
        self.rerun.append(run_id)
        return self.restarted


def test_wake_reruns_the_stale_summary(wake: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Устаревшая сводка перезапускается, и запрос спрашивает только события PR."""
    api = _FakeApi([_run()])
    api.install(wake, monkeypatch)

    said = wake.wake("o/r", SHA, CI_DONE)

    assert api.rerun == [7]
    assert "перезапущена" in said
    [path] = api.paths
    assert f"head_sha={SHA}" in path and "event=pull_request" in path
    assert "/ci-complete.yml/" in path


def test_dry_run_does_not_rerun(wake: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run`` называет действие и ничего не перезапускает."""
    api = _FakeApi([_run()])
    api.install(wake, monkeypatch)

    said = wake.wake("o/r", SHA, CI_DONE, dry_run=True)

    assert api.rerun == []
    assert "перезапустил бы" in said


def test_fresh_summary_is_left_alone(wake: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Зелёная сводка — перезапуска нет, причина названа."""
    api = _FakeApi([_run(conclusion="success")])
    api.install(wake, monkeypatch)

    said = wake.wake("o/r", SHA, CI_DONE)

    assert api.rerun == []
    assert "не устарела" in said


def test_already_rerunning_is_said_not_hidden(
    wake: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Площадка ответила «нечего перезапускать» — так и сказано, без второго зова."""
    api = _FakeApi([_run()], restarted=False)
    api.install(wake, monkeypatch)

    assert "уже перезапускается" in wake.wake("o/r", SHA, CI_DONE)


def test_refused_rerun_is_a_broken_run(
    wake: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отказ площадки — исход 2 с причиной, а не тихий ноль."""
    api = _FakeApi([_run()])
    api.install(wake, monkeypatch)

    def refuse(repo: str, run_id: int, **_: Any) -> bool:
        raise wake.gh_rest.GitHubError("403 Resource not accessible by integration")

    monkeypatch.setattr(wake.gh_rest, "rerun_failed_jobs", refuse)

    code = wake.main(["--repo", "o/r", "--sha", SHA, "--ci-done-at", CI_DONE])

    assert code == wake.gh_rest.EXIT_WAIT
    assert "403" in capsys.readouterr().err


def test_missing_arguments_is_a_broken_run(
    wake: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голова или время конца CI не названы — исход 2."""
    monkeypatch.delenv("HEAD_SHA", raising=False)
    monkeypatch.delenv("CI_DONE_AT", raising=False)
    assert wake.main(["--repo", "o/r"]) == wake.gh_rest.EXIT_WAIT


def test_workflow_wakes_only_on_green_pull_request_ci() -> None:
    """Будильник слушает ``CI`` и просыпается только на зелёном прогоне PR."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert 'workflows: ["CI"]' in text
    assert "github.event.workflow_run.event == 'pull_request'" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "actions: write" in text
