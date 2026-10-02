"""Тесты движка проверок окружения (issue #982).

Предмет — три инварианта, ради которых движок и заводился: проверка это
**данные**, движок **ничего не печатает и не чинит**, а причина сбоя берётся из
одного реестра, поэтому две поверхности не могут назвать её по-разному.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import requests

from stepik_grader.core import diagnostics, stepik_client

_VALID_SECRETS: dict[str, Any] = {
    "client_id": "id",
    "client_secret": "secret",
    "redirect_uri": "http://localhost:8080/",
}


def _write_secrets(tmp_path: pathlib.Path, payload: dict[str, Any] | str) -> pathlib.Path:
    """Положить secrets.json (объект либо сырой текст) и вернуть путь."""
    path = tmp_path / "secrets.json"
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def _context(path: pathlib.Path, **kwargs: Any) -> diagnostics.Context:
    """Контекст без сети: сетевую пробу тесты включают явно."""
    kwargs.setdefault("network", False)
    return diagnostics.Context(secrets_path=path, **kwargs)


# --- реестр как данные ---------------------------------------------------------


def test_every_check_is_data_with_both_texts() -> None:
    """У каждой проверки есть, что проверяю, и что делать пользователю.

    Проверка без ``remedy`` — отчёт о состоянии, а не ответ: ровно то, чем была
    прежняя диагностика, собравшая восемь находок и ни одной про пользу.
    """
    assert diagnostics.CHECKS, "пустой реестр — движок без предмета"
    for check in diagnostics.CHECKS:
        assert check.id, "проверка без идентификатора"
        assert check.subject.startswith("diag_check_"), check.id
        assert check.remedy.startswith("diag_remedy_"), check.id


def test_check_ids_are_unique() -> None:
    """Идентификатор адресует проверку — двойник сделал бы адресацию ложной."""
    ids = [check.id for check in diagnostics.CHECKS]

    assert len(ids) == len(set(ids)), ids


def test_required_checks_exist_and_come_first() -> None:
    """Предпосылка объявлена существующей проверкой и стоит в реестре раньше."""
    seen: set[str] = set()
    for check in diagnostics.CHECKS:
        for required in check.requires:
            assert diagnostics.check_by_id(required) is not None, required
            assert required in seen, f"{check.id}: предпосылка {required} стоит позже"
        seen.add(check.id)


def test_check_by_id_is_none_for_unknown() -> None:
    """Неизвестный идентификатор — ``None``, а не выдуманная проверка."""
    assert diagnostics.check_by_id("нет-такой") is None


# --- пробы: спрашивают состояние -----------------------------------------------


def test_missing_secrets_file_is_a_finding(tmp_path: pathlib.Path) -> None:
    """Нет файла — находка с путём, а не трейсбек."""
    findings = diagnostics.run_checks(_context(tmp_path / "secrets.json"))
    by_id = {finding.id: finding for finding in findings}

    assert by_id["secrets-file"].status is diagnostics.Status.FAIL
    assert by_id["secrets-file"].outcome.detail == "diag_detail_secrets_missing"


def test_unreadable_secrets_names_the_parse_error(tmp_path: pathlib.Path) -> None:
    """Битый JSON — находка с текстом ошибки, а не «файл есть»."""
    path = _write_secrets(tmp_path, "{это не json")

    finding = diagnostics.run_check(diagnostics.CHECKS[0], _context(path))

    assert finding.status is diagnostics.Status.FAIL
    assert finding.outcome.detail == "diag_detail_secrets_unreadable"


def test_valid_secrets_pass(tmp_path: pathlib.Path) -> None:
    """Полный secrets.json проходит обе первые проверки."""
    path = _write_secrets(tmp_path, _VALID_SECRETS)

    findings = {f.id: f for f in diagnostics.run_checks(_context(path))}

    assert findings["secrets-file"].status is diagnostics.Status.OK
    assert findings["oauth-credentials"].status is diagnostics.Status.OK


def test_absent_token_is_skip_not_failure(tmp_path: pathlib.Path) -> None:
    """Первый запуск — не поломка.

    Сохранённого токена ещё нет, и назвать это находкой значило бы приучить
    читателя отчёта пропускать красные строки.
    """
    path = _write_secrets(tmp_path, _VALID_SECRETS)

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["saved-token"]

    assert finding.status is diagnostics.Status.SKIP
    assert finding.outcome.detail == "diag_detail_token_absent"


def test_expired_token_is_skip(tmp_path: pathlib.Path) -> None:
    """Истёкший токен обменяют по refresh_token — тоже не находка."""
    path = _write_secrets(tmp_path, {**_VALID_SECRETS, "access_token": "t", "expires_at": 1})

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["saved-token"]

    assert finding.status is diagnostics.Status.SKIP
    assert finding.outcome.detail == "diag_detail_token_expired"


def test_busy_callback_port_is_named_as_such(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Занятый порт называется занятым портом.

    Находка ``JRN-3A-04``: он выдавался за неверные учётные данные, потому что
    причину называл обработчик авторизации, ничего не знавший о bind'е.
    """
    path = _write_secrets(tmp_path, _VALID_SECRETS)
    monkeypatch.setattr(stepik_client, "callback_port_is_free", lambda host, port: False)

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["callback-port"]

    assert finding.status is diagnostics.Status.FAIL
    assert finding.outcome.detail == "diag_detail_port_busy"
    assert finding.outcome.params["port"] == 8080


