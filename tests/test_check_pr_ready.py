"""Тесты scripts/check_pr_ready.py — готовность PR к мержу (issue #997).

Ядро скрипта — чистая функция `evaluate`, и проверяется именно она: сеть здесь
не нужна, а нужен разбор состояний, которые прошлую проверку обманули. Главный
из них — **пустой** список проверок сразу после пуша: условие «нет красных и нет
ожидающих» на пустоте выполняется, и PR уходит в мерж с девятью незавершёнными
джобами.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_pr_ready.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_pr_ready", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Модуль скрипта."""
    return _load_module()


def _pull(**overrides: Any) -> dict[str, Any]:
    """Открытый PR без конфликтов, если не сказано иначе."""
    return {
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "head": {"sha": "deadbeef"},
        **overrides,
    }


def _runs(*statuses: tuple[str, str | None]) -> dict[str, Any]:
    """Прогоны Actions: пары «статус, заключение»."""
    return {
        "workflow_runs": [
            {"name": f"run-{index}", "status": status, "conclusion": conclusion}
            for index, (status, conclusion) in enumerate(statuses)
        ]
    }


def _checks(*named: tuple[str, str, str | None]) -> dict[str, Any]:
    """Check-runs: тройки «имя, статус, заключение»."""
    return {
        "check_runs": [
            {"name": name, "status": status, "conclusion": conclusion}
            for name, status, conclusion in named
        ]
    }


_GREEN = ("static", "completed", "success"), ("test (ubuntu-latest)", "completed", "success")
_EXPECTED = {"static", "test (ubuntu-latest)"}


class TestNotStartedIsNotGreen:
    """Пустой и неполный список проверок — «CI не стартовал», а не «зелено»."""

    def test_empty_check_list_blocks_merge(self, module: ModuleType) -> None:
        """Сразу после пуша проверок ещё нет — мержить нельзя.

        Это и есть исходный инцидент: REST отдаёт пустой список, и наивное
        «нет красных, нет ожидающих» пропускает PR в мерж.
        """
        verdict = module.evaluate(_pull(), {"workflow_runs": []}, {"check_runs": []}, _EXPECTED)

        assert not verdict.ready
        assert any("CI ещё не стартовал" in reason for reason in verdict.reasons)
        assert any("не стартовало" in reason for reason in verdict.reasons)

    def test_missing_jobs_block_merge(self, module: ModuleType) -> None:
        """Часть джобов ещё не создана — набор меньше эталонного."""
        verdict = module.evaluate(
            _pull(),
            _runs(("completed", "success")),
            _checks(("static", "completed", "success")),
            _EXPECTED,
        )

        assert not verdict.ready
        assert verdict.missing == ["test (ubuntu-latest)"]

    def test_running_workflow_blocks_merge(self, module: ModuleType) -> None:
        """Прогон ещё идёт: часть джобов физически не существует."""
        verdict = module.evaluate(
            _pull(), _runs(("in_progress", None)), _checks(*_GREEN), _EXPECTED
        )

        assert not verdict.ready
        assert any("прогоны не завершены" in reason for reason in verdict.reasons)

    def test_queued_check_blocks_merge(self, module: ModuleType) -> None:
        """Джоб в очереди — не зелёный и не красный, а незавершённый."""
        verdict = module.evaluate(
            _pull(),
            _runs(("completed", "success")),
            _checks(("static", "completed", "success"), ("test (ubuntu-latest)", "queued", None)),
            _EXPECTED,
        )

        assert not verdict.ready
        assert any("проверки не завершены" in reason for reason in verdict.reasons)


