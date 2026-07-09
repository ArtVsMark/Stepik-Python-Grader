"""Tests for issue #71 — инкрементальный --watch через кэш #56.

`_resolve_use_cache` решает, включать ли кэш: явные --cache/--no-cache
выигрывают всегда; под --watch --mode 2 кэш включается по умолчанию (чтобы
на событие перезапускался только изменённый файл); иначе — дефолт из
pyproject. Плюс проверка проводки в main() для --mode 1/2 --watch.
"""

from __future__ import annotations

import argparse
import types

import pytest

from stepik_grader import cli


def _args(cache: bool | None) -> argparse.Namespace:
    return argparse.Namespace(cache=cache)


def test_explicit_cache_wins_over_incremental() -> None:
    assert cli._resolve_use_cache(_args(True), incremental=True) is True
    assert cli._resolve_use_cache(_args(True), incremental=False) is True


def test_explicit_no_cache_wins_even_under_watch() -> None:
    """--no-cache (args.cache=False) отключает кэш даже под инкрементальным watch."""
    assert cli._resolve_use_cache(_args(False), incremental=True) is False
    assert cli._resolve_use_cache(_args(False), incremental=False) is False


def test_incremental_enables_cache_when_unset() -> None:
    """Флаг не задан + incremental=True (--watch --mode 2) → кэш включён."""
    assert cli._resolve_use_cache(_args(None), incremental=True) is True


def test_unset_non_incremental_reads_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Флаг не задан, не incremental → дефолт из [tool.stepik-grader] use_cache.

    issue #119: _resolve_use_cache живёт в cli/options.py — CONFIG патчим
    там же, а не на facade `cli` (facade больше не держит своей копии имени).
    """
    monkeypatch.setattr(cli.options, "CONFIG", types.SimpleNamespace(use_cache=True))
    assert cli._resolve_use_cache(_args(None), incremental=False) is True
    monkeypatch.setattr(cli.options, "CONFIG", types.SimpleNamespace(use_cache=False))
    assert cli._resolve_use_cache(_args(None), incremental=False) is False


# ---------------------------------------------------------------------------
# main() wiring: --watch включает инкрементальный кэш для --mode 2, но не для 1
# ---------------------------------------------------------------------------


def test_main_watch_mode2_enables_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_mode_2(directory: str, *, verbose: bool, output: str, use_cache: bool) -> None:
        captured["use_cache"] = use_cache

    monkeypatch.setattr(cli, "_run_mode_2", fake_run_mode_2)
    monkeypatch.setattr(cli, "_watch_and_rerun", lambda path, rerun: rerun())

    cli.main(["--mode", "2", "--dir", ".", "--watch", "--lang", "en"])
    assert captured["use_cache"] is True


def test_main_watch_mode2_no_cache_opts_out(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_mode_2(directory: str, *, verbose: bool, output: str, use_cache: bool) -> None:
        captured["use_cache"] = use_cache

    monkeypatch.setattr(cli, "_run_mode_2", fake_run_mode_2)
    monkeypatch.setattr(cli, "_watch_and_rerun", lambda path, rerun: rerun())

    cli.main(["--mode", "2", "--dir", ".", "--watch", "--no-cache", "--lang", "en"])
    assert captured["use_cache"] is False


def test_main_watch_mode1_does_not_auto_enable_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Режим 1 — один файл; --watch не включает кэш автоматически (дефолт config)."""
    captured: dict[str, object] = {}

    def fake_run_mode_1(solution: str, *, verbose: bool, output: str, use_cache: bool) -> None:
        captured["use_cache"] = use_cache

    monkeypatch.setattr(cli, "_run_mode_1", fake_run_mode_1)
    monkeypatch.setattr(cli, "_watch_and_rerun", lambda path, rerun: rerun())
    monkeypatch.setattr(cli.options, "CONFIG", types.SimpleNamespace(use_cache=False))

    cli.main(["--mode", "1", "--file", "task.py", "--watch", "--lang", "en"])
    assert captured["use_cache"] is False
