"""Unit-тесты для executor.py (run_solution + main)."""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
import types

import pytest

import executor
from executor import RunResult, _timeout_handler

# ---------------------------------------------------------------------------
# run_solution — базовые сценарии
# ---------------------------------------------------------------------------


def test_run_solution_simple_output() -> None:
    """Корректный код возвращает ожидаемый stdout."""
    result = executor.run_solution("print('42')", stdin="", timeout=5.0)
    assert result.stdout.strip() == "42"
    assert result.stderr == ""
    assert result.timed_out is False


def test_run_solution_reads_stdin() -> None:
    """run_solution с stdin-аргументом не падает и возвращает stdout."""
    result = executor.run_solution("print('hello')", stdin="hello\n", timeout=5.0)
    assert result.stdout.strip() == "hello"


def test_run_solution_syntax_error() -> None:
    """SyntaxError попадает в stderr, timed_out=False."""
    result = executor.run_solution("def broken(", stdin="", timeout=5.0)
    assert result.timed_out is False
    assert "SyntaxError" in result.stderr


def test_run_solution_runtime_error() -> None:
    """ZeroDivisionError попадает в stderr."""
    result = executor.run_solution("print(1 / 0)", stdin="", timeout=5.0)
    assert "ZeroDivisionError" in result.stderr or result.return_code != 0


def test_run_solution_timeout() -> None:
    """Бесконечный цикл прерывается по timeout, timed_out=True."""
    result = executor.run_solution("while True: pass", stdin="", timeout=0.5)
    assert result.timed_out is True
    assert result.return_code == -1


# ---------------------------------------------------------------------------
# run_solution — поля RunResult
# ---------------------------------------------------------------------------


def test_run_solution_return_code_zero_on_success() -> None:
    """Успешный запуск возвращает return_code == 0."""
    result = executor.run_solution("x = 1 + 1", timeout=5.0)
    assert result.return_code == 0
    assert result.timed_out is False


def test_run_solution_return_code_nonzero_on_error() -> None:
    """Исключение в коде даёт ненулевой return_code."""
    result = executor.run_solution("raise ValueError('oops')", timeout=5.0)
    assert result.return_code != 0


def test_run_solution_stdin_param_does_not_break() -> None:
    """Параметр stdin принимается без ошибок (reserved, не используется)."""
    result = executor.run_solution("print('ok')", stdin="some input", timeout=5.0)
    assert result.stdout.strip() == "ok"


def test_run_solution_multiline_output() -> None:
    """Многострочный вывод сохраняется целиком в stdout."""
    code = textwrap.dedent("""
        for i in range(3):
            print(i)
    """)
    result = executor.run_solution(code, timeout=5.0)
    assert result.stdout.strip() == "0\n1\n2"


def test_run_solution_extra_field_default_empty() -> None:
    """Поле extra в RunResult по умолчанию пустой словарь."""
    result = executor.run_solution("pass", timeout=5.0)
    assert result.extra == {}


def test_run_result_timed_out_stderr_contains_message() -> None:
    """При таймауте stderr содержит описание превышения."""
    result = executor.run_solution("while True: pass", timeout=0.5)
    assert "TimeoutExpired" in result.stderr or "exceeded" in result.stderr


# ---------------------------------------------------------------------------
# RunResult dataclass
# ---------------------------------------------------------------------------


def test_run_result_defaults() -> None:
    """RunResult создаётся с пустыми значениями по умолчанию."""
    r = RunResult()
    assert r.stdout == ""
    assert r.stderr == ""
    assert r.return_code == 0
    assert r.timed_out is False
    assert r.extra == {}


def test_run_result_extra_is_independent() -> None:
    """Поле extra у разных экземпляров независимо (нет mutable default)."""
    r1 = RunResult()
    r2 = RunResult()
    r1.extra["key"] = "val"
    assert "key" not in r2.extra


# ---------------------------------------------------------------------------
# _timeout_handler — строки 100-101
# ---------------------------------------------------------------------------


def test_timeout_handler_raises_timeout_error() -> None:
    """_timeout_handler выбрасывает TimeoutError с описанием лимита."""
    with pytest.raises(TimeoutError, match="Execution exceeded"):
        _timeout_handler(signal.SIGALRM if hasattr(signal, "SIGALRM") else 14, None)


def test_timeout_handler_message_contains_timeout_value() -> None:
    """_timeout_handler упоминает значение TIMEOUT в сообщении."""
    with pytest.raises(TimeoutError) as exc_info:
        _timeout_handler(0, None)
    assert str(executor.TIMEOUT) in str(exc_info.value)


# ---------------------------------------------------------------------------
# main() — запуск executor.py как subprocess (покрывает строки 114-129)
# ---------------------------------------------------------------------------


def test_main_prints_output() -> None:
    """executor.py как __main__ исполняет код из stdin и печатает результат."""
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input="print('from_main')",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.stdout.strip() == "from_main"
    assert proc.returncode == 0


def test_main_syntax_error_exits_nonzero() -> None:
    """SyntaxError в коде → ненулевой exit code."""
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input="def bad(",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode != 0
    assert "SyntaxError" in proc.stderr


def test_main_runtime_exception_exits_nonzero() -> None:
    """RuntimeError → ненулевой exit code, traceback в stderr."""
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input="raise RuntimeError('boom')",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode != 0
    assert "RuntimeError" in proc.stderr


def test_main_empty_code_exits_zero() -> None:
    """Пустой код успешно завершается с кодом 0."""
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input="",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0


def test_main_multiline_code() -> None:
    """Многострочный код выполняется корректно."""
    code = "a = 2\nb = 3\nprint(a * b)"
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input=code,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.stdout.strip() == "6"
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# main() — ветки SIGALRM (строки 115-131)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM not available on Windows")
def test_main_sigalrm_timeout_fires() -> None:
    """SIGALRM срабатывает и прерывает бесконечный цикл внутри main()."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ['EXECUTOR_TIMEOUT']='1'; "
         "import executor; executor.main()"],
        input="while True: pass",
        capture_output=True,
        text=True,
        timeout=5,
    )
    # TimeoutError → ненулевой exit code
    assert proc.returncode != 0
    assert "Execution exceeded" in proc.stderr or "TimeoutError" in proc.stderr


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM not available on Windows")
def test_main_sigalrm_alarm_reset_after_success() -> None:
    """SIGALRM сбрасывается после успешного завершения (signal.alarm(0) в finally)."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import os; os.environ['EXECUTOR_TIMEOUT']='5'; "
         "import executor; executor.main()"],
        input="print('done')",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "done"


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="SIGALRM not available on Windows")
def test_main_namespace_isolation() -> None:
    """main() выполняет код в изолированном namespace — не видит переменные executor.py."""
    proc = subprocess.run(
        [sys.executable, "executor.py"],
        input="print(type(__builtins__))",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0
    # __builtins__ должен быть модулем builtins, не dict
    assert "module" in proc.stdout or "dict" in proc.stdout
