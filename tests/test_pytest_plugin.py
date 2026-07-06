"""Tests for pytest_plugin.py — грейдер как pytest-плагин (issue #57).

Используют встроенную фикстуру ``pytester`` (включена в conftest.py через
``pytest_plugins = ["pytester"]``): каждый тест создаёт во временной папке
файл-решение ``task.py`` + ``tests/`` и запускает вложенный pytest. Плагин
подхватывается автоматически через зарегистрированный ``pytest11`` entry
point (``pip install -e .``), поэтому явно грузить его в тесте не нужно.
"""

from __future__ import annotations

import pathlib

import pytest


def _make_solution(root: pathlib.Path, body: str, cases: list[tuple[str, str]]) -> None:
    """Создать root/task.py и root/tests/ с парами (stdin, expected) Формата 1."""
    (root / "task.py").write_text(body, encoding="utf-8")
    tdir = root / "tests"
    tdir.mkdir(exist_ok=True)
    for i, (stdin, expected) in enumerate(cases, start=1):
        (tdir / str(i)).write_text(stdin, encoding="utf-8")
        (tdir / f"{i}.clue").write_text(expected, encoding="utf-8")


def test_grader_mode_collects_and_passes(pytester: pytest.Pytester) -> None:
    """--grader-mode собирает task.py и прогоняет тест-кейс — PASSED."""
    _make_solution(pytester.path, "print(int(input()) * 2)\n", [("21\n", "42\n")])
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes(passed=1)


def test_grader_mode_multiple_cases(pytester: pytest.Pytester) -> None:
    """Каждый тест-кейс — отдельный item."""
    _make_solution(
        pytester.path,
        "print(int(input()) * 2)\n",
        [("1\n", "2\n"), ("5\n", "10\n"), ("0\n", "0\n")],
    )
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes(passed=3)


def test_grader_mode_reports_failure_with_diff(pytester: pytest.Pytester) -> None:
    """Неверный вывод → FAILED с diff «Ожидалось/Получено»."""
    _make_solution(pytester.path, "print(999)\n", [("21\n", "42\n")])
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Ожидалось:*"])
    result.stdout.fnmatch_lines(["*Получено:*"])


def test_grader_mode_reports_runtime_error(pytester: pytest.Pytester) -> None:
    """Ошибка выполнения решения → FAILED с текстом «Ошибка»."""
    _make_solution(pytester.path, "raise ValueError('boom')\n", [("1\n", "1\n")])
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Ошибка:*"])


def test_grader_mode_off_collects_nothing(pytester: pytest.Pytester) -> None:
    """Без --grader-mode task.py не собирается (плагин — no-op)."""
    _make_solution(pytester.path, "print(int(input()) * 2)\n", [("1\n", "2\n")])
    result = pytester.runpytest()  # флаг не передан
    result.assert_outcomes()  # ноль собранных тестов
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_grader_mode_via_ini_option(pytester: pytest.Pytester) -> None:
    """Включение через ini-опцию grader_mode = true (без флага)."""
    _make_solution(pytester.path, "print(int(input()) * 2)\n", [("3\n", "6\n")])
    pytester.makeini("[pytest]\ngrader_mode = true\n")
    result = pytester.runpytest()  # флага нет, включено через ini
    result.assert_outcomes(passed=1)


def test_grader_mode_ignores_non_solution_files(pytester: pytest.Pytester) -> None:
    """helper.py (не подходит под шаблон решения) не собирается."""
    (pytester.path / "helper.py").write_text("x = 1\n", encoding="utf-8")
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes()
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_grader_mode_solution_without_tests_yields_nothing(
    pytester: pytest.Pytester,
) -> None:
    """task.py без соседней tests/ → файл собран, но 0 item'ов, без падения."""
    (pytester.path / "task.py").write_text("print('hi')\n", encoding="utf-8")
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes()
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_grader_mode_mixed_pass_and_fail(pytester: pytest.Pytester) -> None:
    """Смешанный набор: один кейс проходит, другой падает."""
    _make_solution(
        pytester.path,
        "n = int(input())\nprint(n if n == 1 else 999)\n",
        [("1\n", "1\n"), ("2\n", "2\n")],
    )
    result = pytester.runpytest("--grader-mode")
    result.assert_outcomes(passed=1, failed=1)


# ---------------------------------------------------------------------------
# Прямые unit-тесты чистой логики (выполняются во внешнем процессе, поэтому
# засчитываются в coverage — в отличие от вложенных pytester-прогонов).
# ---------------------------------------------------------------------------


class _FakeConfig:
    """Минимальная замена pytest.Config для проверки _grader_enabled."""

    def __init__(self, option: bool, ini: bool) -> None:
        self._option = option
        self._ini = ini

    def getoption(self, name: str) -> bool:
        return self._option

    def getini(self, name: str) -> bool:
        return self._ini


class _FakeParent:
    def __init__(self, config: _FakeConfig) -> None:
        self.config = config


@pytest.mark.parametrize(
    ("option", "ini", "expected"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_grader_enabled(option: bool, ini: bool, expected: bool) -> None:
    from stepik_grader import pytest_plugin

    assert pytest_plugin._grader_enabled(_FakeConfig(option, ini)) is expected


def test_collect_file_returns_none_when_disabled() -> None:
    from stepik_grader import pytest_plugin

    parent = _FakeParent(_FakeConfig(False, False))
    assert pytest_plugin.pytest_collect_file(parent, pathlib.Path("task.py")) is None


def test_grader_failure_carries_result() -> None:
    from stepik_grader.pytest_plugin import GraderFailure

    exc = GraderFailure({"error": "boom", "verdict": "RE"})
    assert exc.result == {"error": "boom", "verdict": "RE"}
    assert "boom" in str(exc)


def test_grader_failure_default_message_without_error() -> None:
    from stepik_grader.pytest_plugin import GraderFailure

    exc = GraderFailure({"error": "", "verdict": "WA"})
    assert "failed" in str(exc)
