"""executor.py — запуск решений студентов в изолированном subprocess.

Public API:
    run_solution(source, stdin, timeout) -> RunResult

CLI entry point (используется test.py как subprocess):
    python executor.py  — читает код из stdin, выполняет в изолированном namespace.

Тайм-аут:
    Unix: SIGALRM (точный, внутри процесса).
    Windows: SIGALRM недоступен — защита обеспечивается через
             subprocess.run(timeout=...) в test.py (SUBPROCESS_TIMEOUT).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import types
from dataclasses import dataclass, field

# Тайм-аут в секундах (можно передать через переменную окружения)
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))

# Команда Python-интерпретатора
_PYTHON_CMD: str = "python3" if sys.platform in {"linux", "linux2", "darwin"} else "python"


@dataclass
class RunResult:
    """Результат запуска одного решения через run_solution()."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def run_solution(
    source_code: str,
    stdin: str = "",
    timeout: float = 10.0,
) -> RunResult:
    """Запустить source_code как дочерний процесс executor.py.

    Parameters
    ----------
    source_code:
        Исходный код Python для исполнения.
    stdin:
        Строка, передаваемая в stdin процесса.
    timeout:
        Тайм-аут в секундах. При превышении возвращает RunResult(timed_out=True).

    Returns
    -------
    RunResult с полями stdout, stderr, returncode, timed_out.
    """
    executor_path = str(os.path.join(os.path.dirname(__file__), "executor.py"))
    try:
        completed = subprocess.run(
            [_PYTHON_CMD, executor_path],
            input=source_code,
            stdin=subprocess.PIPE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        # stdin переданного решения идёт через source_code (executor читает код из stdin);
        # для передачи данных в input() внутри решения нужно использовать
        # двухшаговую схему: executor_file + отдельный stdin — это делает test.py.
        # run_solution — упрощённый API для тестов и диагностики.
        _ = stdin  # зарезервировано для будущего расширения
        return RunResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            stdout="",
            stderr=f"TimeoutExpired: exceeded {timeout}s",
            returncode=-1,
            timed_out=True,
        )


def _timeout_handler(_signum: int, _frame: object) -> None:
    """Обработчик сигнала SIGALRM — прерывает выполнение по тайм-ауту."""
    msg = f"Execution exceeded {TIMEOUT}s limit"
    raise TimeoutError(msg)


def main() -> None:
    """Читает код из stdin и выполняет его в изолированном namespace.

    Безопасность: executor.py запускается как дочерний subprocess из test.py,
    поэтому __builtins__ передаётся без ограничений — изоляция обеспечивается
    на уровне процесса, а не namespace.

    Тайм-аут на Unix: SIGALRM.
    На Windows: SIGALRM недоступен, защита от зависания обеспечивается
    через SUBPROCESS_TIMEOUT в test.py (на уровне subprocess.run).
    """
    source: str = sys.stdin.read()
    compiled: types.CodeType = compile(source, "<solution>", "exec")

    # Изолированный namespace — решение не видит globals executor.py
    namespace: dict[str, object] = {"__builtins__": __builtins__}

    # Тайм-аут только на Unix (Windows не поддерживает SIGALRM)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT)

    try:
        exec(compiled, namespace)  # noqa: S102
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)  # Сбросить таймер после завершения


if __name__ == "__main__":
    main()
