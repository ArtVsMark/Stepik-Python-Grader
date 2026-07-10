"""Tests for core/i18n.py — JSON-locale loader (issue #141/#144).

Проверяет ``load_locale_messages()`` (graceful degradation на отсутствующий/
битый файл) и то, что ``cli._t()`` действительно консультирует JSON-локаль
перед статическим ``_MESSAGES`` — новое сообщение можно добавить только через
JSON, не трогая существующий словарь.
"""

from __future__ import annotations

import json
import pathlib

from stepik_grader import cli
from stepik_grader.core.i18n import load_locale_messages


def test_load_locale_messages_reads_real_json_files() -> None:
    # core/locales/{ru,en}.json больше не пустая заготовка (issue #144) — issue
    # #264 наполнил их каталогом сообщений web-слоя (web/i18n.py). Загрузка не
    # должна падать и должна давать непустой dict с одинаковым набором ключей.
    ru = load_locale_messages("ru")
    en = load_locale_messages("en")
    assert ru != {}
    assert en != {}
    assert set(ru) == set(en)
    assert ru["path_not_found"] == "Путь не найден: {path}"
    assert en["path_not_found"] == "Path not found: {path}"


def test_load_locale_messages_missing_lang_returns_empty_dict() -> None:
    assert load_locale_messages("fr") == {}


def test_load_locale_messages_reads_custom_file(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / "xx.json").write_text(json.dumps({"greeting": "Hello from JSON"}), encoding="utf-8")
    monkeypatch.setattr("stepik_grader.core.i18n.LOCALES_DIR", tmp_path)
    assert load_locale_messages("xx") == {"greeting": "Hello from JSON"}


def test_load_locale_messages_invalid_json_returns_empty_dict(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr("stepik_grader.core.i18n.LOCALES_DIR", tmp_path)
    assert load_locale_messages("broken") == {}


def test_load_locale_messages_non_object_root_returns_empty_dict(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    (tmp_path / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr("stepik_grader.core.i18n.LOCALES_DIR", tmp_path)
    assert load_locale_messages("list") == {}


def test_load_locale_messages_coerces_values_to_str(tmp_path: pathlib.Path, monkeypatch) -> None:
    (tmp_path / "coerce.json").write_text(json.dumps({"n": 42}), encoding="utf-8")
    monkeypatch.setattr("stepik_grader.core.i18n.LOCALES_DIR", tmp_path)
    assert load_locale_messages("coerce") == {"n": "42"}


# ---------------------------------------------------------------------------
# cli._t() — приоритет JSON-локали над статическим _MESSAGES (issue #144)
# ---------------------------------------------------------------------------


def test_t_falls_back_to_static_messages_when_key_absent_in_locale(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_LOCALE_MESSAGES", {"ru": {}, "en": {}})
    monkeypatch.setattr(cli, "_LANG", "en")
    assert cli._t("goodbye") == "Goodbye!"


def test_t_prefers_json_locale_over_static_messages(monkeypatch) -> None:
    # Новое сообщение добавлено ТОЛЬКО через JSON-локаль (не в _MESSAGES) --
    # это и есть issue #144's "новые сообщения можно добавлять через JSON".
    monkeypatch.setattr(
        cli, "_LOCALE_MESSAGES", {"ru": {"brand_new_key": "Привет из JSON"}, "en": {}}
    )
    monkeypatch.setattr(cli, "_LANG", "ru")
    assert cli._t("brand_new_key") == "Привет из JSON"


def test_t_json_locale_overrides_existing_key_when_present(monkeypatch) -> None:
    # Существующий ключ _MESSAGES тоже можно переопределить через JSON, если
    # он там есть -- реальные core/locales/*.json (issue #264) используют
    # отдельное, непересекающееся пространство ключей веб-слоя, так что для
    # ключей _MESSAGES (как "goodbye") поведение CLI не меняется (см.
    # test_existing_cli_messages_unaffected_by_web_catalog_keys).
    monkeypatch.setattr(cli, "_LOCALE_MESSAGES", {"ru": {"goodbye": "Пока (из JSON)"}, "en": {}})
    monkeypatch.setattr(cli, "_LANG", "ru")
    assert cli._t("goodbye") == "Пока (из JSON)"


def test_t_kwargs_formatting_still_works_with_locale_fallback(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_LOCALE_MESSAGES", {"ru": {}, "en": {}})
    monkeypatch.setattr(cli, "_LANG", "en")
    assert cli._t("file_not_found", path="x.py") == "File not found: x.py"


def test_existing_cli_messages_unaffected_by_web_catalog_keys() -> None:
    # core/locales/{ru,en}.json теперь содержат каталог сообщений веб-слоя
    # (issue #264, snake_case-ключи вроде "path_not_found") -- пространство
    # ключей не пересекается с _MESSAGES CLI (см. test_i18n_guardrails.py
    # для проверок самого web-каталога), поэтому _t() для любого ключа
    # _MESSAGES по-прежнему откатывается на статический словарь, как до #264.
    assert set(cli._LOCALE_MESSAGES["ru"]).isdisjoint(cli._MESSAGES.keys())
    assert set(cli._LOCALE_MESSAGES["en"]).isdisjoint(cli._MESSAGES.keys())
