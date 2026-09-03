"""diagnostics.py — движок проверок окружения: данные, а не печать (issue #982).

Архитектурный слой: core, application-facing. Реестр проверок, каждая из
которых — **данные**: что проверяю, как узнал, что делать пользователю.

**Зачем движок вообще.** По прежней диагностике аудит собрал восемь находок и
ни одной про пользу, и корень был не в них по отдельности: инструмент печатал
собственный текст, а основной сценарий — свой. При одной и той же поломке
пользователь получал два разных ответа, и правильным мог оказаться любой.
Занятый порт 8080 назывался неверными учётными данными — не потому, что кто-то
ошибся в строке, а потому, что причину называли два места независимо. Реестр
делает расхождение невозможным по построению: текст «что делать» существует в
одном экземпляре, и обе поверхности берут его отсюда.

**Чего движок не делает — и это инвариант, а не умолчание.**

* **не печатает** — возвращает :class:`Finding`; во что их превращать, решает
  поверхность (консоль пользователя, отчёт для мейнтейнера, будущий ``doctor``);
* **не чинит** — ни одна проверка не пишет на диск, не открывает браузер и не
  обновляет токен. Иначе получилась бы команда, меняющая систему под видом
  диагностики, и «запустите диагностику» перестало бы быть безопасным советом;
* **не решает за конфигурацию** — состояние читается, решения о поведении
  остаются в ``config.py`` и ``UserSettings``.

**Тексты живут в каталоге локалей, а не здесь.** Проверка несёт ключи
(``subject``/``remedy``), рендер — забота поверхности: движок обязан быть
пригоден и для отчёта, который читает мейнтейнер, и для строки, которую видит
пользователь на своём языке.

Запуск проверок::

    from stepik_grader.core import diagnostics

    context = diagnostics.Context(secrets_path=pathlib.Path("secrets.json"))
    findings = diagnostics.run_checks(context)
    failed = [f for f in findings if f.status is diagnostics.Status.FAIL]
"""

from __future__ import annotations

import enum
import json
import pathlib
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from stepik_grader.core import oauth_flow, stepik_client

__all__ = [
    "CHECKS",
    "Check",
    "Context",
    "Finding",
    "Outcome",
    "Status",
    "check_by_id",
    "explain_exception",
    "run_check",
    "run_checks",
]


class Status(enum.StrEnum):
    """Исход одной проверки.

    ``SKIP`` отделён от ``FAIL`` намеренно: «предмета нет» и «предмет плох» —
    разные ответы, и склейка их в один делает отчёт бесполезным. Сохранённого
    токена нет при первом запуске — это норма, а не поломка.
    """

    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Context:
    """Что проверкам разрешено знать об окружении.

    Снимок входных данных, а не второй источник конфигурации: пути и тумблеры
    приходят от вызывающей стороны, а хост API читается из модуля в момент
    вызова — переопределение ``STEPIK_GRADER_API_HOST`` обязано быть видно
    диагностике, иначе она проверяет боевой Stepik, пока грейдер ходит на стенд.

    Attributes:
        secrets_path: Путь к ``secrets.json``.
        network: Опрашивать ли сеть. ``False`` — сетевые проверки дают ``SKIP``
            с причиной, а не молчание: офлайн-прогон обязан отличаться от
            прогона, где сеть проверили и она в порядке.
        timeout: Таймаут сетевой проверки в секундах.
        api_host: Хост API; ``None`` — взять действующий из ``stepik_client``.
    """

    secrets_path: pathlib.Path
    network: bool = True
    timeout: float = 10.0
    api_host: str | None = None

    def host(self) -> str:
        """Действующий хост API — из модуля, а не из значения, снятого на импорте."""
        return self.api_host or stepik_client.API_HOST


@dataclass(frozen=True)
class Outcome:
    """Что проверка узнала: исход и ключ строки «как узнал»."""

    status: Status
    detail: str
    params: dict[str, Any] = field(default_factory=dict)


#: Проба — чистая функция состояния: получает контекст, возвращает исход.
Probe = Callable[[Context], Outcome]


@dataclass(frozen=True)
class Check:
    """Одна проверка как данные.

    Attributes:
        id: Устойчивый идентификатор — им проверку зовут поимённо и по нему
            исключение сопоставляется с причиной.
        subject: Ключ локали «что проверяю».
        remedy: Ключ локали «что делать пользователю».
        probe: Функция опроса состояния.
        requires: Проверки, без которых эта бессмысленна. Провалилась
            предыдущая — эта даёт ``SKIP``, а не второй вариант той же причины:
            каскад одинаковых находок и есть то, из-за чего отчёт перестают
            читать.
    """

    id: str
    subject: str
    remedy: str
    probe: Probe
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    """Результат проверки: сама проверка плюс то, что она узнала."""

    check: Check
    outcome: Outcome

    @property
    def id(self) -> str:
        """Идентификатор проверки."""
        return self.check.id

    @property
    def status(self) -> Status:
        """Исход проверки."""
        return self.outcome.status


