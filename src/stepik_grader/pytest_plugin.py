"""pytest_plugin.py — запуск грейдера как pytest-плагина (issue #57).

Архитектурный слой: Application / Integration (адаптер к pytest).

Регистрируется через entry point ``pytest11`` (см. pyproject.toml), поэтому
доступен в любом проекте, где установлен ``stepik-python-grader``. По
умолчанию — no-op: сбор тест-кейсов включается только флагом
``--grader-mode`` или ini-опцией ``grader_mode = true``.

    pytest --grader-mode StepikTasks/

pytest обходит переданный путь и для каждого файла-решения (``task*.py``,
детекция через ``is_solution_file``) собирает по одному ``pytest.Item`` на
тест-кейс из соответствующей ``tests/``-директории. Каждый item исполняется
через ``run_single_test`` — тот же движок, что и у CLI-режима 1; провал даёт
обычный pytest FAILED с diff «ожидалось/получено», ошибка выполнения —
отчёт с текстом исключения.

Импорты ядра (``core/grader_core``, ``core/test_loader``) намеренно ленивы —
внутри хуков, а не на уровне модуля. Entry-point-плагины грузятся ДО старта
coverage.py, поэтому импорт всего ядра на уровне модуля пометил бы его
def-строки непокрытыми во всём пакете. Ленивая загрузка держит момент импорта
внутри реального прогона (после старта coverage) и ускоряет запуск pytest,
когда грейдер-режим не используется.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

__all__ = [
    "GraderFailure",
    "GraderFile",
    "GraderItem",
    "pytest_addoption",
    "pytest_collect_file",
]

if TYPE_CHECKING:
    import os
    import pathlib
    from collections.abc import Iterable

    from _pytest._code.code import ExceptionInfo, TerminalRepr

    from stepik_grader.core.result import CaseResult
    from stepik_grader.core.test_loader import TestCase


def pytest_addoption(parser: pytest.Parser) -> None:
    """Добавить флаг --grader-mode и ini-опцию grader_mode."""
    group = parser.getgroup("stepik-grader", "Stepik Python Grader")
    group.addoption(
        "--grader-mode",
        action="store_true",
        default=False,
        help="Собирать файлы-решения (task*.py) как pytest-тесты, "
        "по одному item на тест-кейс из tests/.",
    )
    parser.addini(
        "grader_mode",
        type="bool",
        default=False,
        help="Включить сбор решений грейдером (эквивалент --grader-mode).",
    )


def _grader_enabled(config: pytest.Config) -> bool:
    """True, если грейдер-режим включён флагом или ini-опцией."""
    return bool(config.getoption("--grader-mode") or config.getini("grader_mode"))


def pytest_collect_file(parent: pytest.Collector, file_path: Any) -> GraderFile | None:
    """Собрать task*.py как GraderFile, если грейдер-режим включён."""
    if not _grader_enabled(parent.config):
        return None
    from stepik_grader.core.test_loader import is_solution_file

    if file_path.suffix == ".py" and is_solution_file(file_path.name):
        return GraderFile.from_parent(parent, path=file_path)
    return None


class GraderFailure(Exception):
    """Провал тест-кейса грейдера — несёт result-словарь run_single_test."""

    def __init__(self, result: CaseResult) -> None:
        self.result = result
        super().__init__(result.get("error") or "grader test case failed")


class GraderFile(pytest.File):
    """Коллектор одного файла-решения: один Item на тест-кейс."""

    def collect(self) -> Iterable[GraderItem]:
        """Собрать по одному ``GraderItem`` на каждый тест-кейс файла-решения."""
        from stepik_grader.core.test_loader import (
            _apply_run_mode_override,
            load_test_cases,
            resolve_test_dir,
        )

        solution = self.path
        test_dir = resolve_test_dir(solution)
        if test_dir is None:
            return  # тесты для этого решения не найдены — нечего собирать
        cases = load_test_cases(test_dir)
        _apply_run_mode_override(cases, solution, test_dir)
        for case in cases:
            yield GraderItem.from_parent(
                self, name=f"test_{case.index}", case=case, solution=solution
            )


class GraderItem(pytest.Item):
    """Один тест-кейс грейдера как исполняемый pytest Item."""

    def __init__(
        self, name: str, parent: GraderFile, case: TestCase, solution: pathlib.Path
    ) -> None:
        super().__init__(name, parent)
        self.case = case
        self.solution = solution

    def runtest(self) -> None:
        """Прогнать кейс через ``run_single_test``; провал → ``GraderFailure``."""
        from stepik_grader.core.grader_core import run_single_test

        result = run_single_test(self.solution, self.case)
        if not result["passed"]:
            raise GraderFailure(result)

    def repr_failure(
        self, excinfo: ExceptionInfo[BaseException], style: Any = None
    ) -> str | TerminalRepr:
        """Читаемый отчёт: verdict + diff «ожидалось/получено» или текст ошибки."""
        if isinstance(excinfo.value, GraderFailure):
            result = excinfo.value.result
            verdict = result.get("verdict", "?")
            lines = [f"Тест-кейс #{self.case.index}  [{verdict}]"]
            if result.get("error"):
                lines.append(f"Ошибка: {result['error']}")
            else:
                lines.append("Ожидалось:")
                lines.extend(f"  {line}" for line in result["expected"])
                lines.append("Получено:")
                lines.extend(f"  {line}" for line in result["output"])
            return "\n".join(lines)
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[os.PathLike[str] | str, int | None, str]:
        """Заголовок отчёта pytest для кейса (путь, строка, человекочитаемое имя)."""
        return self.path, 0, f"grader: {self.name}"
