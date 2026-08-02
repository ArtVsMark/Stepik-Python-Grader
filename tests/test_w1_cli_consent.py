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


class _AiConfig:
    """Настроенный провайдер с ДОПУСТИМЫМ адресом (issue #812).

    Адрес важен: с #812 недопустимая схема отсекается до гейта согласия, и
    конфиг-заглушка без ``ai_base_url`` не дошла бы до самого гейта.
    """

    ai_base_url = "http://localhost:11434/v1"  # локальный ollama — штатный путь
    ai_model = "test-model"


@pytest.fixture(autouse=True)
def _ai_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Провайдер настроен — чтобы гейт согласия был единственным барьером."""
    monkeypatch.setattr(commands.ai_hints, "is_configured", lambda _config: True)
    monkeypatch.setattr(commands, "get_config", _AiConfig)


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
    settings = user_settings.UserSettings(
        ai_hint_consent=True,
        # issue #812: согласие привязано к получателю — без совпадения адреса
        # его переспросят (и это отдельный тест ниже).
        ai_hint_consent_endpoint="http://localhost:11434",
    )
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


# ---------------------------------------------------------------------------
# Согласие привязано к получателю и отзывается — issue #812 (SECD-02/SECD-06)
# ---------------------------------------------------------------------------


def test_consent_stores_recipient(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Вместе с «да» запоминается, КОМУ оно дано."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    assert commands._resolve_ai_config() is not None

    saved = user_settings.load_settings(_settings_file(tmp_path))
    assert saved.ai_hint_consent_endpoint == "http://localhost:11434"


def test_consent_prompt_names_the_recipient(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Пользователю показывают адрес — иначе согласие не информированное."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "y")

    commands._resolve_ai_config()

    assert "http://localhost:11434" in capsys.readouterr().out


def test_changed_recipient_asks_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Согласие ollama не разрешает отправку на другой адрес.

    Это и была суть SECD-02: согласие было глобальным, поэтому «да» локальному
    провайдеру («данные не покидают машину») молча распространялось на любой
    адрес, который позже окажется в конфиге — а конфиг приезжает вместе с чужой
    папкой задач.
    """
    monkeypatch.chdir(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(
            ai_hint_consent=True, ai_hint_consent_endpoint="http://localhost:11434"
        ),
        _settings_file(tmp_path),
    )

    class _Remote:
        ai_base_url = "https://api.example.com/v1"
        ai_model = "m"

    monkeypatch.setattr(commands, "get_config", _Remote)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda *_: asked.append("asked") or "n")

    assert commands._resolve_ai_config() is None
    assert asked == ["asked"], "смена получателя обязана переспросить"


def test_same_recipient_does_not_ask_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь (/v1 → /v1beta) получателя не меняет — лишний вопрос не задаём."""
    monkeypatch.chdir(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(
            ai_hint_consent=True, ai_hint_consent_endpoint="http://localhost:11434"
        ),
        _settings_file(tmp_path),
    )

    class _SameHostOtherPath:
        ai_base_url = "http://localhost:11434/v1beta"
        ai_model = "m"

    monkeypatch.setattr(commands, "get_config", _SameHostOtherPath)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _forbid_input)

    assert commands._resolve_ai_config() is not None


def test_insecure_url_blocks_before_consent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """http на удалённый хост отсекается ДО вопроса — спрашивать не о чем."""
    monkeypatch.chdir(tmp_path)

    class _Insecure:
        ai_base_url = "http://evil.example/v1"
        ai_model = "m"

    monkeypatch.setattr(commands, "get_config", _Insecure)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _forbid_input)

    assert commands._resolve_ai_config() is None
    assert "не принимается" in capsys.readouterr().out


def test_revoke_clears_consent_and_recipient(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #812 (SECD-06): согласие можно отозвать, а не только править JSON."""
    monkeypatch.chdir(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(
            ai_hint_consent=True, ai_hint_consent_endpoint="http://localhost:11434"
        ),
        _settings_file(tmp_path),
    )

    assert commands.revoke_ai_consent() is True

    saved = user_settings.load_settings(_settings_file(tmp_path))
    assert saved.ai_hint_consent is None
    assert saved.ai_hint_consent_endpoint is None
    assert commands.revoke_ai_consent() is False  # отзывать больше нечего
