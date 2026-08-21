"""issue #632: LocalRunner не наследует секреты грейдера в subprocess решения.

Дефолтный ``LocalRunner`` исполняет код БЕЗ ОС-изоляции и копирует окружение
грейдера (а под ``--serve`` без ``--sandbox`` — окружение всего сервера).
Собственные секреты грейдера — BYOK AI-ключ (``CONFIG.ai_api_key_env``) и
типовые ``*_TOKEN``/``*_SECRET`` оператора — коду решения не нужны и утекать в
него не должны. Скраб по denylist вырезает секретное, но сохраняет
project-import (``PATH``/``PYTHONPATH``), нужный трассировщику.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import LocalRunner, RunSpec, _scrub_secret_env

# ---------------------------------------------------------------------------
# Юнит: denylist-скраб словаря окружения
# ---------------------------------------------------------------------------


def test_scrub_removes_configured_ai_key() -> None:
    """Сконфигурированное имя AI-ключа удаляется, остальное — нет."""
    env = {CONFIG.ai_api_key_env: "super-secret", "PATH": "/usr/bin"}

    _scrub_secret_env(env)

    assert CONFIG.ai_api_key_env not in env
    assert env["PATH"] == "/usr/bin"


def test_scrub_removes_common_secret_names() -> None:
    """Переменные с типовой секрет-подстрокой в имени вычищаются все."""
    env = {
        "MY_TOKEN": "t",
        "DB_PASSWORD": "p",
        "AWS_SECRET_ACCESS_KEY": "s",
        "SERVICE_APIKEY": "a",
        "GH_CREDENTIALS": "c",
    }

    _scrub_secret_env(env)

    assert env == {}


def test_scrub_preserves_non_secret_env() -> None:
    """PATH/PYTHONPATH/VIRTUAL_ENV/HOME не трогаются — иначе сломается import."""
    env = {"PATH": "/usr/bin", "PYTHONPATH": "/proj", "VIRTUAL_ENV": "/venv", "HOME": "/home/x"}
    before = dict(env)

    _scrub_secret_env(env)

    assert env == before


def test_scrub_is_case_insensitive() -> None:
    """Секрет узнаётся по имени независимо от регистра."""
    env = {"my_token": "t", "Db_Secret": "s", "path": "/usr/bin"}

    _scrub_secret_env(env)

    assert env == {"path": "/usr/bin"}


# ---------------------------------------------------------------------------
# Интеграция: реальный subprocess не видит секрет грейдера
# ---------------------------------------------------------------------------


def _echo_env_script(tmp_path: pathlib.Path, var: str) -> pathlib.Path:
    script = tmp_path / "echo_env.py"
    script.write_text(
        f"import os\nprint(os.environ.get({var!r}, '<<absent>>'))\n", encoding="utf-8"
    )
    return script


def test_child_process_cannot_see_ai_key(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключ из окружения грейдера не доходит до subprocess решения."""
    monkeypatch.setenv(CONFIG.ai_api_key_env, "super-secret-value")
    spec = RunSpec(
        path=_echo_env_script(tmp_path, CONFIG.ai_api_key_env),
        stdin=None,
        timeout=30.0,
        measure_memory=False,
    )

    outcome = LocalRunner().run(spec)
    printed = outcome.stdout.decode("utf-8", errors="replace")

    assert "super-secret-value" not in printed
    assert "<<absent>>" in printed


def test_child_process_still_sees_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контроль: PATH переживает скраб — project-import остаётся возможен."""
    monkeypatch.setenv("PATH", "/custom/bin")
    spec = RunSpec(
        path=_echo_env_script(tmp_path, "PATH"),
        stdin=None,
        timeout=30.0,
        measure_memory=False,
    )

    outcome = LocalRunner().run(spec)
    printed = outcome.stdout.decode("utf-8", errors="replace")

    assert "<<absent>>" not in printed


# ---------------------------------------------------------------------------
# SEC-2-06 (issue #996) — доступ без секрета в значении
#
# Denylist ловил слова «секрет», «токен», «пароль» — и пропускал то, что
# секрета в себе не содержит, а доступ даёт: `SSH_AUTH_SOCK` (сокет агента:
# решение подпишет что угодно ключами пользователя, не увидев самого ключа),
# `XAUTHORITY` (доступ к дисплею), `GNUPGHOME`, `KUBECONFIG`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,why",
    [
        ("SSH_AUTH_SOCK", "сокет ssh-агента: подпись чем угодно без раскрытия ключа"),
        ("XAUTHORITY", "cookie X-сервера: доступ к дисплею пользователя"),
        ("GNUPGHOME", "связка ключей GPG"),
        ("KUBECONFIG", "доступ к кластеру"),
        ("SSH_AGENT_PID", "адрес того же агента"),
        ("GPG_AGENT_INFO", "то же для GPG"),
    ],
)
def test_scrub_removes_access_without_a_secret_in_the_value(name: str, why: str) -> None:
    """Переменная-дескриптор доступа не достаётся решению."""
    env = {name: "/tmp/доступ", "PATH": "/usr/bin"}

    _scrub_secret_env(env)

    assert name not in env, why
    assert env["PATH"] == "/usr/bin", "безобидные переменные остаются на месте"


def test_scrub_keeps_project_import_variables() -> None:
    """PYTHONPATH и VIRTUAL_ENV не трогаются — на них держится project-import."""
    env = {"PYTHONPATH": "/проект", "VIRTUAL_ENV": "/проект/.venv", "PATH": "/usr/bin"}

    _scrub_secret_env(env)

    assert env == {"PYTHONPATH": "/проект", "VIRTUAL_ENV": "/проект/.venv", "PATH": "/usr/bin"}
