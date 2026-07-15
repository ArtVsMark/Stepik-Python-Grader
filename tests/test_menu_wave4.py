"""Tests for Wave 4 menu features (issues #445, #430).

#445: зацикленное меню, пункт «Веб-интерфейс», подсказка про файловый диалог,
сообщение о подстановке дефолта при нечисловом вводе.
#430: тумблер записи истории (сохраняется между запусками), nudge после FAIL.

Язык форсируется английским (как в test_cli.py) — ассерты на стабильные
подстроки локали en.json.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stepik_grader import cli, web
from stepik_grader.cli import interactive
from stepik_grader.core import user_settings


@pytest.fixture(autouse=True)
def _force_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_LANG", "en")


@pytest.fixture(autouse=True)
def _no_gui_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_pick_path_via_dialog", lambda *, want_dir: None)


def _failing_result() -> dict:
    return {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "errors": 0,
        "total_time": 0.0,
        "avg_time": 0.0,
        "peak_memory_mb": 0.0,
        "first_fail": 1,
        "cases": [{"passed": False, "verdict": "WA", "time": 0.0, "memory": 0.0, "error": ""}],
    }


def _passing_result() -> dict:
    return {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "total_time": 0.0,
        "avg_time": 0.0,
        "peak_memory_mb": 0.0,
        "first_fail": None,
        "cases": [{"passed": True, "verdict": "AC", "time": 0.0, "memory": 0.0, "error": ""}],
    }


def _make_solution(tmp_path: Path) -> Path:
    sol = tmp_path / "task1.py"
    sol.write_text("print(1)\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text("1", encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text("1", encoding="utf-8")
    return sol


# ---------------------------------------------------------------------------
# #445: цикл меню
# ---------------------------------------------------------------------------


def test_menu_loops_until_zero(tmp_path: Path, monkeypatch) -> None:
    """Меню зациклено: два прогона режима 1 подряд, затем «0» — выход."""
    sol = _make_solution(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: calls.append(solution))
    inputs = iter(["1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert calls == [sol, sol]


def test_menu_mode3_bad_path_continues_not_exits(tmp_path: Path, monkeypatch, capsys) -> None:
    """Ошибка пути в режиме 3 (continue) не выкидывает из меню — «0» ещё работает."""
    inputs = iter(["3", "/no/such/dir", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "Directory not found" in out
    assert "Goodbye" in out  # дошли до «0», а не выпали после ошибки


def test_eof_exits_gracefully(monkeypatch, capsys) -> None:
    """Конец потока ввода (EOF) — корректный выход без трейсбека."""

    def _raise_eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)
    cli._interactive_menu()
    assert "Goodbye" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #445: подсказка про файловый диалог + сообщение о подстановке дефолта
# ---------------------------------------------------------------------------


def test_path_prompt_shows_dialog_hint(tmp_path: Path, monkeypatch, capsys) -> None:
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "picker" in capsys.readouterr().out  # path_dialog_hint (en)


def test_ask_number_invalid_prints_default_message(monkeypatch, capsys) -> None:
    ctx = cli._build_cli_context()
    monkeypatch.setattr("builtins.input", lambda *a: "abc")
    result = interactive._ask_number("? ", default=15, ctx=ctx)
    assert result == 15
    out = capsys.readouterr().out
    assert "abc" in out and "15" in out  # input_default_used


def test_ask_number_empty_uses_default_silently(monkeypatch, capsys) -> None:
    ctx = cli._build_cli_context()
    monkeypatch.setattr("builtins.input", lambda *a: "")
    result = interactive._ask_number("? ", default=42, ctx=ctx)
    assert result == 42
    assert "42" not in capsys.readouterr().out  # пустой ввод — без сообщения


# ---------------------------------------------------------------------------
# #445: пункт «Веб-интерфейс»
# ---------------------------------------------------------------------------


def test_web_menu_item_launches_server(monkeypatch) -> None:
    called: list[dict] = []
    monkeypatch.setattr(web, "run_server", lambda **k: called.append(k))
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert len(called) == 1
    assert "record_history" in called[0]


def test_web_menu_item_survives_keyboard_interrupt(monkeypatch, capsys) -> None:
    """Ctrl+C в сервере возвращает в меню (не роняет процесс) — «0» ещё работает."""

    def _raise_kbd(**_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(web, "run_server", _raise_kbd)
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "Goodbye" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #430: тумблер истории (персистентность) + nudge
# ---------------------------------------------------------------------------


def test_history_toggle_on_persists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "enabled" in out
    settings = user_settings.load_settings(tmp_path / user_settings.SETTINGS_FILE_NAME)
    assert settings.record_history is True


def test_history_toggle_off_persists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(user_settings.UserSettings(record_history=True), path)
    inputs = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "disabled" in capsys.readouterr().out
    assert user_settings.load_settings(path).record_history is False


def test_toggle_on_then_mode_records_history(tmp_path: Path, monkeypatch) -> None:
    """После включения тумблера режим 1 вызывается с record_history=True."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    seen: list[bool] = []
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: seen.append(k["record_history"]))
    inputs = iter(["7", "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert seen == [True]


def test_toggle_on_then_web_passes_true(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[dict] = []
    monkeypatch.setattr(web, "run_server", lambda **k: called.append(k))
    inputs = iter(["7", "6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == [{"record_history": True}]


def test_nudge_after_fail_when_history_off(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" in capsys.readouterr().out  # nudge_enable_history


def test_no_nudge_when_all_pass(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" not in capsys.readouterr().out


def test_no_nudge_when_history_on(tmp_path: Path, monkeypatch, capsys) -> None:
    """История включена → nudge не печатается даже при падении."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(record_history=True),
        tmp_path / user_settings.SETTINGS_FILE_NAME,
    )
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" not in capsys.readouterr().out
