"""Адаптеры веб-слоя не расходятся с фиксами соседей (issue #966).

Оба дефекта одного класса: адаптеры копировали друг у друга приём, а
последующая починка распространялась на один экземпляр. Тесты проверяют не
«код написан», а обещание докстринга: адаптер возвращает контрактный ответ и
никогда не роняет обработчик, а очередь ищется относительно рабочей директории
сервера, а не текущей директории процесса.
"""

from __future__ import annotations

import pathlib

import pytest
import requests

from stepik_grader import config
from stepik_grader.web import glossary_adapter, reference_adapter


@pytest.fixture
def secrets(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Валидный secrets.json, чтобы дойти до обновления токена."""
    path = tmp_path / "secrets.json"
    path.write_text('{"client_id": "id", "client_secret": "секрет"}', encoding="utf-8")
    monkeypatch.setattr(reference_adapter, "secrets_path_for", lambda base: path)
    monkeypatch.setattr(reference_adapter, "load_secrets_dict", lambda p: {"client_id": "id"})
    return path


def test_network_failure_gives_contract_answer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, secrets: pathlib.Path
) -> None:
    """ADD-1-01: обрыв сети при обновлении токена — `ok: false`, а не 500."""

    def boom(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("сеть отвалилась")

    monkeypatch.setattr(reference_adapter, "try_create_session_without_browser", boom)

    result = reference_adapter.import_reference("задача", workspace=tmp_path)

    assert result["ok"] is False
    assert "Сетевая ошибка" in result["message"]


def test_secrets_write_failure_gives_contract_answer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, secrets: pathlib.Path
) -> None:
    """Тот же путь: перезапись secrets.json упала на OSError."""

    def boom(*args: object, **kwargs: object) -> None:
        raise PermissionError("только чтение")

    monkeypatch.setattr(reference_adapter, "try_create_session_without_browser", boom)

    result = reference_adapter.import_reference("задача", workspace=tmp_path)

    assert result["ok"] is False
    assert "обновлённый токен" in result["message"]


def test_missing_queue_is_relative_to_workspace(tmp_path: pathlib.Path) -> None:
    """ADD-1-03: очередь ищется от рабочей директории сервера, а не от cwd."""
    resolved = glossary_adapter.missing_queue_path(tmp_path)

    assert resolved.is_absolute()
    assert resolved.parent == tmp_path


def test_absolute_queue_setting_is_left_alone(tmp_path: pathlib.Path) -> None:
    """Настроенный абсолютный путь — воля пользователя, workspace его не двигает."""
    absolute = tmp_path / "явно" / "очередь.db"
    config.override_config(glossary_missing_queue=str(absolute))
    try:
        assert glossary_adapter.missing_queue_path(tmp_path) == absolute
    finally:
        config.reset_config_cache()


def test_setting_is_read_at_call_time(tmp_path: pathlib.Path) -> None:
    """Значение не вмораживается на импорте — иначе флаги CLI сюда не доходят."""
    config.override_config(glossary_missing_queue="очередь-из-настроек.db")
    try:
        assert glossary_adapter.missing_queue_path(tmp_path).name == "очередь-из-настроек.db"
    finally:
        config.reset_config_cache()


def test_without_workspace_behaviour_is_unchanged() -> None:
    """Без workspace поведение прежнее: CLI-потребители ничего не замечают."""
    assert glossary_adapter.missing_queue_path(None) == pathlib.Path(
        config.get_config().glossary_missing_queue
    )


def test_routes_pass_workspace_to_the_queue() -> None:
    """Маршруты обязаны передавать workspace — иначе фикс мёртв.

    Проверяется исходник, а не поведение: поднимать сервер ради двух вызовов
    дороже, чем сверить, что ни один из них не остался без `queue_path`.
    """
    source = (
        pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "api_routes.py"
    ).read_text(encoding="utf-8")

    for call in ("glossary_missing(", "queue_code_gaps("):
        index = source.index("_json(" + call) if "_json(" + call in source else source.rindex(call)
        tail = source[index : index + 260]
        assert "queue_path=" in tail, f"{call} зовётся без queue_path — очередь снова уедет к cwd"
