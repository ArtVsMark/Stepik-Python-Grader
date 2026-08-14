"""Tests for core/user_settings.py — персистентные user-настройки CLI (issue #430).

Слой отдельный от config.py (frozen, только pyproject): хранит переключаемые из
меню настройки в .grader_settings.json. Здесь — round-trip, best-effort загрузка
и атомарная запись.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from stepik_grader import atomic_io
from stepik_grader.core import user_settings
from stepik_grader.core.user_settings import (
    SETTINGS_FILE_NAME,
    UserSettings,
    load_settings,
    save_fields,
    save_settings,
)


def test_existing_folder_file_wins(tmp_path: Path, monkeypatch) -> None:
    """Накопленная папочная раскладка продолжает работать (issue #948).

    ``.grader_settings.json`` рядом с задачами — это ещё и маркер корня
    проекта, поэтому существующий файл остаётся хозяином положения: обновление
    грейдера не должно уводить настройки из-под ног.
    """
    monkeypatch.delenv(user_settings.SETTINGS_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / user_settings.SETTINGS_FILE_NAME).write_text("{}", encoding="utf-8")

    assert user_settings.default_settings_path() == tmp_path / user_settings.SETTINGS_FILE_NAME


def test_default_settings_path_follows_given_root(tmp_path: Path, monkeypatch) -> None:
    """issue #984: корень настроек передаётся явно — CLI и веб читают один файл.

    Прежде CLI жёстко брал ``cwd``, а веб — ``--root``: один запуск имел два
    разных корня настроек, и тумблеры (история, согласие на AI) расходились.
    """
    monkeypatch.delenv(user_settings.SETTINGS_ENV_VAR, raising=False)
    nested = tmp_path / "task"
    nested.mkdir()
    monkeypatch.chdir(nested)
    (tmp_path / user_settings.SETTINGS_FILE_NAME).write_text("{}", encoding="utf-8")

    assert (
        user_settings.default_settings_path(tmp_path) == tmp_path / user_settings.SETTINGS_FILE_NAME
    )


def test_without_folder_file_settings_are_user_wide(tmp_path: Path, monkeypatch) -> None:
    """Без папочного файла настройки общие — как и база истории (issue #948).

    Тумблер записи истории управляет ГЛОБАЛЬНОЙ ``~/.stepik-grader/history.db``
    (#818), а сам жил в папке: включил в каталоге одной задачи — из соседней
    прогоны в «Прогресс» не попадали, и наоборот, выключил в одной — из другой
    писалось в ту же общую базу.
    """
    monkeypatch.delenv(user_settings.SETTINGS_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert user_settings.default_settings_path() == user_settings.user_settings_path()
    assert user_settings.user_settings_path().parent.name == user_settings.USER_SETTINGS_DIR


def test_env_var_overrides_both(tmp_path: Path, monkeypatch) -> None:
    """``STEPIK_GRADER_SETTINGS`` сильнее папочного файла — как у истории (issue #948)."""
    chosen = tmp_path / "elsewhere" / "settings.json"
    monkeypatch.setenv(user_settings.SETTINGS_ENV_VAR, str(chosen))
    monkeypatch.chdir(tmp_path)
    (tmp_path / user_settings.SETTINGS_FILE_NAME).write_text("{}", encoding="utf-8")

    assert user_settings.default_settings_path() == chosen


def test_toggle_in_one_folder_is_seen_from_another(tmp_path: Path, monkeypatch) -> None:
    """Главный сценарий issue #948: включил в каталоге A — видно из каталога B.

    До фикса запись уходила в ``A/.grader_settings.json``, и из B настройка
    читалась как «не переопределена»: прогоны молча не писались в общую базу.
    """
    home = tmp_path / "home"
    monkeypatch.delenv(user_settings.SETTINGS_ENV_VAR, raising=False)
    monkeypatch.setattr(user_settings.Path, "home", staticmethod(lambda: home))
    folder_a = tmp_path / "course" / "a"
    folder_b = tmp_path / "course" / "b"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)

    monkeypatch.chdir(folder_a)
    save_fields(user_settings.default_settings_path(), record_history=True)

    monkeypatch.chdir(folder_b)
    assert load_settings(user_settings.default_settings_path()).record_history is True


def test_load_absent_returns_defaults(tmp_path: Path) -> None:
    settings = user_settings.load_settings(tmp_path / "nope.json")
    assert settings == UserSettings()
    assert settings.record_history is None


def test_save_then_load_roundtrip_true(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(record_history=True), path)
    assert user_settings.load_settings(path).record_history is True


def test_save_then_load_roundtrip_false(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(record_history=False), path)
    assert user_settings.load_settings(path).record_history is False


def test_save_omits_none_fields(tmp_path: Path) -> None:
    """None-поле (не переопределено) не пишется — файл не фиксирует наследуемое."""
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(record_history=None), path)
    assert "record_history" not in path.read_text(encoding="utf-8")


def test_save_is_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(record_history=True), path)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_load_corrupt_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    path.write_text("{ not valid json", encoding="utf-8")
    assert user_settings.load_settings(path) == UserSettings()


def test_load_non_dict_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert user_settings.load_settings(path) == UserSettings()


def test_load_wrong_type_field_ignored(tmp_path: Path) -> None:
    """record_history не bool (напр. строка) → трактуем как «не задано» (None)."""
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    path.write_text('{"record_history": "yes"}', encoding="utf-8")
    assert user_settings.load_settings(path).record_history is None


def test_load_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    path.write_text('{"record_history": true, "future_key": 42}', encoding="utf-8")
    assert user_settings.load_settings(path).record_history is True


def test_onboarding_seen_roundtrip(tmp_path: Path) -> None:
    """issue #660: флаг закрытия стартового экрана переживает запись/чтение."""
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(onboarding_seen=True), path)
    assert user_settings.load_settings(path).onboarding_seen is True


