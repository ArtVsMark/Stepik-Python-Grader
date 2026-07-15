"""Unit tests for web/auth_adapter.py — браузерный OAuth в web (issue #402).

Тонкий адаптер над core/oauth_flow: статус токена (auth_status) и полный
браузерный flow (perform_browser_auth, тут мокаем authorize_and_get_token —
без реального браузера). Эндпоинты /api/auth/* покрыты в test_web.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from stepik_grader.web import auth_adapter


def test_auth_status_no_secrets_when_missing(tmp_path: Path) -> None:
    assert auth_adapter.auth_status(tmp_path / "nope.json") == {
        "authorized": False,
        "reason": "no_secrets",
    }


def test_auth_status_ok_with_valid_token(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "a",
                "client_secret": "b",
                "redirect_uri": "c",
                "access_token": "tok",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )
    assert auth_adapter.auth_status(path) == {"authorized": True, "reason": "ok"}


def test_auth_status_no_token_when_creds_only(tmp_path: Path) -> None:
    """Креды есть, токена нет/истёк → нужен браузерный flow (no_token)."""
    path = tmp_path / "secrets.json"
    path.write_text(
        json.dumps({"client_id": "a", "client_secret": "b", "redirect_uri": "c"}),
        encoding="utf-8",
    )
    assert auth_adapter.auth_status(path) == {"authorized": False, "reason": "no_token"}


def test_auth_status_no_secrets_when_creds_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"client_id": "a"}), encoding="utf-8")
    assert auth_adapter.auth_status(path) == {"authorized": False, "reason": "no_secrets"}


def test_auth_status_corrupt_json_is_no_secrets(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text("{ broken", encoding="utf-8")
    assert auth_adapter.auth_status(path)["reason"] == "no_secrets"


def test_auth_status_bad_expires_type_does_not_crash(tmp_path: Path) -> None:
    """Битый expires_at (строка вместо числа) → best-effort no_token, не исключение."""
    path = tmp_path / "secrets.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "a",
                "client_secret": "b",
                "redirect_uri": "c",
                "access_token": "tok",
                "expires_at": "soon",
            }
        ),
        encoding="utf-8",
    )
    assert auth_adapter.auth_status(path) == {"authorized": False, "reason": "no_token"}


def test_perform_browser_auth_preserves_refresh_token(tmp_path: Path, monkeypatch) -> None:
    """Повторная авторизация не затирает существующий refresh_token до успеха flow."""
    path = tmp_path / "secrets.json"
    path.write_text(json.dumps({"client_id": "old", "refresh_token": "keep-me"}), encoding="utf-8")
    seen: dict[str, object] = {}

    def _fake_authorize(client_id, client_secret, redirect_uri, secrets_path):
        seen["file_at_flow"] = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
        return {}

    monkeypatch.setattr(auth_adapter, "authorize_and_get_token", _fake_authorize)
    auth_adapter.perform_browser_auth(path, "new-id", "new-secret", "uri")
    # refresh_token сохранён при перезаписи кредов.
    assert seen["file_at_flow"]["refresh_token"] == "keep-me"
    assert seen["file_at_flow"]["client_id"] == "new-id"


def test_perform_browser_auth_writes_creds_then_authorizes(tmp_path: Path, monkeypatch) -> None:
    """Сначала пишет креды в secrets.json (0600), затем browser-flow добирает токен."""
    path = tmp_path / "secrets.json"
    seen: dict[str, object] = {}

    def _fake_authorize(client_id, client_secret, redirect_uri, secrets_path):
        # На момент browser-flow креды уже должны быть в файле (порядок важен).
        seen["file_at_flow"] = json.loads(Path(secrets_path).read_text(encoding="utf-8"))
        seen["args"] = (client_id, client_secret, redirect_uri)
        return {"access_token": "t"}

    monkeypatch.setattr(auth_adapter, "authorize_and_get_token", _fake_authorize)
    result = auth_adapter.perform_browser_auth(
        path, "cid", "csec", "http://localhost:8080/callback"
    )

    assert result == {"authorized": True, "reason": "ok"}
    assert seen["args"] == ("cid", "csec", "http://localhost:8080/callback")
    # Креды записаны ДО обмена на токен.
    assert seen["file_at_flow"]["client_id"] == "cid"
    assert seen["file_at_flow"]["client_secret"] == "csec"


def test_perform_browser_auth_secrets_owner_only_on_posix(tmp_path: Path, monkeypatch) -> None:
    import os
    import stat

    if os.name == "nt":
        return
    path = tmp_path / "secrets.json"
    monkeypatch.setattr(auth_adapter, "authorize_and_get_token", lambda *a, **k: {})
    auth_adapter.perform_browser_auth(path, "cid", "csec", "uri")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
