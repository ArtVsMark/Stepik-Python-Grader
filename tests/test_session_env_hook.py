"""Хук окружения облачного окна `.claude/hooks/session_env.sh` (issue #1560).

Хук ставит Python планки и собирает `.venv` — то есть ходит в сеть. Поэтому
главные свойства здесь — не «собрал», а «не навредил»: вне облака не делает
ничего, сбой сети не роняет старт окна и не подсовывает в `PATH` недособранное
окружение, а повтор не пересобирает того, что уже собрано. Полная сборка
проверяется руками (см. описание PR): она требует сети и минуты времени.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_HOOK = pathlib.Path(__file__).parent.parent / ".claude" / "hooks" / "session_env.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="хук облачного окна — bash; облачный образ — Linux",
)


def _run(project: pathlib.Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Запустить хук с чистым окружением: только то, что передано явно."""
    base = {"HOME": str(project), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        ["bash", str(_HOOK)],
        cwd=project,
        env={**base, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    """Проект с планкой, которой в образе заведомо нет."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=9.99"\n', encoding="utf-8"
    )
    return tmp_path


def test_outside_the_cloud_it_does_nothing(project: pathlib.Path) -> None:
    """На машине владельца окружение его — хук не смеет его трогать."""
    env_file = project / "env"

    result = _run(project, CLAUDE_PROJECT_DIR=str(project), CLAUDE_ENV_FILE=str(env_file))

    assert result.returncode == 0
    assert result.stdout == ""
    assert not env_file.exists()
    assert not (project / ".venv").exists()


def test_without_a_floor_it_says_so_and_exits_zero(tmp_path: pathlib.Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    env_file = tmp_path / "env"

    result = _run(
        tmp_path,
        CLAUDE_CODE_REMOTE="true",
        CLAUDE_PROJECT_DIR=str(tmp_path),
        CLAUDE_ENV_FILE=str(env_file),
    )

    assert result.returncode == 0
    assert "планка не найдена" in result.stdout
    assert not env_file.exists()


def test_a_network_failure_names_the_step_and_keeps_path_clean(project: pathlib.Path) -> None:
    """Недоступный PyPI — строка с названным шагом, код 0, `PATH` не тронут."""
    env_file = project / "env"

    result = _run(
        project,
        CLAUDE_CODE_REMOTE="true",
        CLAUDE_PROJECT_DIR=str(project),
        CLAUDE_ENV_FILE=str(env_file),
        SESSION_ENV_UV_HOME=str(project / "uv"),
        PIP_INDEX_URL="https://pypi.invalid.example/simple",
        PIP_RETRIES="0",
        PIP_TIMEOUT="2",
    )

    assert result.returncode == 0
    assert "не удался" in result.stdout
    assert not env_file.exists(), "недособранное окружение не должно попасть в PATH"


def test_a_matching_stamp_only_exports_path(project: pathlib.Path) -> None:
    """Повтор дешёвый: метка совпала — ни одной установки, только `PATH`."""
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake = venv_bin / "python"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    cksum = (
        subprocess.run(
            ["cksum"],
            input=(project / "pyproject.toml").read_bytes(),
            capture_output=True,
            check=True,
        )
        .stdout.split()[0]
        .decode()
    )
    (project / ".venv" / ".session-env").write_text(f"9.99 {cksum}\n", encoding="utf-8")
    env_file = project / "env"

    result = _run(
        project,
        CLAUDE_CODE_REMOTE="true",
        CLAUDE_PROJECT_DIR=str(project),
        CLAUDE_ENV_FILE=str(env_file),
        SESSION_ENV_UV_HOME=str(project / "uv-not-used"),
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert env_file.read_text(encoding="utf-8") == f'export PATH="{project}/.venv/bin:$PATH"\n'
    assert not (project / "uv-not-used").exists()


def test_the_hook_is_registered_after_the_digest() -> None:
    """Дайджест правил — первым и без сети; окружение — вторым элементом."""
    import json

    settings = json.loads((_HOOK.parent.parent / "settings.json").read_text(encoding="utf-8"))
    commands = [hook["command"] for hook in settings["hooks"]["SessionStart"][0]["hooks"]]

    assert commands[0].endswith('session_start.py"')
    assert commands[1].endswith('session_env.sh"')
    assert os.access(_HOOK, os.X_OK), "хук без бита исполнения"
