"""Точка входа для `python -m stepik_grader` (issue #65).

Делегирует в ту же `cli.main()`, что и консольная команда `stepik-grader`
и `python -m stepik_grader.grader`.
"""

from __future__ import annotations

from stepik_grader.cli import main

if __name__ == "__main__":
    main()