def test_onboarding_seen_omitted_when_none(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(UserSettings(onboarding_seen=None), path)
    assert "onboarding_seen" not in path.read_text(encoding="utf-8")


def test_onboarding_seen_wrong_type_ignored(tmp_path: Path) -> None:
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    path.write_text('{"onboarding_seen": "yes"}', encoding="utf-8")
    assert user_settings.load_settings(path).onboarding_seen is None


def test_settings_fields_are_independent(tmp_path: Path) -> None:
    """issue #660: onboarding_seen пишется/читается рядом с record_history и
    ai_hint_consent, не затирая их (все три — независимые опт-ины)."""
    path = tmp_path / user_settings.SETTINGS_FILE_NAME
    user_settings.save_settings(
        UserSettings(record_history=True, ai_hint_consent=True, onboarding_seen=True), path
    )
    loaded = user_settings.load_settings(path)
    assert loaded.record_history is True
    assert loaded.ai_hint_consent is True
    assert loaded.onboarding_seen is True


# ---------------------------------------------------------------------------
# Запись по полю, а не снапшотом — issue #997 (SET-2-02, CNC-5-01, CNC-5-04)
# ---------------------------------------------------------------------------


def test_save_fields_writes_only_named_field(tmp_path: Path) -> None:
    """Соседние ключи переживают запись одного флага."""
    path = tmp_path / SETTINGS_FILE_NAME
    path.write_text(
        json.dumps({"ai_hint_consent": True, "ai_hint_consent_endpoint": "http://x"}),
        encoding="utf-8",
    )

    save_fields(path, record_history=True)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "ai_hint_consent": True,
        "ai_hint_consent_endpoint": "http://x",
        "record_history": True,
    }


def test_save_fields_none_erases_key(tmp_path: Path) -> None:
    """None — явное стирание: так отзывается согласие на AI-подсказки."""
    path = tmp_path / SETTINGS_FILE_NAME
    path.write_text(json.dumps({"ai_hint_consent": True, "record_history": True}), encoding="utf-8")

    save_fields(path, ai_hint_consent=None)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "ai_hint_consent" not in data
    assert data["record_history"] is True


def test_save_fields_rejects_unknown_field(tmp_path: Path) -> None:
    """Опечатка в имени поля не должна тихо создавать мусорный ключ."""
    with pytest.raises(ValueError):
        save_fields(tmp_path / SETTINGS_FILE_NAME, recrod_history=True)


def test_stale_menu_snapshot_does_not_resurrect_revoked_consent(tmp_path: Path) -> None:
    """CNC-5-01: открытое меню воскрешало отозванное AI-согласие.

    Сценарий: меню открыто (снапшот с consent=True) → пользователь отозвал
    согласие другим каналом → в меню переключён тумблер истории.
    """
    path = tmp_path / SETTINGS_FILE_NAME
    path.write_text(
        json.dumps({"ai_hint_consent": True, "ai_hint_consent_endpoint": "http://x"}),
        encoding="utf-8",
    )
    menu_snapshot = load_settings(path)  # меню сняло снимок при запуске
    assert menu_snapshot.ai_hint_consent is True

    save_fields(path, ai_hint_consent=None, ai_hint_consent_endpoint=None)  # отзыв

    # Пункт 7 меню: пишем ТОЛЬКО тумблер, снапшот на диск не едет.
    save_fields(path, record_history=True)

    assert load_settings(path).ai_hint_consent is None


