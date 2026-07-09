"""cli/context.py — явные зависимости для command handlers (issue #120).

Архитектурный слой: Application / CLI (leaf-модуль).

`CliContext` существует, чтобы handlers в `cli/commands.py` получали
зависимости, которые тесты патчат через facade `stepik_grader.cli`
(`run_tests`, `run_benchmark`, `run_microbench_mode`,
`_resolve_test_dir_from_input`, `_print_tabular`, `_t`), явно параметром,
а не читали их как module-global имена своего собственного модуля —
после переезда handlers в отдельный файл такое чтение больше не совпадало
бы с namespace, который патчат тесты (`monkeypatch.setattr(cli, "...", ...)`).
Не импортирует `stepik_grader.cli` — фасад строит контекст сам
(`cli/__init__.py:_build_cli_context`), читая свои текущие global-имена
на каждый вызов, что и сохраняет late-binding monkeypatch-семантику.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["CliContext"]


@dataclass(frozen=True)
class CliContext:
    """Зависимости, которые command handlers не должны резолвить сами."""

    t: Callable[..., str]
    run_tests: Callable[..., dict[str, Any]]
    run_benchmark: Callable[..., dict[str, Any]]
    run_microbench_mode: Callable[..., dict[str, dict[str, Any]]]
    resolve_test_dir_from_input: Callable[..., str | None]
    print_tabular: Callable[..., None]