# ---------------------------------------------------------------------------
# Пробы: спрашивают состояние и ничего не меняют
# ---------------------------------------------------------------------------


def _probe_secrets_file(context: Context) -> Outcome:
    """Читается ли ``secrets.json`` и является ли он JSON-объектом."""
    path = context.secrets_path
    if not path.exists():
        return Outcome(Status.FAIL, "diag_detail_secrets_missing", {"path": path})
    try:
        oauth_flow.load_secrets_dict(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return Outcome(
            Status.FAIL,
            "diag_detail_secrets_unreadable",
            {"path": path, "error": error},
        )
    return Outcome(Status.OK, "diag_detail_secrets_ok", {"path": path})


def _probe_oauth_credentials(context: Context) -> Outcome:
    """Есть ли в ``secrets.json`` все три поля OAuth-приложения."""
    try:
        _client_id, _secret, redirect_uri = oauth_flow.load_secrets(context.secrets_path)
    except (OSError, ValueError, KeyError) as error:
        return Outcome(Status.FAIL, "diag_detail_credentials_missing", {"error": error})
    return Outcome(
        Status.OK,
        "diag_detail_credentials_ok",
        {"redirect_uri": redirect_uri},
    )


def _probe_saved_token(context: Context) -> Outcome:
    """Жив ли сохранённый ``access_token``.

    Истёкший токен — не поломка: его обменяют по ``refresh_token``, а без него
    откроется браузер. Поэтому исход здесь ``SKIP``, а не ``FAIL``, — иначе
    отчёт называл бы находкой штатный первый запуск.
    """
    try:
        secrets = oauth_flow.load_secrets_dict(context.secrets_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return Outcome(Status.SKIP, "diag_detail_token_unreadable", {"error": error})
    if not str(secrets.get("access_token", "")).strip():
        return Outcome(Status.SKIP, "diag_detail_token_absent")
    if not stepik_client.token_is_valid(secrets):
        return Outcome(Status.SKIP, "diag_detail_token_expired")
    return Outcome(Status.OK, "diag_detail_token_ok")


def _redirect_endpoint(context: Context) -> tuple[str, int] | None:
    """Хост и порт из ``redirect_uri``; ``None`` — прочитать нечем."""
    try:
        _client_id, _secret, redirect_uri = oauth_flow.load_secrets(context.secrets_path)
    except (OSError, ValueError, KeyError):
        return None
    parsed = urlparse(redirect_uri)
    if not parsed.hostname or not parsed.port:
        return None
    return parsed.hostname, parsed.port


def _probe_callback_port(context: Context) -> Outcome:
    """Свободен ли локальный порт OAuth-колбэка.

    Самая частая причина, по которой авторизация не начинается вовсе, — и она
    не имеет отношения к учётным данным. Ответ берётся тем же bind'ом, что и у
    настоящего колбэк-сервера (:func:`stepik_client.callback_port_is_free`):
    свой ``socket.bind`` отвечал бы на другой вопрос.
    """
    endpoint = _redirect_endpoint(context)
    if endpoint is None:
        return Outcome(Status.SKIP, "diag_detail_port_unknown")
    host, port = endpoint
    try:
        free = stepik_client.callback_port_is_free(host, port)
    except (OSError, socket.gaierror) as error:
        return Outcome(
            Status.FAIL,
            "diag_detail_port_unresolvable",
            {"host": host, "port": port, "error": error},
        )
    if not free:
        return Outcome(Status.FAIL, "diag_detail_port_busy", {"host": host, "port": port})
    return Outcome(Status.OK, "diag_detail_port_free", {"host": host, "port": port})


def _probe_api_reachable(context: Context) -> Outcome:
    """Отвечает ли узел Stepik по сети.

    Предмет — досягаемость, а не авторизация: любой HTTP-ответ означает, что
    сеть и DNS работают, и дальше причину искать уже не здесь. Ретраев нет
    намеренно — проверка обязана отвечать быстро, а живучесть боевых запросов
    остаётся заботой ``stepik_client.make_session``.
    """
    if not context.network:
        return Outcome(Status.SKIP, "diag_detail_network_off")
    host = context.host()
    try:
        response = requests.get(host, timeout=context.timeout, allow_redirects=False)
    except requests.RequestException as error:
        return Outcome(
            Status.FAIL,
            "diag_detail_api_unreachable",
            {"host": host, "error": error},
        )
    return Outcome(
        Status.OK,
        "diag_detail_api_ok",
        {"host": host, "status": response.status_code},
    )


# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------

#: Проверки в порядке зависимости: сначала то, без чего остальное не имеет
#: смысла. Порядок здесь — часть данных: он же порядок разделов отчёта.
CHECKS: tuple[Check, ...] = (
    Check(
        id="secrets-file",
        subject="diag_check_secrets_file",
        remedy="diag_remedy_secrets_file",
        probe=_probe_secrets_file,
    ),
    Check(
        id="oauth-credentials",
        subject="diag_check_oauth_credentials",
        remedy="diag_remedy_oauth_credentials",
        probe=_probe_oauth_credentials,
        requires=("secrets-file",),
    ),
    Check(
        id="saved-token",
        subject="diag_check_saved_token",
        remedy="diag_remedy_saved_token",
        probe=_probe_saved_token,
        requires=("secrets-file",),
    ),
    Check(
        id="callback-port",
        subject="diag_check_callback_port",
        remedy="diag_remedy_callback_port",
        probe=_probe_callback_port,
        requires=("oauth-credentials",),
    ),
    Check(
        id="api-reachable",
        subject="diag_check_api_reachable",
        remedy="diag_remedy_api_reachable",
        probe=_probe_api_reachable,
    ),
)

#: Исключение → проверка, которая называет ЕГО причину. Ровно этой таблицы не
#: хватало, когда занятый порт объявлялся неверными учётными данными: тип
#: ``OAuthCallbackPortBusy`` уже существовал, но связать его с текстом «что
#: делать» было нечем, и обработчик доставал общий.
_EXCEPTION_CHECKS: tuple[tuple[type[BaseException], str], ...] = (
    (stepik_client.OAuthCallbackPortBusy, "callback-port"),
    (stepik_client.StepikNetworkError, "api-reachable"),
    (FileNotFoundError, "secrets-file"),
    (IsADirectoryError, "secrets-file"),
    (json.JSONDecodeError, "secrets-file"),
    (KeyError, "oauth-credentials"),
)


def check_by_id(check_id: str) -> Check | None:
    """Проверка по идентификатору; неизвестный — ``None``."""
    for check in CHECKS:
        if check.id == check_id:
            return check
    return None


def explain_exception(error: BaseException) -> Check | None:
    """Проверка, которая называет причину этого исключения.

    Поверхность точки сбоя зовёт **одну** релевантную проверку вместо совета
    «сходите запустите диагностику». Неизвестный тип — ``None``: выдумывать
    причину хуже, чем показать саму ошибку.

    Args:
        error: Пойманное исключение.

    Returns:
        Подходящая :class:`Check` либо ``None``.
    """
    for exception_type, check_id in _EXCEPTION_CHECKS:
        if isinstance(error, exception_type):
            return check_by_id(check_id)
    return None


def run_check(check: Check, context: Context) -> Finding:
    """Выполнить одну проверку, не сверяясь с её зависимостями.

    Падение самой пробы — исход ``FAIL`` с текстом ошибки, а не трейсбек
    наружу: диагностика, роняющая процесс, бесполезна ровно там, где нужна.
    """
    try:
        outcome = check.probe(context)
    except Exception as error:
        outcome = Outcome(Status.FAIL, "diag_detail_probe_crashed", {"error": error})
    return Finding(check=check, outcome=outcome)


def run_checks(
    context: Context,
    *,
    only: Iterable[str] | None = None,
) -> list[Finding]:
    """Прогнать реестр и вернуть результаты — ничего не печатая.

    Проверка, чья предпосылка не выполнена, получает ``SKIP`` с указанием на
    **корневую** причину: вторая формулировка того же и есть то, из-за чего
    отчёт перестают читать.

    Блокировка распространяется по цепочке, а не на один шаг. Иначе выходило
    хуже, чем без неё: при отсутствующем ``secrets.json`` проверка учётных
    данных корректно молчала, а следующая за ней проверка порта запускалась и
    сообщала «порт не определён» — формально верно, по сути третья причина
    вместо одной настоящей.

    Args:
        context: Что проверкам известно об окружении.
        only: Идентификаторы нужных проверок; ``None`` — все.

    Returns:
        Находки в порядке реестра.
    """
    wanted: Sequence[Check] = (
        CHECKS if only is None else tuple(c for c in CHECKS if c.id in set(only))
    )
    findings: list[Finding] = []
    #: Проверка → корневая причина, по которой она (или её предпосылка) не удалась.
    unmet: dict[str, str] = {}
    for check in wanted:
        blocker = next((r for r in check.requires if r in unmet), None)
        if blocker is not None:
            root = unmet[blocker]
            unmet[check.id] = root
            findings.append(
                Finding(
                    check=check,
                    outcome=Outcome(Status.SKIP, "diag_detail_blocked_by", {"blocker": root}),
                )
            )
            continue
        finding = run_check(check, context)
        if finding.status is Status.FAIL:
            unmet[check.id] = check.id
        findings.append(finding)
    return findings
