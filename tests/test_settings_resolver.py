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


class TestAdvancedDescriptions:
    """Описания контролов вкладки: состав, группы, приведение ввода (issue #1136).

    Состав вкладки живёт в ядре, а не в окне, ровно ради этих тестов: на машине
    без дисплея (облачная сессия, любой job CI) проверить содержимое окна иначе
    нечем — а именно там оно и разъезжается незаметно.
    """

    def test_every_control_is_a_permitted_setting(self) -> None:
        """Контрол вне списка разрешённых сохранялся бы с отказом при нажатии."""
        for item in settings_resolver.advanced_settings():
            assert item.name in settings_resolver.USER_TUNABLE_SETTINGS

    def test_only_record_history_is_left_without_a_control(self) -> None:
        """Разрешено, но не показано — только то, что уже стоит на «Запуске».

        Иначе вкладка тихо теряет настройку: разрешить пользователю менять её и
        не дать контрола — это функция, о существовании которой знает лишь тот,
        кто читал исходники.
        """
        shown = {item.name for item in settings_resolver.advanced_settings()}

        assert settings_resolver.USER_TUNABLE_SETTINGS - shown == {"record_history"}

    def test_groups_cover_every_control(self) -> None:
        assert {item.group for item in settings_resolver.advanced_settings()} == set(
            settings_resolver.ADVANCED_GROUPS
        )

    def test_group_filter_keeps_declaration_order(self) -> None:
        names = [item.name for item in settings_resolver.advanced_settings("verdict")]

        assert names == ["compare_mode", "timeout_seconds", "max_memory_mb"]

    def test_sandbox_quotas_are_the_ones_behind_confirmation(self) -> None:
        """Подтверждения требует ровно то, что ослабляет изоляцию, и не больше.

        Лишняя настройка за галкой — лишнее трение; недостающая — снятая защита
        одним кликом.
        """
        unsafe = {item.name for item in settings_resolver.advanced_settings() if item.unsafe}

        assert unsafe == {
            "sandbox_max_cpu_seconds",
            "sandbox_max_processes",
            "sandbox_max_output_bytes",
        }

    def test_choice_control_offers_the_values_config_accepts(self) -> None:
        """Список в комбобоксе и список в валидаторе — одно и то же."""
        spec = settings_resolver.setting_spec("compare_mode")

        for value in spec.choices:
            assert config.validate_values({"compare_mode": value}) == []

    def test_unknown_name_has_no_spec(self) -> None:
        with pytest.raises(ValueError, match="недоступна"):
            settings_resolver.setting_spec("encoding")


class TestCoerceValue:
    """Ввод контрола → тип настройки: поле отдаёт строку, конфиг ждёт число."""

    def test_int_and_float_are_parsed(self) -> None:
        assert settings_resolver.coerce_value("job_workers", "4") == 4
        assert settings_resolver.coerce_value("timeout_seconds", "2.5") == 2.5

    def test_decimal_comma_is_understood(self) -> None:
        """В русской раскладке «2,5» набирается первым — отказ выглядел бы дефектом грейдера."""
        assert settings_resolver.coerce_value("timeout_seconds", "2,5") == 2.5

    def test_empty_nullable_means_no_limit(self) -> None:
        """Пусто у лимита памяти — «не ограничивать», и это валидное значение."""
        value = settings_resolver.coerce_value("max_memory_mb", "   ")

        assert value is None
        assert config.validate_values({"max_memory_mb": value}) == []

    def test_empty_non_nullable_is_refused(self) -> None:
        """У поля без «не задано» пустая строка — незаконченный ввод, а не выбор."""
        with pytest.raises(ValueError, match="пустое значение"):
            settings_resolver.coerce_value("job_workers", "")

    def test_garbage_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="job_workers"):
            settings_resolver.coerce_value("job_workers", "четыре")

    def test_ready_typed_value_passes_through(self) -> None:
        """Галка отдаёт настоящий bool — разбирать нечего."""
        assert settings_resolver.coerce_value("use_cache", True) is True

    def test_text_setting_keeps_its_string(self) -> None:
        assert settings_resolver.coerce_value("ai_model", " gpt-x ") == "gpt-x"


def test_every_control_has_labels_in_both_locales() -> None:
    """Подпись и объяснение — на каждую настройку и в ru, и в en.

    Ключи собираются из имени настройки (``setting_<name>``), поэтому гейт
    локалей их не видит: он ищет строковые литералы в вызовах каталога. Без
    этого теста добавленная настройка показывалась бы служебным
    идентификатором вместо подписи — молча и только в окне.
    """
    from stepik_grader.launcher import load_ui_messages

    for lang in ("ru", "en"):
        messages = load_ui_messages(lang)
        for item in settings_resolver.advanced_settings():
            assert messages.get(f"setting_{item.name}"), f"{lang}: нет подписи {item.name}"
            assert messages.get(f"setting_{item.name}_hint"), f"{lang}: нет пояснения {item.name}"
        for group in settings_resolver.ADVANCED_GROUPS:
            assert messages.get(f"settings_group_{group}"), f"{lang}: нет заголовка {group}"


def test_no_group_outgrows_a_single_screen() -> None:
    """Блок не должен разрастаться: вкладка рассчитана на экран без прокрутки.

    Прокрутка через Canvas дважды подвесила окно на macOS, поэтому её убрали, а
    настройки разложили по вкладкам-группам. Порог здесь и держит это решение:
    группа из десяти настроек вернула бы задачу «как показать длинный список»,
    а вместе с ней и соблазн вернуть Canvas.
    """
    for group in settings_resolver.ADVANCED_GROUPS:
        count = len(settings_resolver.advanced_settings(group))
        assert count <= 4, f"в блоке {group} уже {count} настроек — экрана не хватит"
