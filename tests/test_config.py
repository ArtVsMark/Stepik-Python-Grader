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
    assert cfg.max_output_bytes == 10 * 1024 * 1024  # issue #629


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
    """Ключи, которых нет в GraderConfig, игнорируются — но с предупреждением.

    Прежде опечатка в имени ключа проходила совсем молча: пользователь считал
    параметр настроенным, а грейдер работал на дефолте (issue #795).
    """
    import stepik_grader.config as config_module

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.stepik-grader]\ntimeout_seconds = 20.0\nunknown_field = "ignored"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.warns(UserWarning, match="unknown_field"):
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
        "max_output_bytes",
        "ai_base_url",
        "ai_model",
        "ai_api_key_env",
        "ai_max_tokens",
        "ai_timeout_seconds",
        "ai_max_hints",  # issue #812: потолок AI-вызовов за прогон
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


# ---------------------------------------------------------------------------
# issue #795 — валидация значений [tool.stepik-grader]: опечатка в конфиге
# должна называть параметр и допустимый диапазон, а не падать трейсбеком из
# недр раннера (proc.communicate(timeout="10")) или тихо ломать режим 4
# (microbench_max_cases = 0 → ValueError в _micro_stats).
# ---------------------------------------------------------------------------


def test_every_field_has_a_validation_rule() -> None:
    """Новое поле конфигурации не может остаться без правила проверки.

    Без этого теста валидация тихо разошлась бы с dataclass: поле добавили,
    правило забыли, и мусор в нём снова доезжает до раннера.
    """
    from stepik_grader.config import _RULES

    fields = {f.name for f in dataclasses.fields(GraderConfig)}
    assert fields == set(_RULES), f"без правила: {sorted(fields - set(_RULES))}"


def test_defaults_pass_validation() -> None:
    """Все дефолты сами проходят собственную проверку."""
    from stepik_grader.config import validate_values

    assert validate_values(dataclasses.asdict(GraderConfig())) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", "abc"),  # строка вместо числа — репро из аудита
        ("timeout_seconds", 0),  # нулевой таймаут не запускает ничего
        ("timeout_seconds", -1.0),
        ("similar_threshold", "1.15"),
        ("much_slower_threshold", 0),
        ("measure_child_memory", "yes"),
        ("microbench_max_cases", 0),  # репро из аудита: пустой cases_to_bench
        ("microbench_max_cases", 1.5),
        ("encoding", "utf8mb4"),  # такого кодека в Python нет
        ("encoding", ""),
        ("max_memory_mb", 0),
        ("max_memory_mb", "1024"),
        ("use_cache", 1),
        ("glossary_missing_queue", ""),
        ("job_workers", 0),
        ("job_workers", True),  # bool — подкласс int, но не число воркеров
        ("max_active_runs", -5),
        ("record_stats", "true"),
        ("record_history", None),
        ("insights_window_n", 0),
        ("insights_active_threshold_t", "2"),
        ("insights_clean_streak_k", 0),
        ("sandbox_max_cpu_seconds", 0),
        ("sandbox_max_processes", 0),
        ("sandbox_max_output_bytes", 0),
        ("max_output_bytes", -1),
        ("ai_api_key_env", ""),
        ("ai_max_tokens", 0),
        ("ai_timeout_seconds", -20.0),
        ("ai_base_url", 42),
    ],
)
def test_invalid_value_is_rejected_with_field_name(field: str, value: object) -> None:
    """Негодное значение → ValueError, называющий поле и допустимый диапазон."""
    with pytest.raises(ValueError) as exc:
        GraderConfig(**{field: value})
    message = str(exc.value)
    assert field in message
    assert "ожидается" in message and "получено" in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", 1),  # int вместо float — TOML пишет 10, не 10.0
        ("timeout_seconds", 0.001),
        ("max_memory_mb", None),  # осознанный «без лимита»
        ("glossary_store", None),
        ("glossary_store", "cards.db"),
        ("encoding", "cp1251"),
        ("microbench_max_cases", 1),
        ("job_workers", 1),
        ("ai_base_url", "http://localhost:11434/v1"),
    ],
)
def test_valid_boundary_value_passes(field: str, value: object) -> None:
    """Граничные, но осмысленные значения не отвергаются."""
    assert getattr(GraderConfig(**{field: value}), field) == value


def test_all_problems_are_reported_at_once() -> None:
    """Сообщение перечисляет все негодные поля, а не первое попавшееся."""
    with pytest.raises(ValueError) as exc:
        GraderConfig(timeout_seconds="abc", microbench_max_cases=0, job_workers=0)
    message = str(exc.value)
    assert "timeout_seconds" in message
    assert "microbench_max_cases" in message
    assert "job_workers" in message


def test_load_config_drops_invalid_value_and_keeps_the_rest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опечатка в значении не роняет грейдер: поле откатывается на дефолт.

    Пользовательский путь отличается от прямого конструирования намеренно —
    сломанный конфиг не должен делать инструмент незапускаемым, но и молчать
    о подмене нельзя.
    """
    import stepik_grader.config as config_module

    (tmp_path / "pyproject.toml").write_text(
        '[tool.stepik-grader]\ntimeout_seconds = "10"\nmicrobench_max_cases = 3\n',
        encoding="utf-8",
    )
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.warns(UserWarning, match="timeout_seconds") as warned:
        cfg = config_module.load_config()

    assert cfg.timeout_seconds == 10.0  # дефолт, а не строка "10"
    assert cfg.microbench_max_cases == 3  # соседний валидный ключ уцелел
    text = str(warned[0].message)
    assert "pyproject.toml" in text
    assert "число больше 0" in text


def test_load_config_reports_every_invalid_value(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Каждое отброшенное значение получает собственное предупреждение."""
    import stepik_grader.config as config_module

    (tmp_path / "pyproject.toml").write_text(
        "[tool.stepik-grader]\nmicrobench_max_cases = 0\njob_workers = 0\n",
        encoding="utf-8",
    )
    monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.warns(UserWarning) as warned:
        cfg = config_module.load_config()

    messages = " | ".join(str(w.message) for w in warned)
    assert "microbench_max_cases" in messages and "job_workers" in messages
    # Сравнение с классом ИЗ МОДУЛЯ: test_bare_import_does_not_read_pyproject_toml
    # переимпортирует stepik_grader.config, после чего файловый GraderConfig — уже
    # другой класс, а dataclass-__eq__ сравнивает только объекты одного класса.
    assert cfg == config_module.GraderConfig()
