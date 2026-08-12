"""Tests for core/user_settings.py — персистентные user-настройки CLI (issue #430).

Слой отдельный от config.py (frozen, только pyproject): хранит переключаемые из
меню настройки в .grader_settings.json. Здесь — round-trip, best-effort загрузка
и атомарная запись.
"""

from __future__ import annotations

from pathlib import Path

from stepik_grader.core import user_settings
from stepik_grader.core.user_settings import UserSettings


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
