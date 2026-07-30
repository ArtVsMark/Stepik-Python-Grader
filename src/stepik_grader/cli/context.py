"""cli/context.py — явные зависимости для command/interactive handlers
(issues #120, #121 Phase 2).

Архитектурный слой: Application / CLI (leaf-модуль).

`CliContext` существует, чтобы handlers в `cli/commands.py`/`cli/interactive.py`
получали зависимости, которые тесты патчат через facade `stepik_grader.cli`
(`run_tests`, `run_benchmark`, `run_microbench_mode`,
`_resolve_test_dir_from_input`, `_print_tabular`, `_pick_path_via_dialog`,
`_ask_bench_profile`, `_ask_micro_profile`, `_run_mode_1..4`, `_t`), явно
параметром, а не читали их как module-global имена своего собственного
модуля — после переезда handlers в отдельные файлы такое чтение больше не
совпадало бы с namespace, который патчат тесты
(`monkeypatch.setattr(cli, "...", ...)`). Поля добавляются только для имён,
которые тесты действительно патчат напрямую через `cli.<name>` (подтверждено
grep по tests/, не предположение) — имена, которые лишь вызывают уже
пропущенную через контекст зависимость (например `_print_menu`/`_prompt_path`
в cli/interactive.py), остаются обычными same-module bare-вызовами.

Не импортирует `stepik_grader.cli` — фасад строит контекст сам
(`cli/__init__.py:_build_cli_context`), читая свои текущие global-имена
на каждый вызов, что и сохраняет late-binding monkeypatch-семантику.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["CliContext"]


@dataclass(frozen=True)
class CliContext:
    """Зависимости, которые handlers не должны резолвить сами."""

    t: Callable[..., str]
    run_tests: Callable[..., dict[str, Any]]
    run_benchmark: Callable[..., dict[str, Any]]
    # issue #729: пре-флайт перед замером скорости (режимы 3/4) — отдельной
    # зависимостью, чтобы тесты подменяли его так же, как остальные прогоны.
    preflight_solution: Callable[..., dict[str, Any]]
    run_microbench_mode: Callable[..., dict[Path, dict[str, Any]]]
    resolve_test_dir_from_input: Callable[..., Path | None]
    print_tabular: Callable[..., None]
    # issue #121 Phase 2: interactive-menu/prompt handlers.
    pick_path_via_dialog: Callable[..., Path | None]
    ask_bench_profile: Callable[[], int]
    ask_micro_profile: Callable[[], int]
    # Режимы 1/2 возвращают had_failures (issue #430 — меню решает про nudge);
    # режимы 3/4 (бенчмарки) — None.
    run_mode_1: Callable[..., bool]
    run_mode_2: Callable[..., bool]
    run_mode_3: Callable[..., None]
    run_mode_4: Callable[..., None]
    # issue #753: активная локаль (`cli._LANG`) — попадает в блок «Окружение»
    # обращения обратной связи. Не выводится из `t`, поэтому передаётся явно;
    # дефолт держит совместимость для вызовов, собирающих контекст частично.
    lang: str = "ru"
