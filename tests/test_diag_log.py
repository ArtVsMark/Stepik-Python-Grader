"""Tests for core/diag_log.py — opt-in диагностический логгер (issue #146).

Ключевое — **редакция секретов** (docs/dev/logging.md § Редакция обязательна):
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


def _wait_terminal(job: object, *, timeout: float = 15.0) -> None:
    """Дождаться терминального статуса job'а web-слоя."""
    from stepik_grader.web import runs
    from tests._wait import wait_until

    def _done() -> bool | None:
        current = runs.get_job(job.id)  # type: ignore[attr-defined]
        assert current is not None
        return (
            True if current.to_status_dict()["status"] in ("done", "error", "cancelled") else None
        )

    assert wait_until(_done, timeout=timeout), "job не дошёл до терминального статуса"


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

    @pytest.mark.parametrize(
        "text,why",
        [
            ("StepikClient(access_token='ya29.TOKENVAL')", "repr вызова с одинарными кавычками"),
            ('StepikAPI(token="ya29.TOKENVAL")', "то же с двойными"),
            ("client_secret='TOKENVAL'", "не только token: любой ключ из списка"),
            ("вызов: get(url, params={'code': 'TOKENVAL'})", "внутри вложенной структуры"),
        ],
    )
    def test_quoted_assignment_redacted(self, text: str, why: str) -> None:
        """`key='value'` — форма traceback'а, которую пользователь копирует в форму (#964).

        Паттерн `key=value` требовал, чтобы после `=` шла НЕ кавычка, а
        JSON-паттерн — кавычек вокруг самого ключа. Между ними была щель ровно
        того размера, чтобы живой токен уехал в prefilled-URL публичного issue.
        """
        out = diag_log.redact(text)

        assert "TOKENVAL" not in out, why
        assert "***redacted***" in out

    def test_quoted_assignment_keeps_the_rest_of_the_line(self) -> None:
        """Маскируется значение, а не хвост строки: смешанные кавычки не съедают всё."""
        out = diag_log.redact("token='TOKENVAL' и дальше важный текст")

        assert "TOKENVAL" not in out
        assert "и дальше важный текст" in out

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

    # -- issue #813 (SECD-05): repr-форма и расширенный список ключей ----------

    def test_python_repr_dict_is_redacted(self) -> None:
        """`print(secrets)` в traceback'е даёт одинарные кавычки — раньше мимо.

        Такая строка попадает в поле «Логи» формы обратной связи и уезжает в
        prefilled-URL ПУБЛИЧНОГО GitHub issue: паттерн требовал двойных кавычек,
        то есть ловил JSON-ответ, но не Python-репрезентацию того же словаря.
        """
        out = diag_log.redact("secrets = {'access_token': 'ya29.SECRETVALUE'}")
        assert "ya29.SECRETVALUE" not in out
        assert "***redacted***" in out

    def test_api_key_is_redacted_in_both_quote_styles(self) -> None:
        """`api_key` не ловился ВООБЩЕ — ни в repr, ни в JSON.

        Найдено прогоном сверх формулировки находки: именно им настраивается
        AI-канал подсказок, то есть ключ живой, а не гипотетический.
        """
        assert "sk-SECRET-1" not in diag_log.redact("{'api_key': 'sk-SECRET-1'}")
        assert "sk-SECRET-2" not in diag_log.redact('{"api_key": "sk-SECRET-2"}')

    def test_basic_auth_header_is_redacted(self) -> None:
        out = diag_log.redact("Authorization: Basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNzd29yZA" not in out

    def test_neighbouring_fields_survive(self) -> None:
        """Маскируется ровно значение секрета, а не хвост строки до конца.

        Жадный паттерн схватил бы всё до последней кавычки — тогда из лога
        пропали бы соседние поля, по которым и ведётся диагностика.
        """
        out = diag_log.redact("{'client_id': 'cid', 'access_token': 'SECRETV1', 'x': 1}")
        assert "SECRETV1" not in out
        assert "'client_id': 'cid'" in out
        assert "'x': 1" in out

    def test_key_without_value_untouched(self) -> None:
        """`None` вместо значения — не секрет, трогать нечего."""
        assert diag_log.redact("{'api_key': None}") == "{'api_key': None}"

    def test_bare_keyword_in_prose_untouched(self) -> None:
        """Слово «password» в тексте — не повод портить сообщение."""
        text = "Проверьте password в настройках профиля."
        assert diag_log.redact(text) == text


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


def test_redact_thread_safe_under_concurrent_register() -> None:
    """issue #564: параллельные ``register_secret`` + ``redact`` не бросают
    'Set changed size during iteration'.

    До фикса ``redact`` итерировал ``_SECRETS`` напрямую, а между проверками
    выполнялся Python-код (``.replace``) — GIL мог переключиться на поток,
    делающий ``_SECRETS.add``, и следующий шаг итерации падал ``RuntimeError``.
    Снимок ``tuple(_SECRETS)`` строится атомарно, поэтому гонки нет. На старом
    коде этот тест краснел бы, на новом — стабильно зелёный.
    """
    import threading

    for i in range(100):  # затравка, чтобы redact реально обходил набор
        diag_log.register_secret(f"seedsecretvalue{i:04d}pad")

    stop = threading.Event()
    errors: list[BaseException] = []

    def _adder() -> None:
        # Непрерывная смена размера набора во время итерации redact. Держим
        # набор ограниченным (сброс при переполнении), иначе O(n) redact рос бы
        # неограниченно; сам сброс — тоже смена размера, усиливает проверку.
        i = 0
        while not stop.is_set():
            diag_log.register_secret(f"live-secret-value-{i:06d}")
            i += 1
            if len(diag_log._SECRETS) > 500:
                diag_log._SECRETS.clear()

    def _redactor() -> None:
        try:
            for _ in range(1000):
                diag_log.redact("log line with seedsecretvalue0001pad in it")
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop.set()

    adder = threading.Thread(target=_adder, daemon=True)
    adder.start()
    _redactor()
    adder.join(timeout=2.0)

    assert not errors, errors


class TestFailuresReachTheLog:
    """issue #831 (DEV-12): широкие ``except`` пишут стек в диагностический лог.

    Смысл ``core/diag_log.py`` — «диагностика с редакцией секретов», а форматтер
    здесь с самого начала умел редактировать трейсбек (``record.exc_text``).
    Но ни один широкий ``except`` в проекте им не пользовался: в лог попадали
    строки запросов, а момент падения — нет. Пользователь видел «❌ Ошибка
    обработки шага: 'steps'», и в баг-репорт (``core/feedback`` прикладывает
    логи) класть было нечего.
    """

    def test_web_job_failure_logs_traceback(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader.web import runs

        diag_log.configure_diagnostics("debug", log_dir=tmp_path)

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("сломался грейдинг")

        monkeypatch.setattr(runs, "grade_path", explode)
        solution = tmp_path / "task.py"
        solution.write_text("print(1)\n", encoding="utf-8")

        job = runs.submit_job("tests", solution, {})
        _wait_terminal(job)
        logging.getLogger("stepik_grader").handlers[0].flush()

        out = (tmp_path / "grader.log").read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in out, "стек не доехал до лога"
        assert "сломался грейдинг" in out

    def test_traceback_is_redacted_too(self, tmp_path: pathlib.Path) -> None:
        """Стек проходит ту же редакцию, что и сообщение — секрет из текста
        исключения в файл не попадает."""
        diag_log.configure_diagnostics("debug", log_dir=tmp_path)
        log = diag_log.get_logger("test")
        try:
            raise RuntimeError("Authorization: Bearer abc.def-ghi_123")
        except RuntimeError:
            log.exception("сбой")
        logging.getLogger("stepik_grader").handlers[0].flush()

        out = (tmp_path / "grader.log").read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in out
        assert "abc.def-ghi_123" not in out
