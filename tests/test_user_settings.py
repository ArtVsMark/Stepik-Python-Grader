"""Tests for core/user_settings.py — персистентные user-настройки CLI (issue #430).

Слой отдельный от config.py (frozen, только pyproject): хранит переключаемые из
меню настройки в .grader_settings.json. Здесь — round-trip, best-effort загрузка
и атомарная запись.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepik_grader.core import user_settings
from stepik_grader.core.user_settings import (
    SETTINGS_FILE_NAME,
    UserSettings,
    load_settings,
    save_fields,
    save_settings,
)


def test_default_settings_path_uses_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert user_settings.default_settings_path() == tmp_path / user_settings.SETTINGS_FILE_NAME


def test_default_settings_path_follows_given_root(tmp_path: Path, monkeypatch) -> None:
    """issue #984: корень настроек передаётся явно — CLI и веб читают один файл.

    Прежде CLI жёстко брал ``cwd``, а веб — ``--root``: один запуск имел два
    разных корня настроек, и тумблеры (история, согласие на AI) расходились.
    """
    nested = tmp_path / "task"
    nested.mkdir()
    monkeypatch.chdir(nested)

    assert (
        user_settings.default_settings_path(tmp_path) == tmp_path / user_settings.SETTINGS_FILE_NAME
    )


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
