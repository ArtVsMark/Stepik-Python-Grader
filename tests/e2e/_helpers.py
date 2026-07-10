"""_helpers.py -- shared solution/test-case fixtures for tests/e2e/ (issue #263).

Not a test module itself (no ``test_`` prefix, so pytest never collects it).
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["write_task"]


def write_task(
    folder: Path,
    code: str,
    *,
    stdin: str = "4",
    expected: str = "5",
    filename: str = "task.py",
) -> Path:
    """Write a solution file + one legacy-format (``N``/``N.clue``) test case.

    Mirrors ``tests/test_web.py``'s ``_make_task`` helper -- one stdin/expected
    pair is enough to drive a real browser through the grading UI end to end.
    """
    sol = folder / filename
    sol.write_text(code, encoding="utf-8")
    tests_dir = folder / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "1").write_text(stdin, encoding="utf-8")
    (tests_dir / "1.clue").write_text(expected, encoding="utf-8")
    return sol
