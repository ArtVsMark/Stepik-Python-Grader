"""Образцы конфигурации, которые проект раздаёт наружу, проверяются кодом.

Правило 155 каталога: заготовка, которую проект отдаёт потребителю, применяется
у себя тем же способом, каким предлагается ему, — иначе она расходится с
практикой молча. Проверка «файл существует» границей не считается: она отвечает
на другой вопрос.

Предмет здесь буквальный. `stepik_config.json.example` и `secrets.json.example`
лежат в корне, README велит скопировать их и заполнить, — и до этого теста их не
открывал НИ ОДИН тест и ни один скрипт. Ключ, переименованный в коде, оставил бы
образец прежним, а первый же новый пользователь получил бы отказ на файле,
который ему выдал сам проект.

Тест поэтому спрашивает не форму файла, а КОД: набор ключей сверяется с тем, что
читают реальные потребители — `core/stepik_client.py` у секретов и
`downloader_config.normalize_config_paths` у конфигурации.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent.parent

#: Ключи, которые читает OAuth-обмен (`core/stepik_client.py`, `core/oauth_flow.py`).
_SECRET_KEYS = ("client_id", "client_secret", "redirect_uri", "access_token")

#: Ключи, без которых `normalize_config_paths` уходит в интерактивный до-опрос.
_CONFIG_KEYS = ("root_dir", "secrets_path")


@pytest.mark.parametrize("name", ["secrets.json.example", "stepik_config.json.example"])
def test_shipped_example_is_valid_json(name: str) -> None:
    """Образец разбирается: его копируют как есть, а не читают глазами."""
    payload = json.loads((_ROOT / name).read_text(encoding="utf-8"))

    assert isinstance(payload, dict), f"{name}: ожидался объект"


def test_secrets_example_carries_every_key_the_client_reads() -> None:
    """Каждый ключ, который спрашивает обмен токеном, есть в образце."""
    payload = json.loads((_ROOT / "secrets.json.example").read_text(encoding="utf-8"))

    missing = [key for key in _SECRET_KEYS if key not in payload]

    assert not missing, (
        f"secrets.json.example не содержит {missing}: код читает их по имени "
        "(core/stepik_client.py), и пользователь получит KeyError на файле, "
        "который ему выдал проект"
    )


def test_config_example_survives_the_real_normalizer() -> None:
    """Образец конфигурации не отправляет реальный загрузчик в до-опрос.

    `normalize_config_paths` считает конфиг неполным, если `root_dir` или
    `secrets_path` пусты, и начинает интерактивный опрос. Образец, вызывающий
    опрос, обещает готовый файл и его не даёт.
    """
    payload = json.loads((_ROOT / "stepik_config.json.example").read_text(encoding="utf-8"))

    empty = [key for key in _CONFIG_KEYS if not str(payload.get(key, "")).strip()]

    assert not empty, f"stepik_config.json.example: {empty} пусты — загрузчик уйдёт в до-опрос"
