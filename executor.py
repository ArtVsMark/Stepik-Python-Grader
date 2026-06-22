import sys
import signal
import os

# Тайм-аут в секундах (можно передать через переменную окружения)
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))


def _timeout_handler(_signum: int, _frame: object) -> None:
    """Обработчик сигнала SIGALRM — прерывает выполнение по тайм-ауту."""
    raise TimeoutError(f"Execution exceeded {TIMEOUT}s limit")


def main() -> None:
    """Читает код из stdin и выполняет его в изолированном namespace."""
    source: str = sys.stdin.read()
    compiled = compile(source, "<solution>", "exec")

    # Изолированный namespace — решение не видит globals executor.py
    namespace: dict = {"__builtins__": __builtins__}

    # Тайм-аут только на Unix (Windows не поддерживает SIGALRM)
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(TIMEOUT)

    try:
        exec(compiled, namespace)
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)  # Сбросить таймер после завершения


if __name__ == "__main__":
    main()
