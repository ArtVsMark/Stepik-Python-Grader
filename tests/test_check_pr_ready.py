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


class TestHelpers:
    """Мелочи разбора ответов REST."""

    def test_check_names_reads_names(self, module: ModuleType) -> None:
        """Имена вытаскиваются из `check_runs`."""
        assert module.check_names(_checks(*_GREEN)) == _EXPECTED

    def test_pending_runs_lists_unfinished(self, module: ModuleType) -> None:
        """Незавершённые прогоны перечисляются со статусом."""
        pending = module.pending_runs(_runs(("queued", None), ("completed", "success")))

        assert pending == ["run-0 (queued)"]
