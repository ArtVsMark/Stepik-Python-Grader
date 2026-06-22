from __future__ import annotations

import subprocess
import sys
import pathlib

EXECUTOR = str(pathlib.Path(__file__).parent.parent / "executor.py")
PYTHON = sys.executable


def _run(code: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, EXECUTOR],
        input=code,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_executor_simple_print() -> None:
    result = _run("print('hello')")
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_executor_syntax_error() -> None:
    result = _run("def broken(:")
    assert result.returncode != 0


def test_executor_runtime_error() -> None:
    result = _run("raise ValueError('oops')")
    assert result.returncode != 0


def test_executor_empty_code() -> None:
    result = _run("")
    assert result.returncode == 0


def test_executor_math() -> None:
    result = _run("print(2 ** 10)")
    assert result.returncode == 0
    assert "1024" in result.stdout
