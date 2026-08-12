"""Точка входа для `python -m stepik_grader` (issue #65).

Делегирует в ту же `cli.main()`, что и консольная команда `stepik-grader`
и `python -m stepik_grader.grader`.
"""

from __future__ import annotations

from stepik_grader.cli import run_cli

if __name__ == "__main__":
    # issue #936: код исхода прогона становится статусом процесса.
    run_cli()
