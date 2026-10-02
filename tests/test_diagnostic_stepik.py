"""Tests for diagnostic_stepik.py — OAuth-диагностика шага (issue #1017).

Диагностику запускают именно тогда, когда «что-то не так с токеном», поэтому
проверяется главное: она поднимает сессию тем же путём, что загрузчик и стенд
корпуса (валидный ``access_token`` → обмен ``refresh_token`` → и только потом
браузер), а не требует браузерной авторизации при живых токенах.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from stepik_grader import diagnostic_stepik
from stepik_grader.core import diagnostics, oauth_flow, stepik_client


def _live_secrets() -> dict[str, Any]:
    """Секреты с заведомо валидным access_token — браузер не нужен."""
    return {
        "client_id": "cid",
        "client_secret": "csecret",
        "redirect_uri": "http://localhost:8080/callback",
        "access_token": "live-token",
        "refresh_token": "refresh",
        "expires_at": time.time() + 3600,
    }


def test_diagnostic_uses_shared_session_factory() -> None:
    """У диагностики нет своей реализации авторизации (issue #1017).

    Вторая реализация — это второй набор приоритетов: прежняя версия звала
    ``authorize_via_browser`` сразу и не знала о сохранённых токенах, поэтому
    при полностью рабочем ``secrets.json`` диагностика падала по таймауту
    ожидания кода, тогда как загрузчик той же машины работал молча.
    """
    assert diagnostic_stepik.create_user_session is oauth_flow.create_user_session


def test_valid_token_does_not_open_browser(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой access_token → сессия без браузера, ни одного OAuth-редиректа."""

    def _explode(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("браузерная авторизация при валидном токене")

    monkeypatch.setattr("stepik_grader.core.stepik_client.authorize_via_browser", _explode)

    session = diagnostic_stepik.create_user_session(_live_secrets(), tmp_path / "secrets.json")

    assert session.headers["Authorization"] == "Bearer live-token"


def test_help_prints_usage_instead_of_asking(capsys: pytest.CaptureFixture[str]) -> None:
    """issue #997 (RUN-4-03): ``--help`` печатает справку, а не падает EOFError.

    Модуль начинался с трёх голых ``input()``, поэтому флаг съедался первым
    приглашением, а на закрытом stdin прогон обрывался трейсбеком — в скрипте и
    в CI инструмент был неработоспособен.
    """
    with pytest.raises(SystemExit) as exc:
        diagnostic_stepik.main(["--help"])

    assert exc.value.code == 0
    assert "--url" in capsys.readouterr().out


def test_missing_url_returns_nonzero_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Нет URL и нет интерактива — понятный отказ и ненулевой код (JRN-3A-03)."""
    code = diagnostic_stepik.main(["--url", ""])

    assert code == 1
    assert "URL" in capsys.readouterr().out


def test_failure_returns_nonzero_and_names_the_log(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Сбой диагностики виден процессу и называет файл с подробностями.

    issue #997: JRN-3A-03 (код возврата всегда 0 — триаж-инструмент непригоден
    как проверка) и OPS-1-03 (стек пишется в лог, но пользователю о нём не
    сообщают).
    """
    monkeypatch.setattr(
        diagnostic_stepik,
        "load_secrets_dict",
        lambda _path: (_ for _ in ()).throw(ValueError("нет secrets.json")),
    )

    code = diagnostic_stepik.main(
        ["--url", "https://stepik.org/lesson/1/step/1", "--out", str(tmp_path)]
    )

    # Переводы строк схлопываются: rich переносит длинный путь по ширине
    # терминала, и посимвольное сравнение иначе ловило бы вёрстку, а не факт.
    out = capsys.readouterr().out.replace("\n", "")
    assert code == 1
    assert "нет secrets.json" in out
    assert str((tmp_path / "grader.log").resolve()) in out


def test_saved_report_has_secrets_redacted(tmp_path: pathlib.Path) -> None:
    """issue #950 (OPS-1-02): дамп ответа API не выносит токены на диск.

    Файлы ``stepik_diagnostics/*.json`` создаются, чтобы приложить их к issue,
    поэтому попадание туда токена — не гипотетический риск, а обычный сценарий
    использования.
    """
    from stepik_grader.core.diag_log import register_secret

    token = "super-secret-access-token"
    register_secret(token)

    path = diagnostic_stepik.save_json(tmp_path, "step_debug.json", {"access_token": token})

    saved = path.read_text(encoding="utf-8")
    assert token not in saved
    assert "redacted" in saved


def test_expired_token_is_refreshed_without_browser(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Истёкший access_token обменивается по refresh_token, тоже без браузера.

    Ровно тот случай, что воспроизведён на машине владельца: ``expires_at`` в
    прошлом, ``refresh_token`` живой — загрузчик работал, диагностика открывала
    браузер и ждала 120 секунд.
    """

    def _explode(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("браузерная авторизация при живом refresh_token")

    monkeypatch.setattr("stepik_grader.core.stepik_client.authorize_via_browser", _explode)
    monkeypatch.setattr(
        "stepik_grader.core.stepik_client.refresh_access_token",
        lambda *_a, **_k: {"access_token": "fresh-token", "expires_in": 3600},
    )
    secrets = _live_secrets()
    secrets["expires_at"] = time.time() - 1
    secrets_path = tmp_path / "secrets.json"

    session = diagnostic_stepik.create_user_session(secrets, secrets_path)

    assert session.headers["Authorization"] == "Bearer fresh-token"
    assert secrets_path.is_file()  # новая пара токенов сохранена


# --- две поверхности одного реестра проверок (issue #982) ----------------------


def test_busy_port_is_not_reported_as_bad_credentials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Занятый порт называется занятым портом, и рядом стоит, что делать.

    Находка ``JRN-3A-04``: пользователь шёл перевыпускать OAuth-приложение
    из-за чужого процесса на 8080. Причина и следующий шаг берутся из общего
    реестра, поэтому диагностика и основной сценарий не могут разойтись.
    """
    monkeypatch.setattr(
        diagnostic_stepik,
        "load_secrets_dict",
        lambda _path: (_ for _ in ()).throw(
            stepik_client.OAuthCallbackPortBusy("Порт 8080 занят другим процессом")
        ),
    )

    code = diagnostic_stepik.main(
        ["--url", "https://stepik.org/lesson/1/step/1", "--out", str(tmp_path)]
    )

    out = capsys.readouterr().out.replace("\n", "")
    assert code == 1
    assert "порт" in out.lower()
    assert "redirect_uri" in out, "не сказано, что делать пользователю"


def test_failure_writes_the_environment_report(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отчёт для мейнтейнера пишется там, где он и нужен, — при сбое.

    Поверхность 2 (issue #982): файл прикладывают к issue, понимать его
    содержимое пользователю не требуется.
    """
    monkeypatch.setattr(
        diagnostic_stepik,
        "load_secrets_dict",
        lambda _path: (_ for _ in ()).throw(ValueError("нет secrets.json")),
    )

    diagnostic_stepik.main(["--url", "https://stepik.org/lesson/1/step/1", "--out", str(tmp_path)])

    report = json.loads(
        (tmp_path / diagnostic_stepik.ENVIRONMENT_REPORT_NAME).read_text(encoding="utf-8")
    )
    ids = [entry["id"] for entry in report["checks"]]
    assert ids == [check.id for check in diagnostics.CHECKS]
    for entry in report["checks"]:
        assert entry["subject"] and entry["remedy"], entry


def test_environment_report_is_redacted(tmp_path: pathlib.Path) -> None:
    """Новая точка дампа не может повторить ``OPS-1-02``.

    Редакция одна на весь модуль и живёт в ``save_json``, поэтому отчёт по
    проверкам проходит через неё же — а не через собственную копию правил.
    """
    findings = [
        diagnostics.Finding(
            check=diagnostics.CHECKS[0],
            outcome=diagnostics.Outcome(
                diagnostics.Status.FAIL,
                "diag_detail_secrets_unreadable",
                {
                    "path": "secrets.json",
                    "error": "access_token=ghp_0123456789abcdefghijABCDEFGHIJ0123",
                },
            ),
        )
    ]

    path = diagnostic_stepik.save_json(
        tmp_path,
        diagnostic_stepik.ENVIRONMENT_REPORT_NAME,
        diagnostic_stepik.build_environment_report(findings),
    )

    assert "ghp_0123456789abcdefghijABCDEFGHIJ0123" not in path.read_text(encoding="utf-8")


def test_unknown_failure_invents_no_cause() -> None:
    """Причина неизвестна — молчим, а не подставляем ближайшую.

    Выдуманная причина хуже показанной ошибки: именно так занятый порт и стал
    «неверными учётными данными».
    """
    assert diagnostic_stepik.explain_failure(ValueError("что-то своё")) == []
