"""Tests for пункт 9 интерактивного меню — обратная связь (issue #753, эпик #751).

Язык форсируется английским (как в test_menu_wave4.py) — ассерты на стабильные
подстроки локали en.json.

Ключевой инвариант: браузер не открывается без явного подтверждения, а
предпросмотр показывает ровно то, что уедет в форму.
"""

from __future__ import annotations

import pytest

from stepik_grader import cli
from stepik_grader.cli import interactive
from stepik_grader.core import feedback


@pytest.fixture(autouse=True)
def _force_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_LANG", "en")


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Перехватить webbrowser.open — тест не должен открывать реальный браузер."""
    urls: list[str] = []
    monkeypatch.setattr(interactive.webbrowser, "open", lambda url: urls.append(url) or True)
    return urls


def _feed(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    inputs = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))


def test_menu_shows_feedback_item(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Пункт 9 виден в меню — канал обратной связи находится без документации."""
    _feed(monkeypatch, "0")
    cli._interactive_menu()
    assert "Report a problem" in capsys.readouterr().out


def test_bug_flow_opens_prefilled_url(
    monkeypatch: pytest.MonkeyPatch, capsys, opened: list[str]
) -> None:
    """Баг: описание + автособранное окружение уезжают в форму bug_report.yml."""
    _feed(monkeypatch, "9", "1", "crashes on empty input", "y", "0")
    cli._interactive_menu()

    assert len(opened) == 1
    assert "template=bug_report.yml" in opened[0]
    assert "what-happened" in opened[0]
    out = capsys.readouterr().out
    assert "Environment" in out  # предпросмотр печатает подпись поля
    assert opened[0] in out  # ссылка видна даже если браузер не открылся


def test_empty_confirm_means_yes(
    monkeypatch: pytest.MonkeyPatch, capsys, opened: list[str]
) -> None:
    """Enter на «[Y/n]» — согласие (Y — дефолт)."""
    _feed(monkeypatch, "9", "2", "add dark theme", "", "0")
    cli._interactive_menu()
    assert len(opened) == 1
    assert "template=idea.yml" in opened[0]


def test_decline_opens_nothing(monkeypatch: pytest.MonkeyPatch, capsys, opened: list[str]) -> None:
    """Отказ на подтверждении — ничего не открывается и не отправляется."""
    _feed(monkeypatch, "9", "1", "whatever", "n", "0")
    cli._interactive_menu()

    assert opened == []
    assert "Cancelled" in capsys.readouterr().out


def test_zero_returns_to_menu(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    """«0» в выборе типа — тихий возврат в меню, без вопросов и браузера."""
    _feed(monkeypatch, "9", "0", "0")
    cli._interactive_menu()
    assert opened == []


def test_garbage_kind_returns_to_menu(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    """Мусор вместо типа обращения не роняет меню и ничего не открывает."""
    _feed(monkeypatch, "9", "42", "0")
    cli._interactive_menu()
    assert opened == []


def test_task_problem_asks_step_url(
    monkeypatch: pytest.MonkeyPatch, capsys, opened: list[str]
) -> None:
    """Для «задача проверяется неправильно» спрашивается ссылка на шаг Stepik."""
    step = "https://stepik.org/lesson/1/step/2"
    _feed(monkeypatch, "9", "3", "verdict differs", step, "y", "0")
    cli._interactive_menu()

    assert len(opened) == 1
    assert "template=task_problem.yml" in opened[0]
    assert "step-url" in opened[0]
    assert step in capsys.readouterr().out


def test_empty_summary_still_prefills_environment(
    monkeypatch: pytest.MonkeyPatch, opened: list[str]
) -> None:
    """Пустое описание — форма всё равно несёт окружение (его руками не пишут)."""
    _feed(monkeypatch, "9", "1", "", "y", "0")
    cli._interactive_menu()

    assert len(opened) == 1
    assert "environment" in opened[0]


def test_browser_failure_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, capsys, opened: list[str]
) -> None:
    """Отсутствие браузера (headless) — сообщение и ссылка, а не трейсбек в меню."""

    def _boom(url: str) -> bool:
        raise OSError("no browser")

    monkeypatch.setattr(interactive.webbrowser, "open", _boom)
    _feed(monkeypatch, "9", "1", "crash", "y", "0")
    cli._interactive_menu()

    out = capsys.readouterr().out
    assert "Could not open the browser" in out
    assert f"{feedback.REPO_URL}/issues/new?" in out


def test_keyboard_interrupt_returns_to_menu(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Ctrl+C внутри flow возвращает в меню, а не роняет процесс — «0» ещё работает."""
    answers = iter(["9", "1"])

    def _input(*_args: object) -> str:
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr("builtins.input", _input)
    # Второй проход меню упирается в KeyboardInterrupt на выборе режима — он не
    # подавляется на верхнем уровне, значит меню дожило до следующей итерации.
    with pytest.raises(KeyboardInterrupt):
        cli._interactive_menu()
    assert "Feedback" in capsys.readouterr().out


def test_secrets_never_reach_the_url(monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> None:
    """Токен, вставленный пользователем в описание, редактируется до сборки URL."""
    _feed(monkeypatch, "9", "1", "failed with Bearer abcdef1234567890", "y", "0")
    cli._interactive_menu()

    assert len(opened) == 1
    assert "abcdef1234567890" not in opened[0]
