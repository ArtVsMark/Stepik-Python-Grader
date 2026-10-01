"""Тесты scripts/rerun_red_main.py — красная база получает ОДИН перезапуск.

Предыстория (issue #1524). Красная ``main`` замораживает очередь мержа: пока
база сломана, не двигается никто. Правило верное, но выхода из положения у него
не было, когда база красная не по делу: новый прогон ``main`` рождается только
новым мержем, которого заморозка и не даёт, а перезапуск из облачной сессии
закрыт (``403`` на ``actions:write``). Дважды подряд очередь стояла из-за одной
мигнувшей ячейки матрицы, и разблокировал её клик человека.

Здесь проверяется не «перезапускает», а **где механизм останавливается**. Обе
границы обязательны, и обе описаны отдельными тестами:

* упала ровно одна проверка — две и больше означают поломку, а не мигание;
* попытка ровно одна — второй перезапуск это уже добывание зелёного.

Сеть подделывается на уровне ``gh_rest``: тесты описывают решения механизма,
а не HTTP.
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
_SCRIPT = _SCRIPTS / "rerun_red_main.py"
_WORKFLOW = _ROOT / ".github" / "workflows" / "rerun-red-main.yml"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_rerun_red_main", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def red() -> ModuleType:
    """Свежий модуль механизма на каждый тест."""
    return _load_module()


class _FakeApi:
    """Подделка тех и только тех вызовов, которые делает механизм."""

    def __init__(
        self,
        *,
        conclusion: str | None = "failure",
        status: str = "completed",
        attempt: int = 1,
        failed: tuple[str, ...] = ("test (macos-latest, 3.12)",),
        run_id: int = 4242,
        runs: list[dict[str, Any]] | None = None,
        rerun_raises: BaseException | None = None,
    ) -> None:
        self.runs = (
            runs
            if runs is not None
            else [
                {
                    "id": run_id,
                    "head_sha": "deadbeef",
                    "status": status,
                    "conclusion": conclusion,
                    "run_attempt": attempt,
                }
            ]
        )
        self.failed = failed
        self.rerun_raises = rerun_raises
        self.rerun_calls: list[int] = []

    def install(self, module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            module.gh_rest,
            "main_run",
            lambda *_a, **_k: {"workflow_runs": self.runs},
        )
        monkeypatch.setattr(
            module.rerun_flaky_checks,
            "failed_checks",
            lambda *_a, **_k: [{"name": name} for name in self.failed],
        )
        monkeypatch.setattr(module.gh_rest, "rerun_failed_jobs", self._rerun)

    def _rerun(self, _repo: str, run_id: int, **_kwargs: Any) -> bool:
        if self.rerun_raises is not None:
            raise self.rerun_raises
        self.rerun_calls.append(run_id)
        return True


# --- ради чего всё ------------------------------------------------------------


def test_lone_failure_on_the_first_attempt_is_rerun(
    red: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Единственная упавшая проверка на первой попытке — тот самый случай."""
    api = _FakeApi()
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == [4242]
    assert outcome.rerun == [4242]
    assert "перезапущен прогон 4242" in outcome.report


