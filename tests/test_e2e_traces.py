"""Трейсы Playwright сохраняются только у упавшего e2e-теста (issue #1582).

Проверяется логика сохранения без браузера: подделка контекста записывает,
с каким путём остановили трейсинг. Живой прогон — в ``tests/e2e``.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e import conftest as e2e_conftest


class _FakeTracing:
    """Ровно тот кусочек ``BrowserContext.tracing``, которым пользуется фикстура."""

    def __init__(self) -> None:
        self.stopped_with: list[str | None] = []

    def stop(self, path: str | None = None) -> None:
        self.stopped_with.append(path)


def _context() -> Any:
    return SimpleNamespace(tracing=_FakeTracing())


def _node(*, failed: bool) -> Any:
    return SimpleNamespace(
        nodeid="tests/e2e/test_x.py::test_y", rep_call=SimpleNamespace(failed=failed)
    )


def test_a_failed_test_keeps_its_trace(tmp_path: Path) -> None:
    """Упал — трейс ложится файлом в каталог: по нему и разбирают падение."""
    context = _context()

    e2e_conftest._finish_trace(context, tmp_path / "traces", _node(failed=True))

    [path] = context.tracing.stopped_with
    assert path is not None and path.endswith(".zip")
    assert Path(path).parent == tmp_path / "traces"


def test_a_passed_test_drops_its_trace(tmp_path: Path) -> None:
    """Прошёл — трейс выбрасывается: артефакт не растёт на зелёных тестах."""
    context = _context()

    e2e_conftest._finish_trace(context, tmp_path, _node(failed=False))

    assert context.tracing.stopped_with == [None]


def test_no_report_means_no_file(tmp_path: Path) -> None:
    """Отчёта фазы нет (упал setup) — трейсинг всё равно остановлен, файла нет."""
    context = _context()

    e2e_conftest._finish_trace(context, tmp_path, SimpleNamespace(nodeid="x"))

    assert context.tracing.stopped_with == [None]
