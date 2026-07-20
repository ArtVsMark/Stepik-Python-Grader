"""issue #630: CLI ``--ai-hints`` не должен слать код провайдеру без согласия.

Web-путь гейтит это с issue #543 (``403 consent_required``), CLI отправлял код
молча — приватность соблюдалась лишь в одном из двух путей. Согласие хранится в
общем ``.grader_settings.json`` (``ai_hint_consent``), поэтому данное однажды
согласие действует для обоих путей.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.cli import commands
from stepik_grader.core import user_settings


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Провайдер настроен — чтобы гейт согласия был единственным барьером."""
    monkeypatch.setattr(commands.ai_hints, "is_configured", lambda _config: True)
    monkeypatch.setattr(commands, "get_config", lambda: object())


def _settings_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / user_settings.SETTINGS_FILE_NAME


def _forbid_input(*_args: object) -> str:
    raise AssertionError("input() не должен вызываться")


def test_non_interactive_session_skips_ai_without_asking(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Без TTY согласие не запрашивается, подсказки пропускаются."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _forbid_input)

    assert commands._resolve_ai_config() is None
    assert "В сеть ничего не отправлено" in capsys.readouterr().out
    assert not _settings_file(tmp_path).exists()


def test_granted_consent_is_persisted_and_unlocks_hints(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Согласие спрашивается один раз и сохраняется в настройках."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    assert commands._resolve_ai_config() is not None

    saved = user_settings.load_settings(_settings_file(tmp_path))
    assert saved.ai_hint_consent is True


def test_declined_consent_blocks_hints_and_is_not_persisted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ не фиксируется — иначе передумать можно было бы только правкой JSON."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "n")

    assert commands._resolve_ai_config() is None

    saved = user_settings.load_settings(_settings_file(tmp_path))
    assert saved.ai_hint_consent is None, "отказ не должен залипать"


def test_existing_consent_does_not_ask_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уже данное согласие (в т.ч. через web) не переспрашивается."""
    monkeypatch.chdir(tmp_path)
    settings = user_settings.UserSettings(ai_hint_consent=True)
    user_settings.save_settings(settings, _settings_file(tmp_path))

    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _forbid_input)

    assert commands._resolve_ai_config() is not None


def test_eof_during_prompt_is_treated_as_refusal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+D / закрытый stdin — отказ, а не падение грейдера."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)

    def raise_eof(*_args: object) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    assert commands._resolve_ai_config() is None