def test_dry_run_touches_nothing(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run`` называет решение, не трогая Actions."""
    api = _FakeApi()
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo", dry_run=True)

    assert api.rerun_calls == []
    assert outcome.rerun == []
    assert "перезапустил бы прогон 4242" in outcome.report


# --- где механизм останавливается ---------------------------------------------


def test_two_failures_are_a_breakage_not_a_flake(
    red: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Две красные проверки — поломка; заморозка остаётся, перезапуска нет.

    Это первая из двух границ механизма, и она несёт весь его смысл: настоящий
    дефект валит ряд ячеек, сборку или линтер, а мигание почти всегда одиночно.
    """
    api = _FakeApi(failed=("test (macos-latest, 3.12)", "static"))
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "не мигание, а поломка" in outcome.report
    assert "static" in outcome.report, "имена упавших обязаны попасть в отчёт"


def test_second_attempt_is_left_to_a_human(
    red: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вторая граница: мигание проверяют ОДИН раз.

    Дальше перезапуск перестаёт быть проверкой гипотезы и становится добыванием
    зелёного — тем, из-за чего автоматический перезапуск и считается опасным.
    """
    api = _FakeApi(attempt=2)
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "blocker" in outcome.report, "отчёт обязан назвать выход: PR с меткой blocker"


def test_green_base_is_left_alone(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Зелёная база — обычное состояние, и перезапускать в ней нечего."""
    api = _FakeApi(conclusion="success")
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "зелёная" in outcome.report


def test_running_base_is_awaited(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Незавершённый прогон не трогаем: его исход ещё не известен."""
    api = _FakeApi(status="in_progress", conclusion=None)
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "ещё идёт" in outcome.report


def test_red_without_a_named_failure_is_not_rerun(
    red: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Красный прогон без единой названной упавшей проверки перезапуск не чинит.

    Так выглядит отказ инфраструктуры до старта джобов (или отменённый прогон):
    перезапускать «упавшие джобы» тут попросту нечего, разбирать надо прогон.
    """
    api = _FakeApi(failed=())
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "разбирать надо сам прогон" in outcome.report


def test_no_runs_at_all(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой ответ — не повод молчать и не повод падать."""
    api = _FakeApi(runs=[])
    api.install(red, monkeypatch)

    outcome = red.rerun_red_base("owner/repo")

    assert api.rerun_calls == []
    assert "судить не по чему" in outcome.report


# --- отказы площадки ----------------------------------------------------------


def test_forbidden_rerun_is_reported_not_raised(
    red: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``403`` на запись — сообщение, а не поломка механизма.

    Ровно так механизм отвечает, если его позвали из облачной сессии, где
    ``actions:write`` закрыт прокси. Красным это делать нельзя: чинить нечего.
    """
    api = _FakeApi(rerun_raises=None)
    api.install(red, monkeypatch)
    monkeypatch.setattr(
        red.gh_rest,
        "rerun_failed_jobs",
        _raiser(red.gh_rest.GitHubError("403 Resource not accessible by integration")),
    )

    outcome = red.rerun_red_base("owner/repo")

    assert "перезапуск не удался" in outcome.report
    assert "403" in outcome.report


def test_quota_exhaustion_asks_to_wait(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Кончившаяся квота — состояние аккаунта: свой код возврата, не отказ."""
    monkeypatch.setattr(
        red.gh_rest,
        "main_run",
        _raiser(red.gh_rest.RateLimited("лимит исчерпан, сброс в 13:00")),
    )

    assert red.main([]) == red.gh_rest.EXIT_WAIT


def test_unreadable_base_is_a_failure(red: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Состояние базы не прочитано — механизм отработать не смог, и говорит это."""
    monkeypatch.setattr(
        red.gh_rest,
        "main_run",
        _raiser(red.gh_rest.GitHubError("502 Bad Gateway")),
    )

    assert red.main([]) == red.gh_rest.EXIT_FAIL


def _raiser(exc: BaseException) -> Any:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _boom


# --- границы, заданные самим правилом -----------------------------------------


def test_one_failure_is_the_whole_rule(red: ModuleType) -> None:
    """Порог «ровно одна» — не настройка, а формулировка правила.

    Тест перечисляет значение намеренно: поднять порог значит разрешить
    перезапускать поломки, и это обязано быть отдельным осознанным изменением
    с обоснованием, а не числом, поправленным заодно.
    """
    assert red._LONE_FAILURE == 1


@pytest.mark.parametrize("attempt", [2, 3, 10])
def test_no_attempt_beyond_the_first_ever_reruns(red: ModuleType, attempt: int) -> None:
    """Ни одна попытка после первой не даёт перезапуска — сколько бы их ни было."""
    decision = red.decide(
        conclusion="failure",
        status="completed",
        failed=["test"],
        attempt=attempt,
        run_id=1,
    )
    assert decision.rerun is False


def test_decision_carries_the_run_to_restart(red: ModuleType) -> None:
    """Решение о перезапуске обязано называть, ЧТО перезапускать."""
    decision = red.decide(
        conclusion="failure",
        status="completed",
        failed=["e2e"],
        attempt=1,
        run_id=777,
    )
    assert decision.rerun is True
    assert decision.run_id == 777
    assert "e2e" in decision.reason


# --- исполнитель --------------------------------------------------------------


def test_workflow_exists_and_runs_after_the_base_run() -> None:
    """Механизм без исполнителя — гард, который никто не зовёт (issue #1348)."""
    assert _WORKFLOW.exists(), "нет workflow, запускающего перезапуск красной базы"

    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text, "решать надо сразу по завершении прогона базы"
    assert "workflow_dispatch:" in text, "нужен ручной запуск, не дожидаясь события"
    assert "rerun_red_main" in text


def test_workflow_asks_for_the_permission_it_needs() -> None:
    """``actions: write`` — то единственное, чего не хватает облачной сессии."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "actions: write" in text


def test_workflow_reports_into_the_run_summary() -> None:
    """След обязателен: «было красным, стало зелёным» без объяснения — улика."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_STEP_SUMMARY" in text
