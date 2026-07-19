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
    assert cfg.similar_threshold == 1.15
    assert cfg.much_slower_threshold == 1.50
    assert cfg.measure_child_memory is True
    assert cfg.microbench_max_cases == 5
    assert cfg.encoding == "utf-8"
    assert cfg.max_memory_mb == 1024
    assert cfg.use_cache is False
    assert cfg.glossary_store is None
    assert cfg.glossary_missing_queue == ".grader_glossary_missing.db"
    assert cfg.job_workers == 2
    assert cfg.record_stats is False
    assert cfg.sandbox_max_cpu_seconds == 10.0
    assert cfg.sandbox_max_processes == 32
    assert cfg.sandbox_max_output_bytes == 10 * 1024 * 1024


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


def test_load_config_missing_pyproject_returns_defaults(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отсутствие pyproject.toml → GraderConfig с дефолтами, без ошибок.

    Резолюция пути (issue #258) — env → поиск от cwd вверх → legacy
    (относительно __file__). Изолируем все три источника: env-переменная
    снята, cwd — пустой tmp_path (без родителей с pyproject.toml, поэтому
    используем корень диска), __file__ подменён на путь внутри tmp_path.
    """
    import stepik_grader.config as config_module

    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(empty_root)
    original_file = config_module.__file__
    try:
        config_module.__file__ = str(tmp_path / "src" / "stepik_grader" / "config.py")
        cfg = config_module.load_config()
        assert cfg == GraderConfig()
    finally:
        config_module.__file__ = original_file


def test_load_config_ignores_unknown_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключи в [tool.stepik-grader], которых нет в GraderConfig, молча игнорируются."""
    import stepik_grader.config as config_module

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.stepik-grader]\ntimeout_seconds = 20.0\nunknown_field = "ignored"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = config_module.load_config()
    assert cfg.timeout_seconds == 20.0
    assert cfg.microbench_max_cases == 5  # untouched default


# ---------------------------------------------------------------------------
# issue #258 — поиск pyproject.toml от cwd вверх, env-переопределение,
# graceful fallback (pipx/wheel install не находил конфиг пользователя)
# ---------------------------------------------------------------------------


def test_find_pyproject_searches_upward_from_nested_cwd(tmp_path: pathlib.Path) -> None:
    """_find_pyproject() находит pyproject.toml в родительской директории cwd."""
    from stepik_grader.config import _find_pyproject

    (tmp_path / "pyproject.toml").write_text("[tool.stepik-grader]\n", encoding="utf-8")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)

    found = _find_pyproject(nested)

    assert found == tmp_path / "pyproject.toml"


def test_find_pyproject_returns_none_when_absent(tmp_path: pathlib.Path) -> None:
    """_find_pyproject() возвращает None, если pyproject.toml нигде вверх по дереву нет."""
    from stepik_grader.config import _find_pyproject

    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()

    assert _find_pyproject(empty_root) is None


