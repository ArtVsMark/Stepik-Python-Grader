"""Tests for the guided OAuth secrets.json wizard (issue #433).

downloader_config gains create_secrets_interactively (пошаговое создание
secrets.json через save_secrets, 0600) и точку входа в normalize_config_paths:
при отсутствии файла — предложить создать, а не зациклить пере-запрос пути.
Плюс регресс на баг рекурсии _print (fallback без rich).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from stepik_grader import downloader_config


def _feed(monkeypatch, values: list[str]) -> None:
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))


def test_print_fallback_no_recursion(monkeypatch, capsys) -> None:
    """Регресс #433: без rich _print печатает через print(), а не рекурсивно себя."""
    monkeypatch.setattr(downloader_config, "_RICH", False)
    monkeypatch.setattr(downloader_config, "_console", None)
    downloader_config._print("hello-plain")  # не должно быть RecursionError
    assert "hello-plain" in capsys.readouterr().out


def test_create_secrets_interactively_writes_file(tmp_path: Path, monkeypatch) -> None:
    secrets_path = tmp_path / "secrets.json"
    # client_id, client_secret, redirect_uri (пусто → дефолт)
    _feed(monkeypatch, ["my-id", "my-secret", ""])
    result = downloader_config.create_secrets_interactively(secrets_path)
    assert result == {
        "client_id": "my-id",
        "client_secret": "my-secret",
        "redirect_uri": downloader_config.DEFAULT_REDIRECT_URI,
    }
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data["client_id"] == "my-id"
    assert data["redirect_uri"] == downloader_config.DEFAULT_REDIRECT_URI


def test_create_secrets_custom_redirect(tmp_path: Path, monkeypatch) -> None:
    secrets_path = tmp_path / "secrets.json"
    _feed(monkeypatch, ["id", "sec", "http://localhost:9999/cb"])
    result = downloader_config.create_secrets_interactively(secrets_path)
    assert result["redirect_uri"] == "http://localhost:9999/cb"


def test_create_secrets_prints_oauth_instructions(tmp_path: Path, monkeypatch, capsys) -> None:
    """issue #433 crit 2: печатается ссылка на OAuth-приложение и требуемые поля."""
    _feed(monkeypatch, ["id", "sec", ""])
    downloader_config.create_secrets_interactively(tmp_path / "secrets.json")
    out = capsys.readouterr().out
    assert downloader_config.STEPIK_OAUTH_APPS_URL in out
    assert "Confidential" in out
    assert "Authorization code" in out
    assert downloader_config.DEFAULT_REDIRECT_URI in out


def test_create_secrets_empty_creds_does_not_write(tmp_path: Path, monkeypatch, capsys) -> None:
    """Пустые client_id/secret → файл НЕ создаётся (не пишем невалидный secrets.json)."""
    secrets_path = tmp_path / "secrets.json"
    _feed(monkeypatch, ["", "", ""])
    downloader_config.create_secrets_interactively(secrets_path)
    assert not secrets_path.exists()
    assert "не создан" in capsys.readouterr().out


def test_create_secrets_is_owner_only_on_posix(tmp_path: Path, monkeypatch) -> None:
    if os.name == "nt":
        return  # NTFS ACL, не Unix-биты — see storage.save_secrets docstring
    secrets_path = tmp_path / "secrets.json"
    _feed(monkeypatch, ["id", "sec", ""])
    downloader_config.create_secrets_interactively(secrets_path)
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600


def test_normalize_creates_secrets_via_wizard_on_yes(tmp_path: Path, monkeypatch) -> None:
    """Отсутствует secrets.json + согласие → wizard создаёт файл (не цикл пути)."""
    config_path = tmp_path / "stepik_config.json"
    secrets_path = tmp_path / "secrets.json"  # не существует
    config = {"root_dir": str(tmp_path / "tasks"), "secrets_path": str(secrets_path)}
    # _confirm_yes "" → да, затем wizard: client_id, client_secret, redirect (пусто)
    _feed(monkeypatch, ["", "cid", "csecret", ""])
    result = downloader_config.normalize_config_paths(config, config_path)
    assert secrets_path.exists()
    assert result["secrets_path"] == str(secrets_path)
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data["client_id"] == "cid"


def test_normalize_reprompts_path_on_no(tmp_path: Path, monkeypatch) -> None:
    """Отказ от wizard'а → прежнее поведение: пере-запрос пути к готовому файлу."""
    config_path = tmp_path / "stepik_config.json"
    missing = tmp_path / "nope.json"
    existing = tmp_path / "real_secrets.json"
    existing.write_text(
        json.dumps({"client_id": "a", "client_secret": "b", "redirect_uri": "c"}),
        encoding="utf-8",
    )
    config = {"root_dir": str(tmp_path), "secrets_path": str(missing)}
    # _confirm_yes "n" → нет; затем create_or_update_config: root_dir, secrets_path
    _feed(monkeypatch, ["n", str(tmp_path), str(existing)])
    result = downloader_config.normalize_config_paths(config, config_path)
    assert result["secrets_path"] == str(existing)
    assert not missing.exists()
