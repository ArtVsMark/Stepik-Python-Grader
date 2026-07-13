"""Тесты для downloader_config.py — конфиг и интерактив загрузчика (issue #302).

Выделено из test_downloader_extra.py вместе с самим модулем: ask_value,
create/load/normalize конфига (интерактивные ветки). Патчи нацелены на
``stepik_grader.downloader_config`` — модуль, где функции теперь живут.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

from stepik_grader import downloader_config
from stepik_grader.downloader_config import (
    ask_value,
    create_or_update_config,
    load_or_create_config,
    normalize_config_paths,
)


class TestAskValue:
    """ask_value возвращает ввод пользователя либо дефолт."""

    def test_returns_input(self):
        with patch("builtins.input", return_value="  myval  "):
            assert ask_value("prompt", "def") == "myval"

    def test_returns_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            assert ask_value("prompt", "def") == "def"


class TestConfigFunctions:
    """create/load/normalize конфига — интерактивные ветки."""

    def test_create_or_update_config_writes(self, tmp_path: pathlib.Path):
        """Запрашивает поля и сохраняет конфиг через save_json_file."""
        cfg_path = tmp_path / "cfg.json"
        with patch(
            "stepik_grader.downloader_config.ask_value", side_effect=["/root", "secrets.json"]
        ):
            config = create_or_update_config(cfg_path)
        assert config == {"root_dir": "/root", "secrets_path": "secrets.json"}
        assert cfg_path.exists()

    def test_load_or_create_when_missing(self, tmp_path: pathlib.Path):
        """Отсутствующий конфиг → запуск create_or_update_config."""
        cfg_path = tmp_path / "nope.json"
        with patch(
            "stepik_grader.downloader_config.create_or_update_config",
            return_value={"root_dir": "r"},
        ) as mock_create:
            result = load_or_create_config(cfg_path)
        mock_create.assert_called_once()
        assert result == {"root_dir": "r"}

    def test_load_existing_no_change(self, tmp_path: pathlib.Path):
        """Существующий конфиг, пользователь не хочет менять → возвращается как есть."""
        cfg_path = tmp_path / "cfg.json"
        downloader_config.save_json_file(cfg_path, {"root_dir": "r", "secrets_path": "s"})
        with patch("builtins.input", return_value="n"):
            result = load_or_create_config(cfg_path)
        assert result["root_dir"] == "r"

    def test_load_existing_with_change(self, tmp_path: pathlib.Path):
        """Пользователь отвечает 'y' → перезапуск создания конфига."""
        cfg_path = tmp_path / "cfg.json"
        downloader_config.save_json_file(cfg_path, {"root_dir": "r", "secrets_path": "s"})
        with (
            patch("builtins.input", return_value="y"),
            patch(
                "stepik_grader.downloader_config.create_or_update_config", return_value={"new": 1}
            ) as mock_create,
        ):
            result = load_or_create_config(cfg_path)
        mock_create.assert_called_once()
        assert result == {"new": 1}

    def test_normalize_paths_makes_absolute(self, tmp_path: pathlib.Path):
        """Относительные пути становятся абсолютными; secrets-файл существует."""
        secrets = tmp_path / "secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "StepikTasks", "secrets_path": str(secrets)}
        result = normalize_config_paths(config, cfg_path)
        assert pathlib.Path(result["root_dir"]).is_absolute()
        assert pathlib.Path(result["secrets_path"]).is_absolute()

    def test_normalize_missing_fields_reprompts(self, tmp_path: pathlib.Path):
        """Пустые обязательные поля → повторный create_or_update_config."""
        secrets = tmp_path / "secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "", "secrets_path": ""}
        with patch(
            "stepik_grader.downloader_config.create_or_update_config",
            return_value={
                "root_dir": str(tmp_path / "r"),
                "secrets_path": str(secrets),
            },
        ) as mock_create:
            result = normalize_config_paths(config, cfg_path)
        mock_create.assert_called_once()
        assert pathlib.Path(result["secrets_path"]).is_absolute()

    def test_normalize_secrets_not_found_reprompts(self, tmp_path: pathlib.Path):
        """secrets-файл не существует → повторный запрос конфига."""
        good_secrets = tmp_path / "good.json"
        good_secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "r", "secrets_path": str(tmp_path / "missing.json")}
        with patch(
            "stepik_grader.downloader_config.create_or_update_config",
            return_value={"root_dir": "r2", "secrets_path": str(good_secrets)},
        ) as mock_create:
            result = normalize_config_paths(config, cfg_path)
        mock_create.assert_called_once()
        assert pathlib.Path(result["root_dir"]).is_absolute()
