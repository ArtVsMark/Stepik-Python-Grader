import os
import signal
import sys
import types

# Тайм-аут в секундах (можно передать через переменную окружения)
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))


def _timeout_handler(_signum: int, _frame: object) -> None:
    """Обработчик сигнала SIGALRM — прерывает выполнение по тайм-ауту."""
    msg = f"Execution exceeded {TIMEOUT}s limit"
    raise TimeoutError(msg)


def main() -> None:
    """Читает код из stdin и выполняет его в изолированном namespace.

    Безопасность: executor.py запускается как дочерний subprocess из test.py,
    поэтому __builtins__ передаётся без ограничений — изоляция обеспечивается
    на уровне процесса, а не namespace.
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
