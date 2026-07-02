"""Executor.py — запуск решений студентов в изолированном subprocess.

Public API:
    run_solution(source, stdin, timeout) -> RunResult

CLI entry point (используется grader.py как subprocess):
    python executor.py — читает код из stdin, выполняет в изолированном namespace.

Тайм-аут:
    Unix: SIGALRM (точный, внутри процесса).
    Windows: SIGALRM недоступен — защита обеспечивается через
             subprocess.run(timeout=...) в grader.py (SUBPROCESS_TIMEOUT).
"""

from __future__ import annotations

import builtins
import os
import pathlib
import signal
import subprocess
import sys
import types
from dataclasses import dataclass, field

# config.py resolves relative to the project root and isn't on sys.path when
# executor.py runs as a subprocess script (python core/executor.py sets
# sys.path[0] to core/, not the root) -- fall back to GraderConfig's own
# default (10) in that case, matching CONFIG.executor_timeout's own default.
try:
    from config import CONFIG

    _DEFAULT_EXECUTOR_TIMEOUT = CONFIG.executor_timeout
except ImportError:
    _DEFAULT_EXECUTOR_TIMEOUT = 10

# Тайм-аут в секундах: переменная окружения EXECUTOR_TIMEOUT имеет приоритет
# (нужна для тестов, см. tests/test_executor.py), иначе — CONFIG.executor_timeout
# (единая точка правды, Sprint 6.3; переопределяется через [tool.stepik-grader]
# в pyproject.toml).
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", str(_DEFAULT_EXECUTOR_TIMEOUT)))

# Команда Python-интерпретатора: тот же интерпретатор, что запустил grader
# (включая правильный venv на Windows, где "python"/"python3" может указать
# на системный Python вне активированного окружения).
_PYTHON_CMD: str = sys.executable


@dataclass
class RunResult:
    """Результат запуска одного решения через run_solution()."""

    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    timed_out: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def run_solution(
    source_code: str,
    stdin: str = "",
    timeout: float = 10.0,
) -> RunResult:
    """Запускает source_code как дочерний процесс executor.py.

    Parameters
    ----------
    source_code:
        Исходный код Python для исполнения.
    stdin:
        Строка, передаваемая в stdin процесса (зарезервировано для будущего расширения).
    timeout:
        Тайм-аут в секундах. При превышении возвращает RunResult(timed_out=True).

    Returns
    -------
    RunResult с полями stdout, stderr, return_code, timed_out.
    """
    executor_path = pathlib.Path(__file__).parent / "executor.py"
    try:
        completed = subprocess.run(
            [_PYTHON_CMD, str(executor_path)],
            input=source_code,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        _ = stdin  # зарезервировано для будущего расширения
        return RunResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            return_code=completed.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            stdout="",
            stderr=f"TimeoutExpired: exceeded {timeout}s",
            return_code=-1,
            timed_out=True,
        )


def _timeout_handler(_signum: int, _frame: object) -> None:
    """Обработчик сигнала SIGALRM — прерывает выполнение по тайм-ауту."""
    msg = f"Execution exceeded {TIMEOUT}s limit"
    raise TimeoutError(msg)


def main() -> None:
    """Читает код из stdin и выполняет его в изолированном namespace.

    Безопасность: executor.py запускается как дочерний subprocess из grader.py,
    поэтому __builtins__ передаётся без ограничений — изоляция обеспечивается
    на уровне процесса, а не namespace.

    Тайм-аут на Unix: SIGALRM.
    На Windows: SIGALRM недоступен, защита от зависания обеспечивается
    через SUBPROCESS_TIMEOUT в grader.py (на уровне subprocess.run).
    """
    source: str = sys.stdin.read()
    compiled: types.CodeType = compile(source, "<solution>", "exec")

    # Изолированный namespace — решение не видит globals executor.py.
    namespace: dict[str, object] = {"__builtins__": builtins}

    # Тайм-аут только на Unix (Windows не поддерживает SIGALRM).
    # pragma: no cover — платформо-зависимый блок, не выполняется на Windows.
    if hasattr(signal, "SIGALRM"):  # pragma: no cover
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT)

    try:
        exec(compiled, namespace)  # noqa: S102
    finally:
        if hasattr(signal, "SIGALRM"):  # pragma: no cover
            signal.alarm(0)  # Сбросить таймер после завершения


if __name__ == "__main__":
    main()
