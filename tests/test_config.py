"""Тесты для config.py — единая конфигурация грейдера (Sprint 6.3)."""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from stepik_grader.config import CONFIG, GraderConfig, load_config


def test_grader_config_defaults() -> None:
    """GraderConfig() создаётся с задокументированными дефолтами."""
    cfg = GraderConfig()
    assert cfg.timeout_seconds == 10.0
    assert cfg.executor_timeout == 10
    assert cfg.similar_threshold == 1.15
    assert cfg.much_slower_threshold == 1.50
    assert cfg.measure_child_memory is True
    assert cfg.microbench_max_cases == 5
    assert cfg.encoding == "utf-8"
    assert cfg.max_memory_mb == 1024


def test_grader_config_is_frozen() -> None:
    """frozen=True — мутация поля выбрасывает FrozenInstanceError."""
    cfg = GraderConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.timeout_seconds = 99.0  # type: ignore[misc]


def test_load_config_reads_real_pyproject() -> None:
    """load_config() читает [tool.stepik-grader] из реального pyproject.toml."""
    cfg = load_config()
    assert isinstance(cfg, GraderConfig)
    assert cfg.timeout_seconds == 10.0


def test_load_config_missing_pyproject_returns_defaults(tmp_path: pathlib.Path) -> None:
    """Отсутствие pyproject.toml → GraderConfig с дефолтами, без ошибок.

    config.py резолвит путь к pyproject.toml относительно СВОЕГО __file__
    (три уровня вверх: src/stepik_grader/config.py -> repo root, Issue #35),
    а не cwd, поэтому подменяем __file__ модуля на путь внутри пустого
    tmp_path с той же вложенностью, чтобы резолвился именно tmp_path.
    """
    import stepik_grader.config as config_module

    original_file = config_module.__file__
    try:
        config_module.__file__ = str(tmp_path / "src" / "stepik_grader" / "config.py")
        cfg = config_module.load_config()
        assert cfg == GraderConfig()
    finally:
        config_module.__file__ = original_file


def test_load_config_ignores_unknown_keys(tmp_path: pathlib.Path) -> None:
    """Ключи в [tool.stepik-grader], которых нет в GraderConfig, молча игнорируются."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.stepik-grader]\ntimeout_seconds = 20.0\nunknown_field = "ignored"\n',
        encoding="utf-8",
    )
    import stepik_grader.config as config_module

    original_file = config_module.__file__
    try:
        config_module.__file__ = str(tmp_path / "src" / "stepik_grader" / "config.py")
        cfg = config_module.load_config()
        assert cfg.timeout_seconds == 20.0
        assert cfg.microbench_max_cases == 5  # untouched default
    finally:
        config_module.__file__ = original_file


def test_module_level_config_singleton() -> None:
    """CONFIG — единственный экземпляр GraderConfig, вычисленный при импорте."""
    assert isinstance(CONFIG, GraderConfig)
