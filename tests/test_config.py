"""Тесты для config.py — единая конфигурация грейдера (Sprint 6.3)."""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from stepik_grader import cli, config
from stepik_grader.config import CONFIG, GraderConfig, load_config
from stepik_grader.core.runprofile import current_profile


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
    monkeypatch.setattr(config_module, "_find_config_source", lambda *a, **k: None)

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
        "ai_grounding_k",  # issue #812: сколько карточек в заземлении промпта
        "ai_system_prompt",  # issue #812: свой системный промпт вместо встроенного
        "history_db_path",  # issue #818: где лежит база истории обучения
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


# ---------------------------------------------------------------------------
# issue #830 (ARCH-04): конфиг читается в момент ВЫЗОВА, а не вмораживается
# в дефолты аргументов при импорте модуля.
#
# До правки `grader_core` читал CONFIG на импорте (`TIMEOUT_SECONDS`) и ставил
# снимок дефолтом аргумента — то есть значение фиксировалось при ОПРЕДЕЛЕНИИ
# функции. Ленивость config.py (PEP 562) на реальном пути обесценивалась:
# импорт grader_core сразу читал pyproject.toml, а поменять таймаут без
# перезагрузки модуля было нельзя.
# ---------------------------------------------------------------------------


@pytest.fixture
def _fresh_config(monkeypatch: pytest.MonkeyPatch):
    """Подменяет конфиг, который видит ``grader_core``, и возвращает установщик.

    Патчится именно ``grader_core.get_config``, а не ``config._cached_config``:
    соседний ``test_bare_import_does_not_read_pyproject_toml`` переимпортирует
    ``stepik_grader.config``, после чего в ``sys.modules`` лежит ДРУГОЙ объект
    модуля, и подмена кеша в нём до уже импортированного ``grader_core`` не
    доходит — тест зеленел бы или краснел в зависимости от порядка файлов.
    """
    from stepik_grader.core import grader_core

    def apply(**overrides: object) -> None:
        patched = dataclasses.replace(grader_core.get_config(), **overrides)  # type: ignore[arg-type]
        monkeypatch.setattr(grader_core, "get_config", lambda: patched)

    return apply


class TestConfigReadAtCallTime:
    def test_run_single_test_takes_current_timeout(self, _fresh_config, monkeypatch) -> None:
        """``run_single_test`` без явного timeout берёт АКТУАЛЬНЫЙ конфиг.

        Проверяется по ``RunSpec.timeout``, который уходит в runner: именно он
        решает, когда процесс решения будет убит.
        """
        from stepik_grader.core import grader_core
        from stepik_grader.core.runner import RunOutcome, RunSpec
        from stepik_grader.core.test_loader import TestCase

        seen: list[RunSpec] = []

        def spy(spec: RunSpec) -> RunOutcome:
            seen.append(spec)
            return RunOutcome()

        monkeypatch.setattr(grader_core, "run_spec", spy)
        _fresh_config(timeout_seconds=33.0)

        grader_core.run_single_test(
            pathlib.Path("solution.py"), TestCase(index=1, input_lines=[], expected_lines=[])
        )

        assert seen and seen[0].timeout == 33.0, (
            "таймаут взят снимком времени импорта, а не текущим конфигом"
        )

    def test_explicit_timeout_still_wins(self, _fresh_config, monkeypatch) -> None:
        """Явный аргумент важнее конфига — sentinel ``None`` не съел параметр."""
        from stepik_grader.core import grader_core
        from stepik_grader.core.runner import RunOutcome, RunSpec
        from stepik_grader.core.test_loader import TestCase

        seen: list[RunSpec] = []
        monkeypatch.setattr(
            grader_core,
            "run_spec",
            lambda spec: (seen.append(spec), RunOutcome())[1],
        )
        _fresh_config(timeout_seconds=33.0)

        grader_core.run_single_test(
            pathlib.Path("solution.py"),
            TestCase(index=1, input_lines=[], expected_lines=[]),
            timeout=2.5,
        )

        assert seen and seen[0].timeout == 2.5

    def test_module_constant_stays_for_back_compat(self) -> None:
        """Снимок ``TIMEOUT_SECONDS`` остаётся в модуле — на него ссылается фасад."""
        from stepik_grader.core import grader_core

        assert isinstance(grader_core.TIMEOUT_SECONDS, float)


# ---------------------------------------------------------------------------
# issue #993/#984 — детерминизм окружения: вердикт не зависит от того, что
# лежит в родительских каталогах и откуда запущен грейдер.
# ---------------------------------------------------------------------------


