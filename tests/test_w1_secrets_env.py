"""Волна W1 аудита 2026-07-20: секреты и окружение дочернего процесса.

* **#627** — macOS-backend наследовал весь ``os.environ`` грейдера (включая BYOK
  AI-ключ) в недоверенный код под ``--sandbox``.
* **#628** — ``save_secrets`` писал поверх цели (``O_TRUNC``) вопреки докстрингам
  ``oauth_flow``/``web.auth_adapter``, обещавшим атомарность.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from stepik_grader.core.sandbox import _macos, _posix_common
from stepik_grader.core.storage import save_secrets

# ---------------------------------------------------------------------------
# #627 — минимальное окружение изолированного процесса
# ---------------------------------------------------------------------------


def test_build_minimal_env_excludes_parent_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окружение дочернего процесса не наследует секреты родителя.

    Регрессия #627: под macOS-sandbox решение могло прочитать ``os.environ`` и
    получить BYOK AI-ключ. Запрет сети не спасал — вывести окружение можно
    прямо в stdout, который грейдер возвращает в ответе.
    """
    monkeypatch.setenv("STEPIK_GRADER_AI_KEY", "sk-should-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-should-not-leak")

    env = _posix_common.build_minimal_env()

    assert "STEPIK_GRADER_AI_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    # issue #726: PYTHON_COLORS=0 — гашение ANSI-раскраски traceback'а; набор
    # по-прежнему закрыт (ничего из окружения родителя сюда не просачивается).
    assert set(env) == {"PATH", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHON_COLORS"}


def test_build_minimal_env_path_points_at_interpreter() -> None:
    """PATH ведёт на каталог интерпретатора — как у Linux/Windows backend'ов."""
    import sys

    env = _posix_common.build_minimal_env()

    assert env["PATH"] == str(pathlib.Path(sys.executable).resolve().parent)
    assert env["PYTHONUTF8"] == "1"


def test_macos_backend_passes_explicit_env() -> None:
    """macOS-backend обязан передавать env явно, иначе наследуется окружение.

    Проверка на уровне исходника — намеренно: ``sandbox-exec`` существует
    только на Darwin, поэтому полноценный прогон backend'а на других ОС
    невозможен, а регрессия (пропавший ``env=``) тихая и дорогая: ``env=None``
    в ``run_argv_with_limits`` означает наследование ``os.environ`` (#627).
    """
    source = pathlib.Path(_macos.__file__).read_text(encoding="utf-8")

    assert "env=_posix_common.build_minimal_env()" in source, (
        "macOS-backend перестал передавать минимальное окружение — "
        "недоверенный код снова получит секреты грейдера"
    )


# ---------------------------------------------------------------------------
# #628 — атомарность записи секретов
# ---------------------------------------------------------------------------


def test_save_secrets_leaves_no_temp_files(tmp_path: pathlib.Path) -> None:
    """После успешной записи в каталоге остаётся только сам secrets.json."""
    secrets_file = tmp_path / "secrets.json"

    save_secrets(secrets_file, {"access_token": "AT"})

    assert list(tmp_path.iterdir()) == [secrets_file]


def test_save_secrets_failed_write_leaves_original_intact(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой записи не затирает уже сохранённые секреты (issue #628).

    Прежний путь (``os.open`` с ``O_TRUNC``) обрезал цель ДО записи, поэтому
    краш или конкурентное чтение в этот момент давали усечённый JSON и потерю
    ``refresh_token`` — пользователь заново проходил браузерный OAuth.
    """
    secrets_file = tmp_path / "secrets.json"
    save_secrets(secrets_file, {"refresh_token": "OLD"})

    def failing_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", failing_fsync)

    with pytest.raises(OSError, match="disk full"):
        save_secrets(secrets_file, {"refresh_token": "NEW"})

    assert json.loads(secrets_file.read_text(encoding="utf-8")) == {"refresh_token": "OLD"}
    assert list(tmp_path.iterdir()) == [secrets_file], "temp-файл должен быть убран"


def test_save_secrets_overwrites_existing_content(tmp_path: pathlib.Path) -> None:
    """Успешная перезапись заменяет содержимое целиком, без остатков старого."""
    secrets_file = tmp_path / "secrets.json"

    save_secrets(secrets_file, {"refresh_token": "OLD", "extra": "dropped"})
    save_secrets(secrets_file, {"refresh_token": "NEW"})

    assert json.loads(secrets_file.read_text(encoding="utf-8")) == {"refresh_token": "NEW"}
