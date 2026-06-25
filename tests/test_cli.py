"""test_cli.py — unit-тесты для CLI-helpers grader.py.

Покрывает:
- _ask_number: корректный ввод, неверный ввод → повтор, пустой ввод → default
- _ask_bench_profile: выбор профилей, неверный → повтор
- _ask_micro_profile: аналогично
- _resolve_test_dir_from_input: существующая папка, несуществующая, пустой ввод
- _apply_run_mode_override: все три источника override (env, file, arg)
- _detect_run_mode: присутствие/отсутствие функций определяет режим
- run_cli / интерактивное меню: выход '0', невалидный выбор → повтор

Инструменты: pytest + monkeypatch + tmp_path. Subprocess не запускается.
"""

from __future__ import annotations

import importlib
import pathlib
import sys
import types
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Вспомогательная фикстура: импортируем grader как модуль
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def grader() -> types.ModuleType:
    """Возвращает импортированный модуль grader."""
    if "grader" in sys.modules:
        return sys.modules["grader"]
    return importlib.import_module("grader")


# ---------------------------------------------------------------------------
# _ask_number
# ---------------------------------------------------------------------------


class TestAskNumber:
    """Тесты для grader._ask_number."""

    def test_valid_input_returns_int(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Корректный ввод '3' возвращает 3."""
        monkeypatch.setattr("builtins.input", lambda _prompt: "3")
        result = grader._ask_number("Выберите: ", valid={1, 2, 3})
        assert result == 3

    def test_invalid_then_valid(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Неверный ввод → повтор, затем корректный ввод принимается."""
        responses = iter(["9", "abc", "2"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = grader._ask_number("Выберите: ", valid={1, 2, 3})
        assert result == 2

    def test_empty_input_returns_default(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Пустой ввод возвращает default если он передан."""
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        result = grader._ask_number("Выберите: ", valid={1, 2, 3}, default=1)
        assert result == 1

    def test_no_default_empty_repeats(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Пустой ввод без default → повтор до корректного ввода."""
        responses = iter(["", "", "1"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = grader._ask_number("Выберите: ", valid={1, 2, 3})
        assert result == 1


# ---------------------------------------------------------------------------
# _ask_bench_profile
# ---------------------------------------------------------------------------


class TestAskBenchProfile:
    """Тесты для grader._ask_bench_profile."""

    def test_valid_profile(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ввод '1' возвращает первый профиль."""
        # Первый профиль всегда существует
        monkeypatch.setattr("builtins.input", lambda _prompt: "1")
        result = grader._ask_bench_profile()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_invalid_then_valid(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Неверный ввод → повтор, затем принимается."""
        responses = iter(["999", "bad", "1"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = grader._ask_bench_profile()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _ask_micro_profile
# ---------------------------------------------------------------------------


class TestAskMicroProfile:
    """Тесты для grader._ask_micro_profile."""

    def test_valid_profile(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ввод '1' возвращает строку профиля."""
        monkeypatch.setattr("builtins.input", lambda _prompt: "1")
        result = grader._ask_micro_profile()
        assert isinstance(result, str)

    def test_invalid_then_valid(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """После неверного ввода цикл продолжается до корректного."""
        responses = iter(["0", "xyz", "1"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = grader._ask_micro_profile()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _resolve_test_dir_from_input
# ---------------------------------------------------------------------------


class TestResolveTestDirFromInput:
    """Тесты для grader._resolve_test_dir_from_input."""

    def test_existing_dir_returned(self, grader: types.ModuleType, tmp_path: pathlib.Path) -> None:
        """Существующая директория возвращается как pathlib.Path."""
        result = grader._resolve_test_dir_from_input(str(tmp_path))
        assert result == tmp_path

    def test_nonexistent_dir_returns_none(self, grader: types.ModuleType, tmp_path: pathlib.Path) -> None:
        """Несуществующий путь возвращает None."""
        result = grader._resolve_test_dir_from_input(str(tmp_path / "no_such_dir"))
        assert result is None

    def test_empty_string_returns_none(self, grader: types.ModuleType) -> None:
        """Пустая строка возвращает None."""
        result = grader._resolve_test_dir_from_input("")
        assert result is None

    def test_file_path_returns_none(self, grader: types.ModuleType, tmp_path: pathlib.Path) -> None:
        """Путь к файлу (не директории) возвращает None."""
        f = tmp_path / "file.txt"
        f.write_text("hello", encoding="utf-8")
        result = grader._resolve_test_dir_from_input(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# _apply_run_mode_override
# ---------------------------------------------------------------------------


class TestApplyRunModeOverride:
    """Тесты для grader._apply_run_mode_override."""

    def test_arg_override_wins(self, grader: types.ModuleType) -> None:
        """Прямой аргумент функции имеет наивысший приоритет."""
        result = grader._apply_run_mode_override(
            current_mode="script",
            arg_override="function",
            env_override=None,
            file_override=None,
        )
        assert result == "function"

    def test_env_override_second_priority(self, grader: types.ModuleType) -> None:
        """ENV-переменная имеет второй приоритет после arg."""
        result = grader._apply_run_mode_override(
            current_mode="script",
            arg_override=None,
            env_override="function",
            file_override=None,
        )
        assert result == "function"

    def test_file_override_third_priority(self, grader: types.ModuleType) -> None:
        """Файловый override имеет третий приоритет."""
        result = grader._apply_run_mode_override(
            current_mode="script",
            arg_override=None,
            env_override=None,
            file_override="function",
        )
        assert result == "function"

    def test_no_override_returns_current(self, grader: types.ModuleType) -> None:
        """При отсутствии всех override возвращается current_mode."""
        result = grader._apply_run_mode_override(
            current_mode="script",
            arg_override=None,
            env_override=None,
            file_override=None,
        )
        assert result == "script"

    def test_arg_beats_env(self, grader: types.ModuleType) -> None:
        """arg_override побеждает env_override при конфликте."""
        result = grader._apply_run_mode_override(
            current_mode="script",
            arg_override="function",
            env_override="script",
            file_override=None,
        )
        assert result == "function"


# ---------------------------------------------------------------------------
# _detect_run_mode
# ---------------------------------------------------------------------------


class TestDetectRunMode:
    """Тесты для grader._detect_run_mode."""

    def test_detects_function_mode(self, grader: types.ModuleType, tmp_path: pathlib.Path) -> None:
        """Файл с def → режим 'function'."""
        f = tmp_path / "solution.py"
        f.write_text("def solve(n):\n    return n * 2\n", encoding="utf-8")
        mode = grader._detect_run_mode(f)
        assert mode == "function"

    def test_detects_script_mode(self, grader: types.ModuleType, tmp_path: pathlib.Path) -> None:
        """Файл без def → режим 'script'."""
        f = tmp_path / "solution.py"
        f.write_text("n = int(input())\nprint(n * 2)\n", encoding="utf-8")
        mode = grader._detect_run_mode(f)
        assert mode == "script"


# ---------------------------------------------------------------------------
# run_cli — smoke: немедленный выход через '0'
# ---------------------------------------------------------------------------


class TestRunCliSmoke:
    """Smoke-тест: run_cli завершается при вводе '0'."""

    def test_exit_on_zero(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ввод '0' в главном меню завершает run_cli без исключений."""
        monkeypatch.setattr("builtins.input", lambda _prompt: "0")
        # Если run_cli существует в grader.py — вызываем его
        if hasattr(grader, "run_cli"):
            grader.run_cli()  # не должно бросать исключение
        elif hasattr(grader, "_interactive_menu"):
            grader._interactive_menu()

    def test_invalid_choice_then_exit(self, grader: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Невалидный ввод → повтор, затем '0' завершает меню."""
        responses = iter(["99", "bad", "", "0"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        if hasattr(grader, "run_cli"):
            grader.run_cli()
        elif hasattr(grader, "_interactive_menu"):
            grader._interactive_menu()
