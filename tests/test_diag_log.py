"""Tests for core/diag_log.py — opt-in диагностический логгер (issue #146).

Ключевое — **редакция секретов** (docs/logging.md § Редакция обязательна):
токены/client_secret/Bearer никогда не попадают в лог. Плюс opt-in (по
умолчанию файл не создаётся), уровни и активация через env.
"""

from __future__ import annotations

import logging
import pathlib

import pytest

from stepik_grader.core import diag_log


@pytest.fixture(autouse=True)
def _reset_logger() -> object:
    """Сбросить состояние логгера и множество известных секретов вокруг теста."""
    diag_log._SECRETS.clear()
    yield
    diag_log.configure_diagnostics("off")
    diag_log._SECRETS.clear()


def _log_and_read(tmp_path: pathlib.Path, message: str, *args: object) -> str:
    diag_log.configure_diagnostics("debug", log_dir=tmp_path)
    diag_log.get_logger("test").debug(message, *args)
    logging.getLogger("stepik_grader").handlers[0].flush()
    return (tmp_path / "grader.log").read_text(encoding="utf-8")


class TestRedact:
    def test_bearer_header_redacted(self, tmp_path: pathlib.Path) -> None:
        out = _log_and_read(tmp_path, "Authorization: Bearer abc.def-ghi_123")
        assert "abc.def-ghi_123" not in out
        assert "Bearer ***redacted***" in out

    def test_query_token_params_redacted(self, tmp_path: pathlib.Path) -> None:
        out = _log_and_read(
            tmp_path, "GET https://stepik.org/oauth2/token/?access_token=SECRET1&code=SECRET2&x=1"
        )
        assert "SECRET1" not in out and "SECRET2" not in out
        assert "x=1" in out  # безобидные параметры остаются

    def test_json_token_fields_redacted(self, tmp_path: pathlib.Path) -> None:
        out = _log_and_read(
            tmp_path, 'ответ: {"access_token": "TOKENVAL", "refresh_token": "REFRESHVAL"}'
        )
        assert "TOKENVAL" not in out and "REFRESHVAL" not in out

    def test_registered_secret_redacted_anywhere(self, tmp_path: pathlib.Path) -> None:
        diag_log.register_secret("supersecretvalue12345")
        out = _log_and_read(tmp_path, "случайно логируем supersecretvalue12345 в тексте")
        assert "supersecretvalue12345" not in out
        assert "***redacted***" in out

    def test_short_value_not_registered(self) -> None:
        diag_log.register_secret("short")  # < 8 символов — не маскируем (шум)
        assert diag_log.redact("value short here") == "value short here"

    def test_plain_text_untouched(self) -> None:
        assert diag_log.redact("обычное сообщение без секретов") == "обычное сообщение без секретов"


class TestRedactTraceback:
    """issue #410 (S5): редакция распространяется на трейсбэк и stack_info.

    Прежде ``_RedactingFilter`` чистил только ``record.msg``; при
    ``exc_info=True`` секрет из текста исключения утекал в лог мимо редакции
    (``Formatter`` дописывает трейсбэк отдельно). Теперь фильтр готовит
    ``exc_text``/``stack_info`` уже отредактированными.
    """

    def test_secret_in_exception_message_redacted(self, tmp_path: pathlib.Path) -> None:
        """Секрет в тексте исключения не утекает через трейсбэк (exc_info=True)."""
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        try:
            raise ValueError("сбой: Authorization: Bearer leaky.token_value_here")
        except ValueError:
            diag_log.get_logger("test").debug("ошибка обмена", exc_info=True)
        logging.getLogger("stepik_grader").handlers[0].flush()
        out = (tmp_path / "grader.log").read_text(encoding="utf-8")
        assert "leaky.token_value_here" not in out
        assert "Bearer ***redacted***" in out
        assert "Traceback" in out and "ValueError" in out  # структура трейсбэка цела

    def test_registered_secret_in_traceback_redacted(self, tmp_path: pathlib.Path) -> None:
        """Зарегистрированный секрет маскируется и внутри трейсбэка."""
        diag_log.register_secret("registeredsecretvalue123")
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        try:
            raise RuntimeError("dump registeredsecretvalue123 inside")
        except RuntimeError:
            diag_log.get_logger("test").error("upstream failed", exc_info=True)
        logging.getLogger("stepik_grader").handlers[0].flush()
        out = (tmp_path / "grader.log").read_text(encoding="utf-8")
        assert "registeredsecretvalue123" not in out
        assert "***redacted***" in out

    def test_stack_info_redacted(self, tmp_path: pathlib.Path) -> None:
        """stack_info=True тоже проходит через редакцию (registered secret)."""
        diag_log.register_secret("stackleak987654")
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        diag_log.get_logger("test").debug("trace stackleak987654 here", stack_info=True)
        logging.getLogger("stepik_grader").handlers[0].flush()
        out = (tmp_path / "grader.log").read_text(encoding="utf-8")
        assert "stackleak987654" not in out
        assert "Stack (most recent call last)" in out  # сам stack_info записан


class TestConfigure:
    def test_off_creates_no_file(self, tmp_path: pathlib.Path) -> None:
        assert diag_log.configure_diagnostics("off", log_dir=tmp_path) is False
        diag_log.get_logger("x").warning("nope")
        assert not (tmp_path / "grader.log").exists()

    def test_unknown_level_disabled(self, tmp_path: pathlib.Path) -> None:
        assert diag_log.configure_diagnostics("garbage", log_dir=tmp_path) is False

    def test_debug_creates_file_and_writes(self, tmp_path: pathlib.Path) -> None:
        out = _log_and_read(tmp_path, "диагностическая строка")
        assert "диагностическая строка" in out
        assert "DEBUG stepik_grader.test" in out

    def test_env_var_activation(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STEPIK_GRADER_LOG", "info")
        assert diag_log.configure_diagnostics(None, log_dir=tmp_path) is True

    def test_env_off_by_default(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("STEPIK_GRADER_LOG", raising=False)
        assert diag_log.configure_diagnostics(None, log_dir=tmp_path) is False

    def test_idempotent_reconfigure_single_handler(self, tmp_path: pathlib.Path) -> None:
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        assert len(logging.getLogger("stepik_grader").handlers) == 1  # без накопления


def test_get_logger_naming() -> None:
    assert diag_log.get_logger("downloader").name == "stepik_grader.downloader"


def test_make_session_token_is_redacted(tmp_path: pathlib.Path) -> None:
    # интеграция #148: make_session регистрирует access_token → он маскируется,
    # даже если случайно попадёт в лог-сообщение.
    from stepik_grader.core.stepik_client import make_session

    diag_log.configure_diagnostics("debug", log_dir=tmp_path)
    make_session("realaccesstoken9999")
    diag_log.get_logger("test").debug("токен realaccesstoken9999 не должен утечь")
    logging.getLogger("stepik_grader").handlers[0].flush()
    assert "realaccesstoken9999" not in (tmp_path / "grader.log").read_text(encoding="utf-8")
