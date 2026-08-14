"""Tests for core/settings_resolver.py — user-state поверх pyproject (issue #1136).

Ядро вкладки «Дополнительно»: настройки прогона живут в
``.grader_settings.json`` (у pipx-установки ``pyproject.toml`` нет вовсе),
ложатся поверх конфига проекта и умеют называть своё происхождение — без
этого персистентная настройка становится липкой и через месяц её автор не
помнит, что менял.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepik_grader import config
from stepik_grader.core import settings_resolver
from stepik_grader.core.user_settings import SETTINGS_FILE_NAME, load_settings


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Свой корень настроек и чистый кэш конфига на каждый тест."""
    monkeypatch.chdir(tmp_path)
    config.set_workspace_root(tmp_path)
    config.reset_config_cache()
    try:
        yield tmp_path
    finally:
        config.set_workspace_root(None)
        config.reset_config_cache()


def _write_user_settings(root: Path, **values: object) -> None:
    (root / SETTINGS_FILE_NAME).write_text(
        json.dumps({"run_settings": values}, ensure_ascii=False), encoding="utf-8"
    )


def _write_pyproject(root: Path, body: str) -> None:
    (root / "pyproject.toml").write_text(f"[tool.stepik-grader]\n{body}\n", encoding="utf-8")


class TestApplyUserRunSettings:
    def test_user_value_lands_on_config(self, _isolated_config: Path) -> None:
        _write_user_settings(_isolated_config, timeout_seconds=30.0)

        assert settings_resolver.apply_user_run_settings() == []
        assert config.get_config().timeout_seconds == 30.0

    def test_user_value_beats_pyproject(self, _isolated_config: Path) -> None:
        """Файл настроек — ступень МЕЖДУ флагом и проектом, а не под проектом."""
        _write_pyproject(_isolated_config, "timeout_seconds = 5.0")
        _write_user_settings(_isolated_config, timeout_seconds=30.0)
        config.reset_config_cache()

        settings_resolver.apply_user_run_settings()

        assert config.get_config().timeout_seconds == 30.0

    def test_untouched_settings_still_come_from_pyproject(self, _isolated_config: Path) -> None:
        """Правка проекта продолжает действовать для всего, чего не трогали.

        Это критерий приёмки #1136 и главный риск персистентных настроек:
        снимок «всех значений на сегодня» заморозил бы конфиг проекта навсегда.
        """
        _write_pyproject(_isolated_config, "timeout_seconds = 5.0\nmax_memory_mb = 256")
        _write_user_settings(_isolated_config, timeout_seconds=30.0)
        config.reset_config_cache()

        settings_resolver.apply_user_run_settings()

        assert config.get_config().timeout_seconds == 30.0
        assert config.get_config().max_memory_mb == 256

    def test_invalid_value_is_reported_not_applied(self, _isolated_config: Path) -> None:
        """Негодное значение отбрасывается тем же валидатором, что и pyproject."""
        _write_user_settings(_isolated_config, timeout_seconds=-1)

        rejected = settings_resolver.apply_user_run_settings()

        assert rejected == ["timeout_seconds"]
        assert config.get_config().timeout_seconds == config.GraderConfig().timeout_seconds

    def test_one_bad_value_does_not_block_the_others(self, _isolated_config: Path) -> None:
        _write_user_settings(_isolated_config, timeout_seconds=-1, max_memory_mb=256)

        rejected = settings_resolver.apply_user_run_settings()

        assert rejected == ["timeout_seconds"]
        assert config.get_config().max_memory_mb == 256

    def test_settings_outside_the_allowed_list_are_ignored(self, _isolated_config: Path) -> None:
        """Закрытый список: файл настроек не становится вторым конфигом проекта.

        `encoding` — тюнинг, которому место в pyproject.toml; вкладка его не
        предъявляет, и через файл он тоже не проходит.
        """
        _write_user_settings(_isolated_config, encoding="cp1251")

        settings_resolver.apply_user_run_settings()

        assert config.get_config().encoding == "utf-8"

    def test_empty_user_settings_change_nothing(self, _isolated_config: Path) -> None:
        assert settings_resolver.apply_user_run_settings() == []
        assert config.get_config() == config.GraderConfig()