class TestFailuresBlockMerge:
    """Красное состояние в любом слое запрещает мерж."""

    def test_failed_check_blocks_merge(self, module: ModuleType) -> None:
        """Провалившийся джоб."""
        verdict = module.evaluate(
            _pull(),
            _runs(("completed", "success")),
            _checks(
                ("static", "completed", "failure"), ("test (ubuntu-latest)", "completed", "success")
            ),
            _EXPECTED,
        )

        assert not verdict.ready
        assert any("красные проверки" in reason for reason in verdict.reasons)

    def test_failed_workflow_blocks_merge(self, module: ModuleType) -> None:
        """Красный прогон целиком."""
        verdict = module.evaluate(
            _pull(), _runs(("completed", "failure")), _checks(*_GREEN), _EXPECTED
        )

        assert not verdict.ready
        assert any("красные прогоны" in reason for reason in verdict.reasons)

    def test_draft_blocks_merge(self, module: ModuleType) -> None:
        """Черновик не мержится, даже если всё зелёное."""
        verdict = module.evaluate(
            _pull(draft=True), _runs(("completed", "success")), _checks(*_GREEN), _EXPECTED
        )

        assert not verdict.ready

    def test_conflict_blocks_merge(self, module: ModuleType) -> None:
        """Конфликт с базовой веткой — `mergeable_state=dirty`."""
        verdict = module.evaluate(
            _pull(mergeable_state="dirty", mergeable=False),
            _runs(("completed", "success")),
            _checks(*_GREEN),
            _EXPECTED,
        )

        assert not verdict.ready

    def test_closed_pull_blocks_merge(self, module: ModuleType) -> None:
        """Закрытый PR мержить нечего."""
        verdict = module.evaluate(
            _pull(state="closed"), _runs(("completed", "success")), _checks(*_GREEN), _EXPECTED
        )

        assert not verdict.ready


class TestReady:
    """Зелёный путь: набор полон, всё завершено и успешно."""

    def test_full_green_set_is_ready(self, module: ModuleType) -> None:
        """Полный эталонный набор, все завершены успешно."""
        verdict = module.evaluate(
            _pull(), _runs(("completed", "success")), _checks(*_GREEN), _EXPECTED
        )

        assert verdict.ready
        assert verdict.reasons == []
        assert verdict.completed == verdict.total_checks == 2

    def test_skipped_counts_as_success(self, module: ModuleType) -> None:
        """Пропущенный джоб — это условие в workflow, а не отказ."""
        verdict = module.evaluate(
            _pull(),
            _runs(("completed", "skipped")),
            _checks(
                ("static", "completed", "success"), ("test (ubuntu-latest)", "completed", "skipped")
            ),
            _EXPECTED,
        )

        assert verdict.ready

    def test_unknown_expected_set_does_not_block(self, module: ModuleType) -> None:
        """Эталон не удалось получить — не выдумываем недостающие имена."""
        verdict = module.evaluate(_pull(), _runs(("completed", "success")), _checks(*_GREEN), set())

        assert verdict.ready


