"""_helpers.py -- shared solution/test-case fixtures for tests/e2e/ (issue #263).

Not a test module itself (no ``test_`` prefix, so pytest never collects it).

Здесь же — чистые помощники guard'а «набор не скипнулся молча» (issue #921,
находка `QA-2-02`). Хуки pytest обязаны жить в ``conftest.py``, а вот решение,
которое они принимают, — нет: вынесенное сюда, оно проверяется обычным тестом
из основного набора, а не только в job'е с браузером.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = ["GUARD_FILE", "REQUIRE_E2E_ENV", "executed_beyond_guards", "write_task"]

REQUIRE_E2E_ENV = "STEPIK_REQUIRE_E2E_TESTS"
"""Переменная, включающая жёсткий режим: пропуск набора становится отказом."""

GUARD_FILE = "test_not_silently_skipped.py"
"""Файл самих guard'ов — они не считаются полезной работой набора."""


def executed_beyond_guards(nodeids: Iterable[str]) -> set[str]:
    """Выполненные e2e-тесты за вычетом самих guard'ов.

    Guard'ы выполняются всегда — они не трогают ни браузер, ни сервер. Считать
    их значило бы объявлять набор живым ровно тогда, когда живы только сторожа.
    """
    return {nodeid for nodeid in nodeids if GUARD_FILE not in nodeid}


def write_task(
    folder: Path,
    code: str,
    *,
    stdin: str = "4",
    expected: str = "5",
    filename: str = "task.py",
) -> Path:
    """Write a solution file + one legacy-format (``N``/``N.clue``) test case.

    Mirrors ``tests/test_web.py``'s ``_make_task`` helper -- one stdin/expected
    pair is enough to drive a real browser through the grading UI end to end.
    """
    sol = folder / filename
    sol.write_text(code, encoding="utf-8")
    tests_dir = folder / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "1").write_text(stdin, encoding="utf-8")
    (tests_dir / "1.clue").write_text(expected, encoding="utf-8")
    return sol