class TestDescribeSetting:
    def test_default_origin(self, _isolated_config: Path) -> None:
        view = settings_resolver.describe_setting("timeout_seconds")

        assert view.origin == "default"
        assert view.value == config.GraderConfig().timeout_seconds

    def test_pyproject_origin(self, _isolated_config: Path) -> None:
        _write_pyproject(_isolated_config, "timeout_seconds = 5.0")
        config.reset_config_cache()

        view = settings_resolver.describe_setting("timeout_seconds")

        assert view.origin == "pyproject"
        assert view.value == 5.0

    def test_user_origin_and_inherited_value(self, _isolated_config: Path) -> None:
        """«Изменено вами» показывает и то, что вернётся после сброса."""
        _write_pyproject(_isolated_config, "timeout_seconds = 5.0")
        _write_user_settings(_isolated_config, timeout_seconds=30.0)
        config.reset_config_cache()

        view = settings_resolver.describe_setting("timeout_seconds")

        assert view.origin == "user"
        assert view.value == 30.0
        assert view.inherited == 5.0
        assert view.default == config.GraderConfig().timeout_seconds

    def test_origin_survives_applied_settings(self, _isolated_config: Path) -> None:
        """После применения «изменено вами» не превращается в «из pyproject.toml».

        Происхождение читается отдельным чтением файла проекта: активный конфиг
        к этому моменту уже содержит наложенный user-state, и по нему два
        источника неразличимы.
        """
        _write_user_settings(_isolated_config, timeout_seconds=30.0)
        settings_resolver.apply_user_run_settings()

        assert settings_resolver.describe_setting("timeout_seconds").origin == "user"

    def test_unknown_name_is_refused(self, _isolated_config: Path) -> None:
        """Опечатка не должна показывать в интерфейсе настройку, которой нет."""
        with pytest.raises(ValueError):
            settings_resolver.describe_setting("timeout_secondz")

    def test_setting_outside_the_list_is_refused(self, _isolated_config: Path) -> None:
        with pytest.raises(ValueError):
            settings_resolver.describe_setting("encoding")


class TestSetAndReset:
    def test_set_writes_and_applies(self, _isolated_config: Path) -> None:
        settings_resolver.set_user_run_setting("timeout_seconds", 30.0)

        stored = load_settings(_isolated_config / SETTINGS_FILE_NAME).run_settings
        assert stored == {"timeout_seconds": 30.0}
        settings_resolver.apply_user_run_settings()
        assert config.get_config().timeout_seconds == 30.0

    def test_set_refuses_invalid_value(self, _isolated_config: Path) -> None:
        """Отказ, а не тихое сохранение: иначе интерфейс показывает одно, прогон идёт по другому."""
        with pytest.raises(ValueError):
            settings_resolver.set_user_run_setting("timeout_seconds", -5)

        assert load_settings(_isolated_config / SETTINGS_FILE_NAME).run_settings == {}

    def test_set_refuses_setting_outside_the_list(self, _isolated_config: Path) -> None:
        with pytest.raises(ValueError):
            settings_resolver.set_user_run_setting("encoding", "cp1251")

    def test_set_keeps_other_settings(self, _isolated_config: Path) -> None:
        settings_resolver.set_user_run_setting("timeout_seconds", 30.0)
        settings_resolver.set_user_run_setting("max_memory_mb", 256)

        stored = load_settings(_isolated_config / SETTINGS_FILE_NAME).run_settings
        assert stored == {"timeout_seconds": 30.0, "max_memory_mb": 256}

    def test_reset_removes_the_key_not_freezes_it(self, _isolated_config: Path) -> None:
        """Сброс УДАЛЯЕТ ключ — иначе следующая правка проекта перестала бы действовать.

        Запись значения проекта вместо удаления заморозила бы снимок
        сегодняшнего дня: правишь pyproject.toml, а действует старое.
        """
        _write_pyproject(_isolated_config, "timeout_seconds = 5.0")
        settings_resolver.set_user_run_setting("timeout_seconds", 30.0)

        settings_resolver.reset_setting("timeout_seconds")

        raw = json.loads((_isolated_config / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
        assert raw["run_settings"] == {}
        config.reset_config_cache()
        settings_resolver.apply_user_run_settings()
        assert config.get_config().timeout_seconds == 5.0

    def test_reset_is_idempotent(self, _isolated_config: Path) -> None:
        """«Сбросить» жмут и на унаследованном значении, и повторно."""
        settings_resolver.reset_setting("timeout_seconds")
        settings_resolver.reset_setting("timeout_seconds")

        assert load_settings(_isolated_config / SETTINGS_FILE_NAME).run_settings == {}

    def test_reset_keeps_neighbours(self, _isolated_config: Path) -> None:
        settings_resolver.set_user_run_setting("timeout_seconds", 30.0)
        settings_resolver.set_user_run_setting("max_memory_mb", 256)

        settings_resolver.reset_setting("timeout_seconds")

        stored = load_settings(_isolated_config / SETTINGS_FILE_NAME).run_settings
        assert stored == {"max_memory_mb": 256}


def test_allowed_list_names_exist_in_config() -> None:
    """Каждое имя из списка — настоящее поле GraderConfig.

    Опечатка здесь означала бы контрол, который ничего не меняет: значение
    сохранится в файл и молча не доедет до прогона.
    """
    known = {field.name for field in __import__("dataclasses").fields(config.GraderConfig)}

    assert settings_resolver.USER_TUNABLE_SETTINGS <= known


def test_tuning_only_settings_stay_out_of_reach() -> None:
    """То, что постановка отнесла к тюнингу, вкладка не предъявляет."""
    for name in ("encoding", "glossary_store", "glossary_missing_queue", "similar_threshold"):
        assert name not in settings_resolver.USER_TUNABLE_SETTINGS