def test_free_callback_port_passes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Свободный порт — ``OK`` с адресом, который проверяли."""
    path = _write_secrets(tmp_path, _VALID_SECRETS)
    monkeypatch.setattr(stepik_client, "callback_port_is_free", lambda host, port: True)

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["callback-port"]

    assert finding.status is diagnostics.Status.OK
    assert finding.outcome.params["host"] == "localhost"


def test_port_check_skips_when_redirect_uri_has_no_port(tmp_path: pathlib.Path) -> None:
    """Порт не выведен из redirect_uri — ``SKIP``, а не выдуманный порт."""
    path = _write_secrets(tmp_path, {**_VALID_SECRETS, "redirect_uri": "не-адрес"})

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["callback-port"]

    assert finding.status is diagnostics.Status.SKIP
    assert finding.outcome.detail == "diag_detail_port_unknown"


def test_network_probe_off_is_skip_with_a_reason(tmp_path: pathlib.Path) -> None:
    """Офлайн-прогон отличается от прогона, где сеть проверили."""
    path = _write_secrets(tmp_path, _VALID_SECRETS)

    finding = {f.id: f for f in diagnostics.run_checks(_context(path))}["api-reachable"]

    assert finding.status is diagnostics.Status.SKIP
    assert finding.outcome.detail == "diag_detail_network_off"


def test_unreachable_host_is_a_finding(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обрыв сети называется обрывом сети, а не отказом учётных данных."""
    path = _write_secrets(tmp_path, _VALID_SECRETS)

    def _boom(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("нет маршрута")

    monkeypatch.setattr(requests, "get", _boom)

    finding = diagnostics.run_checks(
        diagnostics.Context(secrets_path=path, network=True, api_host="https://stepik.invalid"),
        only=["api-reachable"],
    )[0]

    assert finding.status is diagnostics.Status.FAIL
    assert finding.outcome.detail == "diag_detail_api_unreachable"


def test_any_http_answer_means_reachable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Предмет проверки — досягаемость, а не авторизация: 403 тоже ответ."""

    class _Response:
        status_code = 403

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Response())

    finding = diagnostics.run_checks(
        diagnostics.Context(
            secrets_path=tmp_path / "secrets.json",
            network=True,
            api_host="https://stepik.example",
        ),
        only=["api-reachable"],
    )[0]

    assert finding.status is diagnostics.Status.OK
    assert finding.outcome.params["status"] == 403


def test_api_host_is_read_from_the_module_not_frozen_on_import(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переопределённый хост виден проверке (находка ``STR-3-06``, тот же класс).

    Значение, снятое на импорте, означало бы, что диагностика проверяет боевой
    stepik.org, пока грейдер ходит на стенд.
    """
    monkeypatch.setattr(stepik_client, "API_HOST", "https://stand.example")

    assert _context(tmp_path / "secrets.json").host() == "https://stand.example"


# --- поведение движка ----------------------------------------------------------


def test_failed_prerequisite_skips_dependents_instead_of_repeating(
    tmp_path: pathlib.Path,
) -> None:
    """Одна настоящая причина вместо каскада её пересказов.

    Отсутствующий ``secrets.json`` — ровно одна находка. Проверки учётных
    данных и порта молчат со ссылкой на неё, а не сообщают «поля неполны» и
    «порт не определён»: формально верно, по сути та же причина трижды.
    """
    findings = {f.id: f for f in diagnostics.run_checks(_context(tmp_path / "нет.json"))}

    failed = [f.id for f in findings.values() if f.status is diagnostics.Status.FAIL]
    assert failed == ["secrets-file"], failed
    for dependent in ("oauth-credentials", "saved-token", "callback-port"):
        assert findings[dependent].status is diagnostics.Status.SKIP, dependent


def test_blocking_names_the_root_cause_not_the_neighbour(tmp_path: pathlib.Path) -> None:
    """Блокировка идёт по цепочке и называет корень, а не предыдущий шаг.

    ``callback-port`` зависит от ``oauth-credentials``, а тот — от
    ``secrets-file``. Читателю нужен файл, которого нет, а не сообщение про
    промежуточное звено.
    """
    findings = {f.id: f for f in diagnostics.run_checks(_context(tmp_path / "нет.json"))}

    assert findings["callback-port"].outcome.params["blocker"] == "secrets-file"


def test_only_runs_the_named_checks(tmp_path: pathlib.Path) -> None:
    """Поверхность точки сбоя зовёт одну проверку, а не весь реестр."""
    findings = diagnostics.run_checks(_context(tmp_path / "нет.json"), only=["secrets-file"])

    assert [f.id for f in findings] == ["secrets-file"]


def test_a_crashing_probe_becomes_a_finding(tmp_path: pathlib.Path) -> None:
    """Проба не имеет права уронить обход: её падение — исход, а не трейсбек."""

    def _boom(_context: diagnostics.Context) -> diagnostics.Outcome:
        raise RuntimeError("проба сломалась")

    check = diagnostics.Check(
        id="взрыв",
        subject="diag_check_secrets_file",
        remedy="diag_remedy_secrets_file",
        probe=_boom,
    )

    finding = diagnostics.run_check(check, _context(tmp_path / "нет.json"))

    assert finding.status is diagnostics.Status.FAIL
    assert finding.outcome.detail == "diag_detail_probe_crashed"


def test_engine_prints_nothing(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Инвариант: движок возвращает данные, печатает поверхность."""
    diagnostics.run_checks(_context(_write_secrets(tmp_path, _VALID_SECRETS)))

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_engine_changes_nothing_on_disk(tmp_path: pathlib.Path) -> None:
    """Инвариант: диагностика не чинит — «запустите её» остаётся безопасным советом."""
    path = _write_secrets(tmp_path, _VALID_SECRETS)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    diagnostics.run_checks(_context(path))

    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert after == before


# --- сопоставление исключения с причиной ---------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (stepik_client.OAuthCallbackPortBusy("порт 8080 занят"), "callback-port"),
        (stepik_client.StepikNetworkError("нет сети"), "api-reachable"),
        (FileNotFoundError("secrets.json"), "secrets-file"),
        (KeyError("client_id"), "oauth-credentials"),
    ],
)
def test_exception_maps_to_the_check_that_names_its_cause(
    error: BaseException, expected: str
) -> None:
    """Причину называет реестр, а не обработчик — иначе два места разойдутся."""
    check = diagnostics.explain_exception(error)

    assert check is not None and check.id == expected


def test_unknown_exception_invents_no_cause() -> None:
    """Неизвестный тип — ``None``: выдуманная причина хуже показанной ошибки."""
    assert diagnostics.explain_exception(ValueError("что-то своё")) is None


def test_port_busy_is_not_called_a_credentials_problem() -> None:
    """Живая проверка находки ``JRN-3A-04`` — та самая подмена причины."""
    check = diagnostics.explain_exception(stepik_client.OAuthCallbackPortBusy("занят"))

    assert check is not None
    assert check.id != "oauth-credentials"
    assert check.remedy == "diag_remedy_callback_port"