class TestStaleRunsAreIgnored:
    """Отменённый предшественник не должен держать PR красным вечно."""

    def _pair(self, name: str, old: tuple[str, str], new: tuple[str, str]) -> dict[str, Any]:
        """Две записи одного имени: старая и свежая (статус, заключение)."""
        return {
            "check_runs": [
                {
                    "name": name,
                    "status": old[0],
                    "conclusion": old[1],
                    "started_at": "2026-08-13T12:47:35Z",
                    "id": 1,
                },
                {
                    "name": name,
                    "status": new[0],
                    "conclusion": new[1],
                    "started_at": "2026-08-13T12:47:43Z",
                    "id": 2,
                },
            ]
        }

    def test_cancelled_predecessor_does_not_block(self, module: ModuleType) -> None:
        """Перезапуск оставляет на коммите две записи — судим по свежей.

        Живой случай: `ci.yml` держит concurrency-группу, снятие черновика
        погасило прогон ревью и тут же запустило новый. В ответе REST рядом
        лежали `cancelled` в 12:47:35 и `success` в 12:47:43 — и гейт объявлял
        PR красным навсегда.
        """
        checks = self._pair("claude-review", ("completed", "cancelled"), ("completed", "success"))

        verdict = module.evaluate(
            _pull(), _runs(("completed", "success")), checks, {"claude-review"}
        )

        assert verdict.ready, verdict.reasons

    def test_fresh_failure_still_blocks(self, module: ModuleType) -> None:
        """А если свежая запись красная — старый успех её не отменяет."""
        checks = self._pair("static", ("completed", "success"), ("completed", "failure"))

        verdict = module.evaluate(_pull(), _runs(("completed", "success")), checks, {"static"})

        assert not verdict.ready
        assert any("красные проверки" in reason for reason in verdict.reasons)

    def test_stale_workflow_run_does_not_block(self, module: ModuleType) -> None:
        """То же для прогонов Actions: отменённый предшественник не в счёт."""
        workflow_runs = {
            "workflow_runs": [
                {
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "cancelled",
                    "run_started_at": "2026-08-13T12:40:00Z",
                    "id": 1,
                },
                {
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "run_started_at": "2026-08-13T12:47:00Z",
                    "id": 2,
                },
            ]
        }

        verdict = module.evaluate(_pull(), workflow_runs, _checks(*_GREEN), _EXPECTED)

        assert verdict.ready, verdict.reasons

    def test_latest_by_name_keeps_one_per_name(self, module: ModuleType) -> None:
        """Отбор оставляет по одной записи на имя — самую свежую."""
        items = [
            {"name": "a", "started_at": "2026-01-01T00:00:00Z", "id": 1},
            {"name": "a", "started_at": "2026-01-02T00:00:00Z", "id": 2},
            {"name": "b", "started_at": "2026-01-01T00:00:00Z", "id": 3},
        ]

        latest = module.latest_by_name(items)

        assert sorted((item["name"], item["id"]) for item in latest) == [("a", 2), ("b", 3)]


class TestExpectedSetSource:
    """Эталон собирается только из прогонов, которые бывают и на PR."""

    def test_workflow_without_pull_request_trigger_is_excluded(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Прогон, который на PR не запускается, не должен числиться недостающим.

        Живой случай: на `main` есть проверка от workflow с триггерами
        `issue_comment`/`workflow_dispatch`. На PR её не бывает никогда, и в
        эталоне она превращала гейт в вечно красный — а гейт, который краснеет
        всегда, обходят.
        """
        (tmp_path / "ci.yml").write_text(
            "name: CI\non:\n  push:\n    branches: [main]\n  pull_request:\n    branches: [main]\n"
            "jobs:\n  static:\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        (tmp_path / "claude.yml").write_text(
            "name: Claude\non:\n  issue_comment:\n    types: [created]\n"
            "jobs:\n  claude:\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )

        assert module.workflows_running_on_pull_requests(tmp_path) == {"CI"}

    def test_job_names_are_read(self, module: ModuleType) -> None:
        """Имена джобов читаются из ответа `/actions/runs/{id}/jobs`."""
        payload = {"jobs": [{"name": "static"}, {"name": "test (ubuntu-latest, 3.13, false)"}]}

        assert module.job_names(payload) == {"static", "test (ubuntu-latest, 3.13, false)"}

    def test_real_ci_workflow_is_recognised(self, module: ModuleType) -> None:
        """На настоящих workflow проекта фильтр находит CI."""
        real = pathlib.Path(__file__).parent.parent / ".github" / "workflows"

        assert "CI" in module.workflows_running_on_pull_requests(real)


class TestHelpers:
    """Мелочи разбора ответов REST."""

    def test_check_names_reads_names(self, module: ModuleType) -> None:
        """Имена вытаскиваются из `check_runs`."""
        assert module.check_names(_checks(*_GREEN)) == _EXPECTED

    def test_pending_runs_lists_unfinished(self, module: ModuleType) -> None:
        """Незавершённые прогоны перечисляются со статусом."""
        pending = module.pending_runs(_runs(("queued", None), ("completed", "success")))

        assert pending == ["run-0 (queued)"]
