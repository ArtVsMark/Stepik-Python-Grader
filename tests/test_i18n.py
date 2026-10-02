"""Tests for core/i18n.py — JSON-locale loader (issue #141/#144/#355).

Проверяет ``load_locale_messages()`` (graceful degradation на отсутствующий/
битый файл) и то, что ``cli._t()`` — тонкая обёртка над единым JSON-каталогом
``core/locales/<lang>.json`` (issue #355 слил захардкоженный ``_MESSAGES`` в
JSON; отдельного словаря больше нет).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader import cli
from stepik_grader.cli import options
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
# cli._t() — тонкая обёртка над единым JSON-каталогом (issue #355)
# ---------------------------------------------------------------------------


def test_t_reads_cli_message_from_locale_catalog(monkeypatch) -> None:
    # CLI-сообщения слиты в core/locales/*.json (issue #355); _t() читает их
    # напрямую из загруженного каталога, без отдельного захардкоженного словаря.
    monkeypatch.setattr(cli, "_LANG", "en")
    assert cli._t("goodbye") == "Goodbye!"
    monkeypatch.setattr(cli, "_LANG", "ru")
    assert cli._t("goodbye") == "До свидания!"


def test_t_resolves_key_from_locale_messages_mapping(monkeypatch) -> None:
    # _t() резолвит ключ строго из _LOCALE_MESSAGES[_LANG]: подмена каталога
    # меняет вывод — единственный источник это JSON-каталог.
    monkeypatch.setattr(
        cli, "_LOCALE_MESSAGES", {"ru": {"brand_new_key": "Привет из JSON"}, "en": {}}
    )
    monkeypatch.setattr(cli, "_LANG", "ru")
    assert cli._t("brand_new_key") == "Привет из JSON"


def test_t_kwargs_formatting(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_LANG", "en")
    assert cli._t("file_not_found", path="x.py") == "File not found: x.py"


def test_t_raises_keyerror_on_unknown_key(monkeypatch) -> None:
    # Отсутствующий ключ — программная ошибка, а не тихий фолбэк (парность
    # ru/en стережёт check_locale_guardrails.py): _t() поднимает KeyError.
    monkeypatch.setattr(cli, "_LANG", "en")
    with pytest.raises(KeyError):
        cli._t("no_such_key_anywhere")


# ---------------------------------------------------------------------------
# Справка следует --lang — issue #997 (INS-5-03)
# ---------------------------------------------------------------------------


class TestHelpFollowsLang:
    """`--lang en --help` печатал целиком русскую справку.

    Справка — первая поверхность, которую видит новый пользователь, и
    единственная, до которой он доходит раньше всех остальных. Она игнорировала
    выбор языка, потому что тексты были зашиты в код парсера.
    """

    def _help(self, capsys, argv: list[str]) -> str:
        with pytest.raises(SystemExit):
            cli.main([*argv, "--help"])
        return capsys.readouterr().out

    def test_english_help_has_no_russian(self, capsys):
        out = self._help(capsys, ["--lang", "en"])

        assert "Run mode" in out
        assert not any("\u0400" <= ch <= "\u04ff" for ch in out), "в английской справке кириллица"

    def test_russian_help_unchanged(self, capsys):
        """Регрессия: без флага справка прежняя, русская."""
        out = self._help(capsys, [])

        assert "Режим запуска" in out
        assert "Run mode" not in out

    def test_epilog_follows_lang(self, capsys):
        """Эпилог — тот же случай: он объясняет, как устроена задача."""
        assert "How a task is laid out" in self._help(capsys, ["--lang", "en"])
        assert "Как устроена задача" in self._help(capsys, [])

    def test_peek_lang_reads_flag_before_parser(self):
        assert options.peek_lang(["--lang", "en", "--mode", "1"]) == "en"
        assert options.peek_lang(["--mode", "1"]) == options.DEFAULT_LANG

    def test_peek_lang_ignores_unknown_value(self):
        """Ругаться на неизвестный язык — работа основного парсера с choices."""
        assert options.peek_lang(["--lang", "klingon"]) == options.DEFAULT_LANG

    def test_unknown_lang_still_rejected_by_parser(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--lang", "klingon", "--mode", "1"])

    def test_help_keys_exist_for_every_flag(self):
        """Гейт от дрейфа: новый флаг обязан принести ключ в обе локали."""
        parser = options._build_arg_parser()
        ru = options._help_texts("ru")
        en = options._help_texts("en")
        for action in parser._actions:
            if not action.option_strings or action.dest == "help":
                continue
            long = next(
                (o for o in action.option_strings if o.startswith("--")), action.option_strings[0]
            )
            key = "cli_help_" + long.lstrip("-").replace("-", "_")
            assert key in ru, f"нет русского текста справки: {key}"
            assert key in en, f"нет английского текста справки: {key}"