class TestConfigDeterminism:
    """Границы поиска конфига, устойчивость к битому TOML, явные источники."""

    @staticmethod
    def _write(path: pathlib.Path, text: str) -> pathlib.Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_foreign_pyproject_without_grader_section_is_skipped(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pyproject.toml без [tool.stepik-grader] — чужой, поиск идёт мимо него.

        Прежде выигрывал первый файл вверх по дереву, каким бы он ни был.
        """
        import stepik_grader.config as config_module

        self._write(tmp_path / "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
        self._write(
            tmp_path / "course" / "pyproject.toml",
            "[tool.stepik-grader]\ntimeout_seconds = 42.0\n",
        )
        nested = tmp_path / "course" / "task"
        nested.mkdir(parents=True)
        monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
        monkeypatch.chdir(nested)

        cfg = config_module.load_config()

        assert cfg.timeout_seconds == 42.0
        assert config_module.config_source() == tmp_path.resolve() / "course" / "pyproject.toml"

    def test_search_stops_at_project_boundary(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Выше границы проекта (.git) конфиг не читается — это чужая территория.

        Репро INS-4-01: ``timeout_seconds = 0.001`` этажом выше превращал верное
        решение в ``FAIL`` по таймауту.
        """
        import stepik_grader.config as config_module

        self._write(tmp_path / "pyproject.toml", "[tool.stepik-grader]\ntimeout_seconds = 0.001\n")
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        task = project / "task"
        task.mkdir()
        monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
        monkeypatch.chdir(task)

        assert config_module._find_pyproject(task) is None

    def test_broken_pyproject_above_cwd_warns_instead_of_raising(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Битый TOML в родительском каталоге — предупреждение, а не трейсбек.

        Репро INS-1-01: синтаксическая ошибка в чужом файле роняла любую
        команду, включая ``--help``.
        """
        import stepik_grader.config as config_module

        broken = self._write(
            tmp_path / "pyproject.toml", "[tool.stepik-grader\ntimeout_seconds = ??\n"
        )
        nested = tmp_path / "task"
        nested.mkdir()
        monkeypatch.delenv(config_module._ENV_CONFIG_PATH, raising=False)
        monkeypatch.chdir(nested)

        with pytest.warns(UserWarning, match="TOML") as warned:
            cfg = config_module.load_config()

        assert isinstance(cfg, config_module.GraderConfig)  # не исключение
        assert str(broken) in str(warned[0].message)  # путь назван
        assert config_module.config_source() != broken  # и не применён

    def test_broken_explicit_config_does_not_silently_pick_another(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Битый явный конфиг → дефолты, а не подстановка найденного по дереву."""
        import stepik_grader.config as config_module

        broken = self._write(tmp_path / "broken.toml", "[tool.stepik-grader\n")
        self._write(tmp_path / "pyproject.toml", "[tool.stepik-grader]\ntimeout_seconds = 33.0\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(config_module._ENV_CONFIG_PATH, str(broken))

        with pytest.warns(UserWarning, match="TOML"):
            cfg = config_module.load_config()

        assert cfg.timeout_seconds == config_module.GraderConfig().timeout_seconds

    def test_explicit_path_wins_over_env_and_search(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """set_config_path() (то есть --config) — источник высшего приоритета."""
        import stepik_grader.config as config_module

        chosen = self._write(
            tmp_path / "chosen.toml", "[tool.stepik-grader]\ntimeout_seconds = 7.0\n"
        )
        from_env = self._write(
            tmp_path / "env.toml", "[tool.stepik-grader]\ntimeout_seconds = 99.0\n"
        )
        self._write(tmp_path / "pyproject.toml", "[tool.stepik-grader]\ntimeout_seconds = 33.0\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(config_module._ENV_CONFIG_PATH, str(from_env))
        config_module.set_config_path(chosen)

        assert config_module.load_config().timeout_seconds == 7.0
        assert config_module.config_source() == chosen

    @pytest.mark.parametrize(
        "argv",
        [
            ["--mode", "1", "--config", "cfg.toml"],
            ["--config=cfg.toml", "--mode", "1"],
        ],
    )
    def test_config_flag_is_read_from_argv(self, argv: list[str]) -> None:
        """Обе формы флага разбираются до argparse — CONFIG связывается на импорте."""
        import stepik_grader.config as config_module

        found = config_module._path_from_argv(argv)

        assert found is not None and found.name == "cfg.toml"

    def test_workspace_root_stops_at_marker(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Корень настроек — ближайший каталог с маркером проекта."""
        import stepik_grader.config as config_module

        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        nested = project / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        assert config_module.workspace_root() == project.resolve()

    def test_explicit_workspace_root_wins(self, tmp_path: pathlib.Path) -> None:
        """--root у --serve задаёт корень настроек напрямую (issue #984)."""
        import stepik_grader.config as config_module

        config_module.set_workspace_root(tmp_path)

        assert config_module.workspace_root() == tmp_path.resolve()

    def test_same_verdict_from_nested_dir_with_foreign_pyproject(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Критерий приёмки #993: прогон из вложенной папки не зависит от соседей.

        Полный прогон подпроцессом (а не ``load_config()``): проверяется то, что
        видит пользователь, — вердикт. Чужой ``pyproject.toml`` этажом выше
        требует ``timeout_seconds = 0.001``; до фикса верное решение получало
        ``FAIL`` по таймауту, теперь граница проекта этот файл не пускает.
        """
        import json
        import subprocess
        import sys

        self._write(tmp_path / "pyproject.toml", "[tool.stepik-grader]\ntimeout_seconds = 0.001\n")
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        self._write(project / "tests" / "1", "4")
        self._write(project / "tests" / "1.clue", "5")
        self._write(project / "solution.py", "print(int(input()) + 1)\n")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "stepik_grader",
                "--mode",
                "1",
                "--file",
                "solution.py",
                "--output",
                "json",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        assert [case["passed"] for case in payload["cases"]] == [True]


# ---------------------------------------------------------------------------
# Лимиты задаются из CLI — issue #997 (SET-3-03)
# ---------------------------------------------------------------------------


class TestRuntimeLimitFlags:
    """--timeout / --memory-limit перекрывают pyproject.toml на один запуск.

    Значения лимитов правились только в ``[tool.stepik-grader]``, а у
    установки через pipx такого файла нет вовсе: единственным способом разово
    поднять таймаут было «заведите проект».
    """

    def _slow_task(self, tmp_path: pathlib.Path, seconds: float) -> pathlib.Path:
        task = tmp_path / "task1"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text(
            f"import time\ntime.sleep({seconds})\nprint(int(input()) * 2)\n", encoding="utf-8"
        )
        (task / "tests" / "1").write_text("5", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("10", encoding="utf-8")
        return task

    def _verdicts(self, capsys) -> list[str]:
        payload = json.loads(capsys.readouterr().out)
        return [case["verdict"] for case in payload["cases"]]

    def test_timeout_flag_makes_slow_solution_fail(self, tmp_path, monkeypatch, capsys):
        task = self._slow_task(tmp_path, 2.0)
        monkeypatch.chdir(tmp_path)

        cli.main(
            [
                "--mode",
                "1",
                "--file",
                str(task / "task1_1.py"),
                "--timeout",
                "0.5",
                "--output",
                "json",
            ]
        )

        assert self._verdicts(capsys) == ["TLE"]

    def test_timeout_flag_lets_slow_solution_pass(self, tmp_path, monkeypatch, capsys):
        """Тот же код с большим таймаутом проходит — флаг действительно применён."""
        task = self._slow_task(tmp_path, 1.0)
        monkeypatch.chdir(tmp_path)

        cli.main(
            [
                "--mode",
                "1",
                "--file",
                str(task / "task1_1.py"),
                "--timeout",
                "20",
                "--output",
                "json",
            ]
        )

        assert self._verdicts(capsys) == ["AC"]

    def test_timeout_is_visible_to_run_profile(self, tmp_path, monkeypatch, capsys):
        """Паспорт условий и ключ кэша обязаны видеть то же значение."""
        task = self._slow_task(tmp_path, 0.0)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--timeout", "7"])
        capsys.readouterr()

        assert current_profile().timeout_seconds == 7.0

    def test_memory_limit_zero_means_unlimited(self, tmp_path, monkeypatch, capsys):
        task = self._slow_task(tmp_path, 0.0)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--memory-limit", "0"])
        capsys.readouterr()

        assert current_profile().max_memory_mb is None

    def test_non_positive_timeout_is_rejected(self, tmp_path, monkeypatch):
        """Опечатка не должна тихо превращаться в «без таймаута»."""
        task = self._slow_task(tmp_path, 0.0)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit):
            cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--timeout", "0"])

    def test_override_config_rejects_unknown_field(self):
        with pytest.raises(ValueError):
            config.override_config(timeuot_seconds=1.0)
