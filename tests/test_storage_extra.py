"""test_storage_extra.py — расширенные тесты для модуля storage.

Покрывает edge-cases, не вошедшие в test_storage.py:
- load_json_file: невалидный JSON, JSON-массив, директория вместо файла,
  отсутствующий файл, Unicode-символы, пустой объект.
- save_json_file: создание вложенных директорий, перезапись существующего,
  Unicode-значения, ensure_ascii=False.
- save_secrets: делегирует save_json_file (smoke).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from storage import load_json_file, save_json_file, save_secrets


# ---------------------------------------------------------------------------
# load_json_file — happy-path
# ---------------------------------------------------------------------------


class TestLoadJsonFileHappyPath:
    """load_json_file успешно читает корректные JSON-объекты."""

    def test_simple_dict(self, tmp_path: pathlib.Path) -> None:
        """Простой dict читается без ошибок."""
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = load_json_file(f)
        assert result == {"key": "value"}

    def test_empty_object(self, tmp_path: pathlib.Path) -> None:
        """Пустой JSON-объект {} возвращает пустой dict."""
        f = tmp_path / "empty.json"
        f.write_text("{}", encoding="utf-8")
        assert load_json_file(f) == {}

    def test_nested_dict(self, tmp_path: pathlib.Path) -> None:
        """Вложенный dict читается корректно."""
        payload = {"outer": {"inner": [1, 2, 3]}, "flag": True}
        f = tmp_path / "nested.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        assert load_json_file(f) == payload

    def test_unicode_values(self, tmp_path: pathlib.Path) -> None:
        """Unicode-значения читаются без искажений."""
        payload = {"привет": "мир", "emoji": "\U0001f40d"}
        f = tmp_path / "unicode.json"
        f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert load_json_file(f) == payload

    def test_accepts_pathlike(self, tmp_path: pathlib.Path) -> None:
        """Принимает pathlib.Path-объект."""
        f = tmp_path / "file.json"
        f.write_text('{"x": 1}', encoding="utf-8")
        result = load_json_file(f)  # pathlib.Path
        assert result["x"] == 1


# ---------------------------------------------------------------------------
# load_json_file — error cases
# ---------------------------------------------------------------------------


class TestLoadJsonFileErrors:
    """load_json_file поднимает корректные исключения при неверных входных данных."""

    def test_raises_on_missing_file(self, tmp_path: pathlib.Path) -> None:
        """FileNotFoundError при отсутствующем файле."""
        with pytest.raises(FileNotFoundError):
            load_json_file(tmp_path / "no_such_file.json")

    def test_raises_on_directory(self, tmp_path: pathlib.Path) -> None:
        """IsADirectoryError (или OSError на Windows) если путь — директория."""
        with pytest.raises((IsADirectoryError, OSError, PermissionError)):
            load_json_file(tmp_path)

    def test_raises_on_invalid_json(self, tmp_path: pathlib.Path) -> None:
        """json.JSONDecodeError при невалидном JSON."""
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_json_file(f)

    def test_raises_on_json_array(self, tmp_path: pathlib.Path) -> None:
        """ValueError если корень JSON — массив, а не объект."""
        f = tmp_path / "array.json"
        f.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="Ожидался JSON-объект"):
            load_json_file(f)

    def test_raises_on_json_string(self, tmp_path: pathlib.Path) -> None:
        """ValueError если корень JSON — строка."""
        f = tmp_path / "string.json"
        f.write_text('"just a string"', encoding="utf-8")
        with pytest.raises(ValueError, match="Ожидался JSON-объект"):
            load_json_file(f)

    def test_raises_on_json_number(self, tmp_path: pathlib.Path) -> None:
        """ValueError если корень JSON — число."""
        f = tmp_path / "number.json"
        f.write_text("42", encoding="utf-8")
        with pytest.raises(ValueError, match="Ожидался JSON-объект"):
            load_json_file(f)


# ---------------------------------------------------------------------------
# save_json_file — happy-path
# ---------------------------------------------------------------------------


class TestSaveJsonFile:
    """save_json_file записывает dict как корректный JSON-файл."""

    def test_creates_file(self, tmp_path: pathlib.Path) -> None:
        """Файл создаётся если не существовал."""
        f = tmp_path / "out.json"
        save_json_file(f, {"a": 1})
        assert f.exists()

    def test_roundtrip(self, tmp_path: pathlib.Path) -> None:
        """Записанное и прочитанное содержимое совпадает."""
        payload = {"step": 1, "name": "test", "items": [1, 2]}
        f = tmp_path / "rt.json"
        save_json_file(f, payload)
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_creates_nested_dirs(self, tmp_path: pathlib.Path) -> None:
        """Создаёт отсутствующие родительские директории (mkdir parents=True)."""
        f = tmp_path / "a" / "b" / "c" / "file.json"
        save_json_file(f, {"x": 99})
        assert f.exists()
        assert json.loads(f.read_text(encoding="utf-8")) == {"x": 99}

    def test_overwrites_existing_file(self, tmp_path: pathlib.Path) -> None:
        """Перезаписывает существующий файл без ошибок."""
        f = tmp_path / "over.json"
        save_json_file(f, {"v": 1})
        save_json_file(f, {"v": 2})
        assert json.loads(f.read_text(encoding="utf-8")) == {"v": 2}

    def test_unicode_ensure_ascii_false(self, tmp_path: pathlib.Path) -> None:
        """Unicode пишется без \\uXXXX экранирования (ensure_ascii=False)."""
        f = tmp_path / "uni.json"
        save_json_file(f, {"key": "значение"})
        raw = f.read_text(encoding="utf-8")
        assert "значение" in raw

    def test_indent_2(self, tmp_path: pathlib.Path) -> None:
        """Файл записывается с отступом 2 пробела (indent=2)."""
        f = tmp_path / "pretty.json"
        save_json_file(f, {"a": {"b": 1}})
        raw = f.read_text(encoding="utf-8")
        # indent=2 даёт как минимум двойной пробел перед вложенным ключом
        assert "  " in raw

    def test_empty_dict(self, tmp_path: pathlib.Path) -> None:
        """Пустой dict записывается и читается как {}."""
        f = tmp_path / "empty.json"
        save_json_file(f, {})
        assert json.loads(f.read_text(encoding="utf-8")) == {}


# ---------------------------------------------------------------------------
# save_secrets — smoke
# ---------------------------------------------------------------------------


class TestSaveSecrets:
    """save_secrets делегирует save_json_file: достаточно smoke-теста."""

    def test_saves_correctly(self, tmp_path: pathlib.Path) -> None:
        """Файл создаётся и содержит переданные данные."""
        f = tmp_path / "secrets.json"
        data = {"client_id": "abc", "client_secret": "xyz"}
        save_secrets(f, data)
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: pathlib.Path) -> None:
        """Вложенные директории создаются автоматически."""
        f = tmp_path / "sub" / "secrets.json"
        save_secrets(f, {"token": "t"})
        assert f.exists()
