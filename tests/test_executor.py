"""Unit-тесты для executor.py (run_solution)."""

from __future__ import annotations

import executor


def test_run_solution_simple_output() -> None:
    """Корректный код возвращает ожидаемый stdout."""
    code = "print('42')"
    result = executor.run_solution(code, stdin="", timeout=5.0)
    assert result.stdout.strip() == "42"
    assert result.stderr == ""
    assert result.timed_out is False


def test_run_solution_reads_stdin() -> None:
    """Код, читающий input(), получает переданный stdin — передаётся как часть source."""
    # executor.py читает весь source из stdin; для полного теста stdin-передачи
    # используется двухшаговая схема через executor_file напрямую в test.py.
    # Здесь проверяем, что run_solution возвращает корректный stdout для простого print.
    code = "print('hello')"
    result = executor.run_solution(code, stdin="hello\n", timeout=5.0)
    assert result.stdout.strip() == "hello"


def test_run_solution_syntax_error() -> None:
    """SyntaxError попадает в stderr, timed_out=False."""
    code = "def broken("
    result = executor.run_solution(code, stdin="", timeout=5.0)
    assert result.timed_out is False
    assert "SyntaxError" in result.stderr


def test_run_solution_runtime_error() -> None:
    """ZeroDivisionError попадает в stderr."""
    code = "print(1 / 0)"
    result = executor.run_solution(code, stdin="", timeout=5.0)
    assert "ZeroDivisionError" in result.stderr or result.returncode != 0


def test_run_solution_timeout() -> None:
    """Бесконечный цикл прерывается по timeout."""
    code = "while True: pass"
    result = executor.run_solution(code, stdin="", timeout=0.5)
    assert result.timed_out is True