def test_load_config_finds_pyproject_via_cwd_search(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config() без env-переменной находит pyproject.toml поиском от cwd вверх."""
    import stepik_grader.config as config_module

    nested = tmp_path / "project" / "subdir"
    nested.mkdir(parents=True)
    (tmp_path / "project" / "pyproject.toml").write_text(
        "[tool.stepik-grader]\ntimeout_seconds = 42.0\n", encoding="utf-8"
    )
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(nested)

    cfg = config_module.load_config()

    assert cfg.timeout_seconds == 42.0


def test_env_var_takes_priority_over_cwd_search(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STEPIK_GRADER_CONFIG перекрывает поиск от cwd, даже если оба валидны."""
    import stepik_grader.config as config_module

    cwd_pyproject = tmp_path / "pyproject.toml"
    cwd_pyproject.write_text("[tool.stepik-grader]\ntimeout_seconds = 11.0\n", encoding="utf-8")
    env_pyproject = tmp_path / "elsewhere" / "pyproject.toml"
    env_pyproject.parent.mkdir()
    env_pyproject.write_text("[tool.stepik-grader]\ntimeout_seconds = 99.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config_module._ENV_CONFIG_PATH, str(env_pyproject))

    cfg = config_module.load_config()

    assert cfg.timeout_seconds == 99.0


def test_env_var_invalid_path_falls_back_gracefully(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Невалидный STEPIK_GRADER_CONFIG не поднимает исключение — идёт поиск от cwd."""
    import stepik_grader.config as config_module

    (tmp_path / "pyproject.toml").write_text(
        "[tool.stepik-grader]\ntimeout_seconds = 33.0\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config_module._ENV_CONFIG_PATH, str(tmp_path / "does_not_exist.toml"))

    cfg = config_module.load_config()

    assert cfg.timeout_seconds == 33.0


def test_legacy_fallback_used_when_search_and_env_find_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если env не задан и поиск от cwd ничего не нашёл — используется legacy-путь
    относительно __file__ (issue #35), сохраняя поведение при запуске из репозитория."""
    import stepik_grader.config as config_module

    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.setattr(config_module, "_find_pyproject", lambda *a, **k: None)

    cfg = config_module.load_config()

    assert cfg.timeout_seconds == 10.0  # реальный pyproject.toml репозитория


def test_module_level_config_singleton() -> None:
    """CONFIG — единственный экземпляр GraderConfig, вычисленный при импорте."""
    assert isinstance(CONFIG, GraderConfig)


# ---------------------------------------------------------------------------
# issue #143 — dataclasses.fields() вместо __dataclass_fields__
# ---------------------------------------------------------------------------


def test_dataclass_fields_matches_known_field_set() -> None:
    """Список полей GraderConfig зафиксирован явно — ловит случайный дрейф."""
    field_names = {f.name for f in dataclasses.fields(GraderConfig)}
    assert field_names == {
        "timeout_seconds",
        "similar_threshold",
        "much_slower_threshold",
        "measure_child_memory",
        "microbench_max_cases",
        "encoding",
        "max_memory_mb",
        "use_cache",
        "glossary_store",
        "glossary_missing_queue",
        "job_workers",
        "max_active_runs",
        "record_stats",
        "record_history",
        "insights_window_n",
        "insights_active_threshold_t",
        "insights_clean_streak_k",
        "sandbox_max_cpu_seconds",
        "sandbox_max_processes",
        "sandbox_max_output_bytes",
        "ai_base_url",
        "ai_model",
        "ai_api_key_env",
        "ai_max_tokens",
        "ai_timeout_seconds",
    }


def test_load_config_does_not_use_dunder_dataclass_fields() -> None:
    """load_config() не читает __dataclass_fields__ напрямую (issue #143)."""
    import inspect

    source = inspect.getsource(load_config)
    assert "__dataclass_fields__" not in source
    assert "dataclasses.fields" in source


# ---------------------------------------------------------------------------
# issue #142/#145 — ленивая загрузка config.CONFIG, отсутствие I/O при импорте
# ---------------------------------------------------------------------------


def test_bare_import_does_not_read_pyproject_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Голый import stepik_grader.config не трогает диск (issue #145)."""
    import importlib
    import sys
    import tomllib as tomllib_module

    load_calls: list[object] = []
    monkeypatch.setattr(tomllib_module, "load", lambda *a, **k: load_calls.append(1) or {})
    monkeypatch.delitem(sys.modules, "stepik_grader.config", raising=False)

    importlib.import_module("stepik_grader.config")

    assert load_calls == []


def test_config_attribute_is_lazy_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """CONFIG вычисляется при первом обращении и кэшируется (issue #142/#145)."""
    import stepik_grader.config as config_module

    monkeypatch.setattr(config_module, "_cached_config", None)
    load_calls: list[object] = []
    original_load_config = config_module.load_config

    def _tracking_load_config() -> GraderConfig:
        load_calls.append(1)
        return original_load_config()

    monkeypatch.setattr(config_module, "load_config", _tracking_load_config)

    first = config_module.CONFIG
    second = config_module.CONFIG

    assert first is second
    assert load_calls == [1]  # один реальный вызов load_config(), второй — из кэша


def test_get_config_returns_same_cached_instance() -> None:
    """get_config() и CONFIG дают один и тот же закэшированный объект."""
    import stepik_grader.config as config_module

    assert config_module.get_config() is config_module.CONFIG


def test_config_getattr_raises_for_unknown_name() -> None:
    """Module __getattr__ поднимает AttributeError для незнакомых имён."""
    import stepik_grader.config as config_module

    with pytest.raises(AttributeError):
        config_module.__getattr__("NOT_A_REAL_NAME")