def test_save_settings_preserves_keys_written_by_another_channel(tmp_path: Path) -> None:
    """SET-2-02: save_settings больше не затирает файл целиком."""
    path = tmp_path / SETTINGS_FILE_NAME
    path.write_text(json.dumps({"onboarding_seen": True}), encoding="utf-8")

    save_settings(UserSettings(record_history=False), path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["onboarding_seen"] is True
    assert data["record_history"] is False


def test_save_settings_keeps_keys_unknown_to_this_version(tmp_path: Path) -> None:
    """Ключ от более новой версии не должен исчезать при записи из старой."""
    path = tmp_path / SETTINGS_FILE_NAME
    path.write_text(json.dumps({"future_flag": "keep-me"}), encoding="utf-8")

    save_settings(UserSettings(record_history=True), path)

    assert json.loads(path.read_text(encoding="utf-8"))["future_flag"] == "keep-me"


# ---------------------------------------------------------------------------
# issue #1133 — память окна лаунчера: первое непримитивное поле файла настроек
# ---------------------------------------------------------------------------


def test_field_readers_cover_every_dataclass_field() -> None:
    """LNCH-3-05: список полей один, а не переписан в четырёх местах.

    Поле, добавленное в dataclass и забытое в читателях, МОЛЧА не сохранялось
    бы — именно этим рисковала память лаунчера. Тест держит их синхронными
    вместо внимательности автора правки.
    """
    from dataclasses import fields

    declared = {field.name for field in fields(user_settings.UserSettings)}

    assert user_settings.settings_field_names() == declared


def test_launch_choice_roundtrip(tmp_path: Path) -> None:
    """Выбор окна переживает запись и чтение — включая путь как Path."""
    path = tmp_path / ".grader_settings.json"
    choice = user_settings.LaunchChoice(
        sandbox=True,
        record_history=False,
        lang="en",
        port=8123,
        workdir=tmp_path / "tasks",
    )

    user_settings.save_fields(path, last_launch=choice)

    assert user_settings.load_settings(path).last_launch == choice


def test_launch_choice_keeps_unset_fields_apart_from_defaults(tmp_path: Path) -> None:
    """«Не выбирал» остаётся None, а не превращается в False/0 (ADR-0012)."""
    path = tmp_path / ".grader_settings.json"

    user_settings.save_fields(path, last_launch=user_settings.LaunchChoice(port=9000))
    restored = user_settings.load_settings(path).last_launch

    assert restored is not None
    assert restored.port == 9000
    assert restored.sandbox is None
    assert restored.record_history is None
    assert restored.lang is None


def test_saving_launch_choice_keeps_foreign_keys(tmp_path: Path) -> None:
    """Критерий приёмки #1133: параллельная запись веба не теряется.

    Окно и веб пишут ОДИН файл. Если бы память окна писалась целым снапшотом,
    она вернула бы на диск состояние, снятое при открытии окна, — включая
    отозванное тем временем согласие на отправку кода AI-провайдеру.
    """
    path = tmp_path / ".grader_settings.json"
    user_settings.save_fields(path, ai_hint_consent=True, onboarding_seen=True)

    # Веб отозвал согласие, пока окно было открыто.
    user_settings.save_fields(path, ai_hint_consent=None)
    user_settings.save_fields(path, last_launch=user_settings.LaunchChoice(sandbox=True))

    restored = user_settings.load_settings(path)
    assert restored.ai_hint_consent is None, "отозванное согласие вернулось из памяти окна"
    assert restored.onboarding_seen is True, "чужой ключ затёрт"
    assert restored.last_launch is not None
    assert restored.last_launch.sandbox is True


def test_corrupt_launch_choice_does_not_break_other_settings(tmp_path: Path) -> None:
    """Мусор в памяти окна не уносит с собой остальные настройки."""
    path = tmp_path / ".grader_settings.json"
    path.write_text(
        json.dumps({"record_history": True, "last_launch": "не объект"}), encoding="utf-8"
    )

    restored = user_settings.load_settings(path)

    assert restored.record_history is True
    assert restored.last_launch is None


def test_launch_choice_ignores_wrong_types_field_by_field(tmp_path: Path) -> None:
    """Одно испорченное поле не обнуляет соседние — разбор поштучный.

    ``True`` в порту отдельным случаем: ``bool`` — подкласс ``int``, и без
    явной отсечки запомненный порт стал бы единицей.
    """
    path = tmp_path / ".grader_settings.json"
    path.write_text(
        json.dumps({"last_launch": {"sandbox": True, "port": True, "lang": 42, "workdir": ""}}),
        encoding="utf-8",
    )

    restored = user_settings.load_settings(path).last_launch

    assert restored is not None
    assert restored.sandbox is True  # уцелело
    assert restored.port is None  # bool портом не считается
    assert restored.lang is None
    assert restored.workdir is None


# ---------------------------------------------------------------------------
# issue #1136 (LNCH-3-06) — два писателя одного файла не теряют чужие ключи
# ---------------------------------------------------------------------------


def test_save_settings_writes_every_known_field(tmp_path: Path) -> None:
    """Снапшот сохраняет ВСЕ поля модели, а не те, что вспомнили руками.

    `last_launch` появился в модели, в чтении и в `save_fields`, но в
    `save_settings` его забыли — память окна лаунчера молча не доезжала до
    диска. Ровно тот дефект, о котором предупреждала находка LNCH-3-05.
    """
    path = tmp_path / ".grader_settings.json"
    settings = UserSettings(
        record_history=True,
        ai_hint_consent=True,
        ai_hint_consent_endpoint="http://localhost:11434",
        onboarding_seen=True,
        last_launch=user_settings.LaunchChoice(sandbox=True, port=8123),
        launch_profiles={"с изоляцией": user_settings.LaunchChoice(sandbox=True)},
        last_profile="с изоляцией",
    )

    user_settings.save_settings(settings, path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert set(written) == user_settings.settings_field_names()
    assert written["last_launch"] == {"sandbox": True, "port": 8123}
    assert written["launch_profiles"] == {"с изоляцией": {"sandbox": True}}


def test_concurrent_writers_keep_each_others_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Веб и лаунчер пишут один файл — ключи обоих остаются на месте.

    Атомарная запись спасает от усечённого файла, но не от потерянного
    обновления: цикл «прочитать всё → заменить одно поле → записать всё» у двух
    писателей пересекается.

    Окно гонки расширено намеренно — паузой после чтения, — иначе тест зависел
    бы от планировщика: вживую он ронял чужой ключ примерно в каждом десятом
    прогоне, то есть «зелёный» ничего не доказывал бы. С паузой без блокировки
    потеря гарантирована, а с блокировкой второй писатель ждёт освобождения и
    читает уже обновлённое состояние.
    """
    path = tmp_path / ".grader_settings.json"
    save_fields(path, onboarding_seen=True)
    real_read_raw = user_settings._read_raw

    def slow_read_raw(target: Path) -> dict[str, object]:
        data = real_read_raw(target)
        time.sleep(0.2)
        return data

    monkeypatch.setattr(user_settings, "_read_raw", slow_read_raw)
    barrier = threading.Barrier(2)

    def writer(field: str, value: object) -> None:
        barrier.wait()
        save_fields(path, **{field: value})

    threads = [
        threading.Thread(target=writer, args=("record_history", False)),
        threading.Thread(target=writer, args=("ai_hint_consent", True)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    restored = load_settings(path)
    assert restored.onboarding_seen is True, "ключ, записанный до гонки, затёрт"
    assert restored.record_history is False, "запись первого писателя потеряна"
    assert restored.ai_hint_consent is True, "запись второго писателя потеряна"


def test_lock_failure_does_not_block_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Блокировка недоступна — запись всё равно идёт (best-effort).

    Отказать в сохранении было бы хуже потерянного обновления: пользователь
    решил бы, что настройка не работает вовсе.
    """
    path = tmp_path / ".grader_settings.json"
    monkeypatch.setattr(atomic_io, "_try_acquire", lambda _fd: False)

    save_fields(path, record_history=True)

    assert load_settings(path).record_history is True


# ---------------------------------------------------------------------------
# issue #1133 (шаг 2) — именованные профили запуска
# ---------------------------------------------------------------------------


class TestLaunchProfiles:
    """Профиль — это выбор, которому дали имя: один контрол вместо пяти."""

    def test_saved_profile_comes_back(self, tmp_path: Path) -> None:
        path = tmp_path / SETTINGS_FILE_NAME
        choice = user_settings.LaunchChoice(sandbox=True, port=8123, lang="en")

        user_settings.save_launch_profile(path, "с изоляцией", choice)

        assert user_settings.load_settings(path).launch_profiles == {"с изоляцией": choice}

    def test_same_name_updates_instead_of_duplicating(self, tmp_path: Path) -> None:
        path = tmp_path / SETTINGS_FILE_NAME
        user_settings.save_launch_profile(path, "как обычно", user_settings.LaunchChoice(port=8000))
        user_settings.save_launch_profile(path, "как обычно", user_settings.LaunchChoice(port=9000))

        profiles = user_settings.load_settings(path).launch_profiles
        assert list(profiles) == ["как обычно"]
        assert profiles["как обычно"].port == 9000

    def test_profile_does_not_touch_other_keys(self, tmp_path: Path) -> None:
        """Файл общий с вебом и меню — их ключи профиль не трогает."""
        path = tmp_path / SETTINGS_FILE_NAME
        save_fields(path, ai_hint_consent=True, record_history=False)

        user_settings.save_launch_profile(path, "быстрый", user_settings.LaunchChoice(port=8080))

        restored = user_settings.load_settings(path)
        assert restored.ai_hint_consent is True
        assert restored.record_history is False
        assert "быстрый" in restored.launch_profiles

    def test_delete_removes_profile_and_reports_it(self, tmp_path: Path) -> None:
        path = tmp_path / SETTINGS_FILE_NAME
        user_settings.save_launch_profile(path, "лишний", user_settings.LaunchChoice(port=8081))

        assert user_settings.delete_launch_profile(path, "лишний") is True
        assert user_settings.delete_launch_profile(path, "лишний") is False
        assert user_settings.load_settings(path).launch_profiles == {}

    def test_delete_clears_last_profile_pointing_at_it(self, tmp_path: Path) -> None:
        """Ссылка на удалённый профиль показывала бы имя, которое ничего не даёт."""
        path = tmp_path / SETTINGS_FILE_NAME
        user_settings.save_launch_profile(path, "текущий", user_settings.LaunchChoice(port=8082))
        save_fields(path, last_profile="текущий")

        user_settings.delete_launch_profile(path, "текущий")

        assert user_settings.load_settings(path).last_profile is None

    def test_delete_keeps_last_profile_pointing_elsewhere(self, tmp_path: Path) -> None:
        path = tmp_path / SETTINGS_FILE_NAME
        user_settings.save_launch_profile(path, "первый", user_settings.LaunchChoice(port=8083))
        user_settings.save_launch_profile(path, "второй", user_settings.LaunchChoice(port=8084))
        save_fields(path, last_profile="второй")

        user_settings.delete_launch_profile(path, "первый")

        assert user_settings.load_settings(path).last_profile == "второй"

    @pytest.mark.parametrize("name", ["", "   ", "\n", "имя\nс переводом", "x" * 65])
    def test_bad_names_are_refused(self, tmp_path: Path, name: str) -> None:
        """Молча подменять имя нельзя: пользователь ищет в списке ровно своё.

        Перевод строки отдельным случаем — им можно подделать соседнюю строку
        списка, а не только испортить раскладку.
        """
        with pytest.raises(ValueError):
            user_settings.save_launch_profile(
                tmp_path / SETTINGS_FILE_NAME, name, user_settings.LaunchChoice()
            )

    def test_limit_is_enforced_for_new_names_only(self, tmp_path: Path) -> None:
        """Лимит бережёт файл от роста, но не мешает обновлять существующий."""
        path = tmp_path / SETTINGS_FILE_NAME
        for index in range(user_settings.MAX_LAUNCH_PROFILES):
            user_settings.save_launch_profile(
                path, f"профиль {index}", user_settings.LaunchChoice(port=8000 + index)
            )

        with pytest.raises(user_settings.ProfileLimitError):
            user_settings.save_launch_profile(path, "лишний", user_settings.LaunchChoice())

        # Существующий по-прежнему перезаписывается — это «обновить», не «завести».
        user_settings.save_launch_profile(path, "профиль 0", user_settings.LaunchChoice(port=9999))
        assert user_settings.load_settings(path).launch_profiles["профиль 0"].port == 9999

    def test_broken_entries_are_dropped_one_by_one(self, tmp_path: Path) -> None:
        """Один испорченный профиль не уносит соседние."""
        path = tmp_path / SETTINGS_FILE_NAME
        path.write_text(
            json.dumps(
                {
                    "launch_profiles": {
                        "хороший": {"sandbox": True},
                        "": {"port": 1},
                        "мусор": "строка вместо объекта",
                    }
                }
            ),
            encoding="utf-8",
        )

        profiles = user_settings.load_settings(path).launch_profiles

        assert list(profiles) == ["хороший"]

    def test_last_profile_survives_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / SETTINGS_FILE_NAME
        save_fields(path, last_profile="как обычно")

        assert user_settings.load_settings(path).last_profile == "как обычно"

    def test_absent_profiles_are_empty_dict_not_none(self, tmp_path: Path) -> None:
        """«Профилей нет» и «поле не задано» для списка выбора — одно и то же."""
        assert user_settings.load_settings(tmp_path / SETTINGS_FILE_NAME).launch_profiles == {}
