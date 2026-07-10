"""Тесты для storage.py — чтение и запись JSON-файлов."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import sys

import pytest

from stepik_grader.core.storage import load_json_file, save_json_file, save_secrets

# ── load_json_file ────────────────────────────────────────────────────────────


def test_load_json_file_returns_dict(tmp_path: pathlib.Path) -> None:
    """Читает корректный JSON-объект и возвращает dict."""
    file = tmp_path / "data.json"
    file.write_text('{"key": "value", "num": 42}', encoding="utf-8")
    result = load_json_file(file)
    assert result == {"key": "value", "num": 42}


def test_load_json_file_nested(tmp_path: pathlib.Path) -> None:
    """Читает вложенный JSON-объект."""
    payload = {"outer": {"inner": [1, 2, 3]}, "flag": True}
    file = tmp_path / "nested.json"
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert load_json_file(file) == payload


def test_load_json_file_raises_value_error_for_list(tmp_path: pathlib.Path) -> None:
    """Бросает ValueError если корень JSON — список, а не объект."""
    file = tmp_path / "list.json"
    file.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="Ожидался JSON-объект"):
        load_json_file(file)


def test_load_json_file_raises_value_error_for_string(tmp_path: pathlib.Path) -> None:
    """Бросает ValueError если корень JSON — строка."""
    file = tmp_path / "str.json"
    file.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(ValueError, match="Ожидался JSON-объект"):
        load_json_file(file)


def test_load_json_file_raises_for_missing_file(tmp_path: pathlib.Path) -> None:
    """Бросает FileNotFoundError если файл не существует."""
    with pytest.raises(FileNotFoundError):
        load_json_file(tmp_path / "ghost.json")


def test_load_json_file_raises_for_invalid_json(tmp_path: pathlib.Path) -> None:
    """Бросает json.JSONDecodeError если файл содержит невалидный JSON."""
    file = tmp_path / "bad.json"
    file.write_text("not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_json_file(file)


def test_load_json_file_cyrillic_values(tmp_path: pathlib.Path) -> None:
    """Корректно читает кириллические значения."""
    file = tmp_path / "cyrillic.json"
    file.write_text('{"имя": "Артём"}', encoding="utf-8")
    assert load_json_file(file) == {"имя": "Артём"}


# ── save_json_file ────────────────────────────────────────────────────────────


def test_save_json_file_creates_file(tmp_path: pathlib.Path) -> None:
    """Создаёт файл и записывает корректный JSON."""
    file = tmp_path / "out.json"
    save_json_file(file, {"hello": "world"})
    assert file.exists()
    assert json.loads(file.read_text(encoding="utf-8")) == {"hello": "world"}


def test_save_json_file_creates_parent_dirs(tmp_path: pathlib.Path) -> None:
    """Создаёт родительские директории при необходимости."""
    file = tmp_path / "a" / "b" / "c" / "data.json"
    save_json_file(file, {"x": 1})
    assert file.exists()


def test_save_json_file_uses_indent_2(tmp_path: pathlib.Path) -> None:
    """Файл содержит отступы в 2 пробела (indent=2)."""
    file = tmp_path / "pretty.json"
    save_json_file(file, {"a": 1})
    content = file.read_text(encoding="utf-8")
    assert "  " in content


def test_save_json_file_no_ascii_escape(tmp_path: pathlib.Path) -> None:
    """Кириллица сохраняется без \\uXXXX-экранирования (ensure_ascii=False)."""
    file = tmp_path / "ru.json"
    save_json_file(file, {"город": "Москва"})
    content = file.read_text(encoding="utf-8")
    assert "Москва" in content
    assert "\\u" not in content


def test_save_json_file_roundtrip(tmp_path: pathlib.Path) -> None:
    """Сохранённый файл полностью восстанавливается через load_json_file."""
    file = tmp_path / "roundtrip.json"
    original = {"list": [1, 2, 3], "nested": {"ok": True}}
    save_json_file(file, original)
    assert load_json_file(file) == original


# ── save_secrets ──────────────────────────────────────────────────────────────


def test_save_secrets_creates_file(tmp_path: pathlib.Path) -> None:
    """save_secrets создаёт файл с корректным содержимым."""
    secrets_file = tmp_path / "secrets.json"
    data = {"client_id": "abc", "client_secret": "xyz"}
    save_secrets(secrets_file, data)
    assert secrets_file.exists()
    assert json.loads(secrets_file.read_text(encoding="utf-8")) == data


def test_save_secrets_same_json_format_as_save_json_file(tmp_path: pathlib.Path) -> None:
    """save_secrets сериализует так же, как save_json_file (issue #243: own
    write path for chmod, но формат JSON не меняется)."""
    file_a = tmp_path / "a.json"
    file_b = tmp_path / "b.json"
    data = {"token": "secret123"}
    save_secrets(file_a, data)
    save_json_file(file_b, data)
    assert file_a.read_text(encoding="utf-8") == file_b.read_text(encoding="utf-8")


def test_save_secrets_creates_parent_dirs(tmp_path: pathlib.Path) -> None:
    """save_secrets создаёт недостающие родительские директории, как save_json_file."""
    secrets_file = tmp_path / "nested" / "dir" / "secrets.json"
    save_secrets(secrets_file, {"client_id": "abc"})
    assert secrets_file.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: биты режима, не NTFS ACL")
def test_save_secrets_sets_owner_only_permissions_on_posix(tmp_path: pathlib.Path) -> None:
    """На POSIX secrets.json создаётся с режимом 0600 (issue #243, F-04)."""
    secrets_file = tmp_path / "secrets.json"
    save_secrets(secrets_file, {"access_token": "AT"})
    mode = stat.S_IMODE(secrets_file.stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: биты режима, не NTFS ACL")
def test_save_secrets_fixes_permissions_of_preexisting_wide_open_file(
    tmp_path: pathlib.Path,
) -> None:
    """Файл, оставшийся от старой версии с широкими правами, приводится к 0600."""
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text("{}", encoding="utf-8")
    os.chmod(secrets_file, 0o644)

    save_secrets(secrets_file, {"access_token": "AT"})

    mode = stat.S_IMODE(secrets_file.stat().st_mode)
    assert mode == 0o600
