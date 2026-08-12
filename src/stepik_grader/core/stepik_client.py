"""stepik_client.py — HTTP/OAuth клиент для Stepik API.

Архитектурный слой: Infrastructure.
Отвечает исключительно за:
  - аутентификацию (OAuth2 Authorization Code + Refresh Token),
  - создание авторизованной requests.Session,
  - GET-запросы к Stepik REST API (/api/steps, /api/lessons и др.),
  - скачивание сабмишнов.

Бизнес-логика (slugify, build_task_directory, save_task_files и т.д.)
находится в downloader.py.

Типичный вызов из downloader.py:
    from stepik_client import create_user_session, fetch_step_data, ...
    session = create_user_session(secrets, secrets_path)
    step    = fetch_step_data(session, lesson_id, step_position)
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json as _json_mod
import pathlib
import secrets as secrets_module
import socketserver
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry

from stepik_grader.core.diag_log import get_logger, register_secret
from stepik_grader.core.storage import save_secrets

__all__ = [
    "API_HOST",
    "CACHE_DIR",
    "CACHE_MAX_ENTRIES",
    "CACHE_TTL_SECONDS",
    "EXTERNAL_DOWNLOAD_ALLOWED_HOSTS",
    "HEADERS",
    "RETRY_STATUS_FORCELIST",
    "STEPIK_HOST",
    "ExternalUrlRejected",
    "OAuthCallbackPortBusy",
    "StepikNetworkError",
    "SubmissionResult",
    "authorize_via_browser",
    "clear_cache",
    "create_attempt",
    "create_user_session",
    "external_download_get",
    "fetch_comments_with_submissions",
    "fetch_course_data",
    "fetch_discussion_proxy",
    "fetch_discussion_threads",
    "fetch_lesson_data",
    "fetch_section_data",
    "fetch_section_units",
    "fetch_step_data",
    "fetch_step_languages",
    "fetch_submission_data",
    "fetch_submission_history",
    "fetch_unit_data",
    "is_stepik_url",
    "make_session",
    "poll_submission",
    "prune_cache",
    "read_step_id",
    "refresh_access_token",
    "submit_and_wait",
    "submit_solution",
    "token_is_valid",
    "validate_external_url",
    "wait_for_auth_code",
]

_log = get_logger("stepik_client")  # issue #148: диагностический лог (opt-in)

API_HOST = "https://stepik.org"

# issue #943: шаг опроса колбэк-сервера. ``handle_request`` блокирует поток до
# запроса или до своего таймаута, поэтому ждать им весь остаток дедлайна нельзя:
# цикл не смог бы выйти сразу после получения кода. Полсекунды — незаметно для
# пользователя и не создаёт заметного холостого хода.
_OAUTH_POLL_SECONDS = 0.5

# issue #109: статусы, повторяемые транспортным слоем make_session() —
# 429 (rate limit) и временные 5xx. 4xx помимо 429 не повторяются (не временные).
RETRY_STATUS_FORCELIST: tuple[int, ...] = (429, 500, 502, 503, 504)

# issue #815 (NETA-01): методы, для которых транспортный слой повторяет запрос.
# К идемпотентным дефолтам urllib3 добавлен POST — им идут `/api/attempts` и
# `/api/submissions` (отправка решения, issue #683), и повтор ограничен
# статусами `RETRY_STATUS_FORCELIST`, то есть «сервер занят / временно
# недоступен». Дубль попытки Stepik терпит: он и так создаёт новую при каждом
# сабмите, а вот потерянная отправка стоит пользователю решённой задачи.
_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE", "POST"})

# issue #815 (NETA-03): потолок ожидания по заголовку `Retry-After`. Дефолт
# urllib3 — 21600 с (6 часов).
_RETRY_AFTER_MAX_SECONDS = 60

# issue #815 (NETA-04): потолок тела внешней загрузки. Адрес приходит из HTML
# задачи, а `requests` без `stream=True` читает ответ в память целиком — ссылка
# на многогигабайтный файл выжирала RAM до OOM. 64 МБ несопоставимо больше
# любого архива тест-кейсов и несопоставимо меньше того, чем можно уронить
# машину.
_MAX_EXTERNAL_DOWNLOAD_BYTES = 64 * 1024 * 1024

# issue #1055: потолок обхода истории отправок. Страница API — 20 записей, то
# есть 50 страниц ≈ 1000 попыток по одному шагу: столько не набирает даже
# многократно переписанное решение, а цикл получает конец при залипшем
# `has_next`.
_SUBMISSIONS_MAX_PAGES = 50

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def make_session(
    access_token: str,
    *,
    retries: int = 3,
    backoff_factor: float = 1.0,
) -> requests.Session:
    """Возвращает requests.Session с Bearer-токеном и transport-level retry (issue #109).

    ``HTTPAdapter`` + ``urllib3.util.Retry`` монтируются на http/https:
    429 (rate limit) и временные 5xx (``RETRY_STATUS_FORCELIST``) повторяются
    автоматически с экспоненциальным backoff (``backoff_factor`` удваивается с
    каждой попыткой). Действует на уровне транспорта — применяется к любому
    запросу через эту сессию и является ЕДИНСТВЕННЫМ уровнем повтора: прикладной
    ``_get_with_retry()`` больше свою петлю не держит (issue #404), а лишь
    вызывает ``raise_for_status`` и логирует. Прочие 4xx (напр. 404) не повторяются.

    issue #815: повтор распространяется и на **POST** (``_RETRY_METHODS``) —
    отправка решения (``/api/attempts``, ``/api/submissions``) прежде не
    переживала единичный 429/503, хотя докстринг ``_post_json`` обещал обратное.
    ``Retry-After`` по-прежнему уважается, но не дольше
    ``_RETRY_AFTER_MAX_SECONDS``: дефолтные 6 часов urllib3 подвешивали поток
    без возможности отмены.

    Args:
        retries: максимальное число попыток на статусы из
            ``RETRY_STATUS_FORCELIST`` (``Retry.total``).
        backoff_factor: базовая задержка в секундах перед повтором; тесты
            передают маленькое значение, чтобы не ждать реально.
    """
    register_secret(access_token)  # issue #148: маскировать токен в диагностике
    _log.debug("создаю авторизованную сессию к %s (retries=%d)", API_HOST, retries)
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Authorization"] = f"Bearer {access_token}"

    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUS_FORCELIST,
        respect_retry_after_header=True,
        # issue #815 (NETA-01): POST в дефолтный allowed_methods urllib3 не
        # входит (там только идемпотентные методы), поэтому единичный 429/503 на
        # `/api/attempts` или `/api/submissions` ронял отправку решения с первой
        # попытки — при том что докстринг `_post_json` обещал транспортный
        # повтор. Повторяем ТОЛЬКО статусы из `RETRY_STATUS_FORCELIST`: это
        # «сервер занят/временно недоступен», где повтор безопасен. Обычная
        # ошибка POST (4xx кроме 429) как не повторялась, так и не повторяется.
        allowed_methods=_RETRY_METHODS,
        # issue #815 (NETA-03): потолок сна по `Retry-After`. Дефолт urllib3 —
        # 6 часов: один 429 с большим заголовком подвешивал поток-обработчик
        # web-запроса без возможности отмены, а в CLI — весь процесс. Честная
        # ошибка «Stepik просит подождать» полезнее зависшего грейдера.
        retry_after_max=_RETRY_AFTER_MAX_SECONDS,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# External downloads: unauthenticated session + host allowlist (issue #240)
#
# downloader.py извлекает ZIP/GitHub-ссылки из текста задачи Stepik. Эти
# ссылки могут указывать на произвольный сторонний хост, поэтому им нельзя
# передавать авторизованную ``requests.Session`` из ``make_session()`` —
# иначе OAuth Bearer-токен утечёт на сторонний домен вместе с запросом.
# ---------------------------------------------------------------------------

STEPIK_HOST = "stepik.org"

# Известные легитимные источники внешних тест-кейсов (issue #240, F-01).
# Расширять точечно, а не заменой на wildcard-подстроку хоста.
EXTERNAL_DOWNLOAD_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "api.github.com",
        "codeload.github.com",
    }
)


class ExternalUrlRejected(ValueError):
    """URL внешней загрузки не прошёл проверку allowlist/private-address."""


class OAuthCallbackPortBusy(RuntimeError):
    """Порт колбэка OAuth занят другим процессом (issue #997, ``JRN-3A-04``).

    Отдельный тип, а не общий ``OSError``: вызывающая сторона показывает разные
    подсказки. Занятый порт чинится закрытием чужого процесса или сменой
    ``redirect_uri``, а прежде он приходил тем же путём, что и отказ сервера, и
    пользователь читал совет «проверьте client_id / client_secret» — то есть
    правил ровно то, что было в порядке.
    """


class StepikNetworkError(RuntimeError):
    """Сеть до Stepik недоступна (issue #997, ``DEV-3-06``).

    Обрыв связи, DNS, таймаут — всё, что не является ответом сервера. Прежде
    эти случаи доходили до общего обработчика авторизации и объявлялись
    неверными учётными данными: пользователь шёл перевыпускать OAuth-приложение
    из-за отключившегося Wi-Fi.
    """


def _is_stepik_host(hostname: str) -> bool:
    """True если hostname — сам Stepik (stepik.org или его поддомен)."""
    return hostname == STEPIK_HOST or hostname.endswith(f".{STEPIK_HOST}")


def _is_disallowed_ip_literal(hostname: str) -> bool:
    """True если hostname — IP-литерал loopback/private/link-local/reserved сети."""
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast


def is_stepik_url(url: str) -> bool:
    """True если URL указывает на сам Stepik (авторизованная сессия уместна)."""
    return _is_stepik_host((urlparse(url).hostname or "").lower())


def validate_external_url(url: str) -> None:
    """Проверяет URL перед скачиванием без OAuth-токена.

    Разрешены только http(s)-ссылки на хосты из
    ``EXTERNAL_DOWNLOAD_ALLOWED_HOSTS`` (точное совпадение hostname — без
    поддоменных трюков вида ``raw.githubusercontent.com.evil.example``).
    Loopback/private/link-local/reserved IP-литералы отклоняются безусловно.
    Поднимает :class:`ExternalUrlRejected` при отказе.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ExternalUrlRejected(f"Недопустимая схема URL: {url!r}")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ExternalUrlRejected(f"Не удалось определить host: {url!r}")

    if hostname == "localhost" or _is_disallowed_ip_literal(hostname):
        raise ExternalUrlRejected(f"Локальный/приватный адрес запрещён: {url!r}")

    if hostname not in EXTERNAL_DOWNLOAD_ALLOWED_HOSTS:
        raise ExternalUrlRejected(f"Host не входит в allowlist внешних загрузок: {hostname!r}")


# issue #564: сколько редиректов внешней загрузки готовы пройти, ревалидируя
# каждый hop. Легитимная цепочка GitHub — github.com → codeload.github.com —
# один hop; 5 с запасом.
_MAX_EXTERNAL_REDIRECT_HOPS = 5


def _guard_response_size(response: requests.Response, url: str) -> None:
    """Отклонить слишком большой ответ внешней загрузки (issue #815, ``NETA-04``).

    ``requests`` без ``stream=True`` читает тело в память целиком, а адрес
    приходит из HTML задачи — то есть из недоверенного источника: ссылка на
    многогигабайтный файл выжирала RAM до OOM ещё до того, как кто-либо
    посмотрит на содержимое.

    Сначала смотрим ``Content-Length`` (дёшево и отсекает честный большой
    файл), затем фактический размер: заголовок необязателен и может врать.
    """
    # ВНИМАНИЕ: `ExternalUrlRejected` — подкласс `ValueError`, поэтому парсинг
    # заголовка отделён от проверки: `suppress(ValueError)` вокруг `raise`
    # проглотил бы собственное исключение (поймано тестом на этом же фиксе).
    declared_raw = response.headers.get("Content-Length")
    declared: int | None = None
    if declared_raw is not None:
        with contextlib.suppress(ValueError):
            declared = int(declared_raw)
    if declared is not None and declared > _MAX_EXTERNAL_DOWNLOAD_BYTES:
        raise ExternalUrlRejected(
            f"Ответ слишком велик ({declared} Б > {_MAX_EXTERNAL_DOWNLOAD_BYTES} Б): {url}"
        )
    actual = len(response.content)
    if actual > _MAX_EXTERNAL_DOWNLOAD_BYTES:
        raise ExternalUrlRejected(
            f"Ответ слишком велик ({actual} Б > {_MAX_EXTERNAL_DOWNLOAD_BYTES} Б): {url}"
        )


def external_download_get(
    url: str,
    *,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """GET на внешний URL через отдельную сессию без Authorization (issue #240).

    Используется для ZIP/GitHub-ссылок из текста задачи Stepik, которые не
    являются самим Stepik API — им не передаётся Bearer-токен текущей
    OAuth-сессии. URL сначала проверяется через :func:`validate_external_url`.

    Редиректы НЕ следуются автоматически (issue #564): каждый ``Location``
    заново прогоняется через :func:`validate_external_url`, иначе allowlist-хост
    мог бы 30x-редиректом увести запрос на loopback/приватный/metadata-адрес
    (SSRF-обход валидации, которая раньше проверяла только исходный URL).
    Относительный ``Location`` абсолютизируется от текущего URL. Больше
    ``_MAX_EXTERNAL_REDIRECT_HOPS`` hop'ов — отказ.
    """
    validate_external_url(url)
    session = requests.Session()
    session.headers.update(HEADERS)
    if headers:
        session.headers.update(headers)

    current = url
    for _hop in range(_MAX_EXTERNAL_REDIRECT_HOPS + 1):
        response = session.get(current, timeout=timeout, allow_redirects=False)
        if not response.is_redirect:
            _guard_response_size(response, current)
            return response
        current = urljoin(current, response.headers.get("Location", ""))
        validate_external_url(current)
    raise ExternalUrlRejected(
        f"Превышен лимит редиректов ({_MAX_EXTERNAL_REDIRECT_HOPS}) для {url!r}"
    )


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def token_is_valid(secrets: dict[str, Any]) -> bool:
    """True если access_token существует и не истечёт в ближайшие 60 секунд."""
    access_token = str(secrets.get("access_token", "")).strip()
    expires_at = float(secrets.get("expires_at", 0) or 0)
    return bool(access_token) and time.time() < expires_at - 60


def _validate_token_payload(token_data: dict[str, Any]) -> None:
    """Проверить, что ответ токен-эндпоинта пригоден к сохранению (issue #943).

    ``raise_for_status`` пропускает ЛЮБОЙ ``200``, поэтому валидный JSON вида
    ``{"expires_in": 3600}`` без ``access_token`` уходил в ``secrets.json`` как
    есть: прежний протухший токен оставался на месте, а ``expires_at``
    сдвигался в будущее — и ``token_is_valid()`` целый час отвечал ``True``,
    пока каждый запрос получал ``401`` без внятной причины.

    Проверяется ровно то, без чего сохранение бессмысленно: непустой строковый
    ``access_token`` и числовой ``expires_in``. ``expires_in`` отдельно потому,
    что дальше по пути стоит ``float(...)``, а ``float(None)`` — ``TypeError``
    из недр, а не понятная ошибка.

    Raises
    ------
    ValueError:
        Если ответ не несёт пригодных полей — вызывающая сторона обязана НЕ
        трогать secrets и вернуть управление на браузерную авторизацию.
    """
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError(
            "Ответ токен-эндпоинта без access_token — secrets не обновлены. "
            "Так отвечает подменённый или сломанный эндпоинт; пройдите авторизацию заново."
        )
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, int | float):
        raise ValueError(
            f"Ответ токен-эндпоинта с нечисловым expires_in ({expires_in!r}) — "
            "secrets не обновлены; пройдите авторизацию заново."
        )


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Обменивает refresh_token на новую пару access/refresh токенов."""
    register_secret(client_secret)
    register_secret(refresh_token)
    _log.info("обновляю access_token через %s/oauth2/token/ (grant=refresh_token)", API_HOST)
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    token_data = cast(dict[str, Any], response.json())
    _validate_token_payload(token_data)
    register_secret(str(token_data.get("access_token", "")))
    register_secret(str(token_data.get("refresh_token", "")))
    _log.info("access_token обновлён (expires_in=%s)", token_data.get("expires_in"))
    return token_data


# ---------------------------------------------------------------------------
# OAuth2 Authorization Code flow
# ---------------------------------------------------------------------------


class _OAuthHTTPServer(HTTPServer):
    """Колбэк-сервер без обратного DNS при старте (issue #943).

    ``HTTPServer.server_bind`` зовёт ``socket.getfqdn(host)`` ради поля
    ``server_name``. Это обратный DNS-запрос, и на машине, где резолвер не
    отвечает быстро (macOS без записи для loopback — воспроизведено на
    CI-раннере), он висит секундами: сокет уже забинден, но ``listen`` ещё не
    вызван, поэтому браузер, уже получивший редирект, стучится в закрытый порт.

    Само ``server_name`` нужно только заголовку ``Server`` и CGI, которых здесь
    нет, поэтому подставляем адрес как есть и стартуем мгновенно.
    """

    def server_bind(self) -> None:
        """Забиндить сокет, не спрашивая DNS об имени хоста."""
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def _make_oauth_handler(
    auth_data: dict[str, Any],
    path: str,
    expected_state: str,
) -> type[BaseHTTPRequestHandler]:
    """Фабрика OAuthHandler: захватывает auth_data, path и ожидаемый OAuth ``state``.

    ``expected_state`` защищает от Login-CSRF (issue #241): колбэк с
    ``state``, не совпадающим с тем, что был отправлен в authorize URL,
    отклоняется без извлечения ``code`` — иначе злоумышленник мог бы
    подсунуть жертве ссылку на локальный callback-сервер со своим кодом
    авторизации и привязать её сессию к своему Stepik-аккаунту.
    """

    class OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            req = urlparse(self.path)
            params = parse_qs(req.query)
            if req.path != path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            # issue #943: запрос на тот же path, но БЕЗ ``code``/``error`` — это
            # вообще не колбэк: так выглядит открытый вручную `localhost:8080/`
            # или префетч корня браузером. Прежде он проваливался в проверку
            # ``state`` ниже, писал `state_mismatch` и прекращал ожидание —
            # настоящий редирект Stepik приходил уже в закрытый сервер.
            # Решение принимается только там, где есть что решать.
            if not params.get("code") and not params.get("error"):
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<h1>Жду колбэк авторизации Stepik…</h1>"
                    "<p>Эта страница открыта напрямую, без параметров авторизации. "
                    "Вернитесь на вкладку Stepik и подтвердите доступ.</p>".encode()
                )
                return

            received_state = params.get("state", [None])[0]
            if received_state != expected_state:
                auth_data["error"] = "state_mismatch"
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>Invalid state. Possible CSRF - authorization rejected.</h1>")
                return

            auth_data["code"] = params.get("code", [None])[0]
            auth_data["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>OK. You can close this tab.</h1>")

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    return OAuthHandler


def wait_for_auth_code(
    host: str,
    port: int,
    path: str,
    expected_state: str,
    timeout: int = 120,
) -> str:
    """Запускает временный HTTP-сервер и ожидает OAuth-колбэк с кодом авторизации.

    Parameters
    ----------
    host, port, path:
        Параметры redirect_uri из secrets.json.
    expected_state:
        Значение ``state``, отправленное в authorize URL (issue #241);
        колбэк с несовпадающим/отсутствующим ``state`` отклоняется как
        потенциальный Login-CSRF.
    timeout:
        Максимальное время ожидания в секундах (по умолчанию 120).

    Raises
    ------
    RuntimeError:
        Если Stepik вернул error-параметр в колбэке, либо ``state`` колбэка
        не совпал с ``expected_state``.
    TimeoutError:
        Если код не получен в течение timeout секунд.
    """
    auth_data: dict[str, Any] = {}
    handler_class = _make_oauth_handler(auth_data, path, expected_state)
    try:
        server = _OAuthHTTPServer((host, port), handler_class)  # type: ignore[arg-type]
    except OSError as exc:
        # issue #997 (JRN-3A-04): «Address already in use» на 8080 — самая
        # частая причина, по которой авторизация не начинается вовсе, и она
        # никак не связана с учётными данными. Называем её прямо.
        raise OAuthCallbackPortBusy(
            f"Порт {port} занят другим процессом ({exc}). Закройте занявшую его "
            f"программу или укажите другой redirect_uri в secrets.json."
        ) from exc

    # issue #943 (DEV-3-04): обслуживаем запросы ДО ДЕДЛАЙНА, а не ровно один.
    # Раньше здесь стоял единственный ``handle_request`` — и любой посторонний
    # GET съедал его целиком: браузер сам префетчит ``/favicon.ico``, ветки «не
    # тот path» (404) и «state_mismatch» (400) тоже отвечают и возвращают
    # управление. Сервер закрывался, настоящий колбэк Stepik упирался в
    # ECONNREFUSED, а пользователь мгновенно получал «код не получен за 120с» —
    # сообщение про ожидание, которого не было (проверено прогоном: 0.0 секунды).
    #
    # Дедлайн считается по МОНОТОННЫМ часам, а не по одному вызову с
    # ``server.timeout``: перевод системного времени не должен ни обрывать
    # ожидание раньше срока, ни подвешивать его дольше.
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Короткий тик, а не весь остаток: ``handle_request`` блокирует поток
            # до запроса или до своего таймаута, и без тика отмена/выход из цикла
            # ждали бы полного дедлайна даже после получения кода.
            server.timeout = min(_OAUTH_POLL_SECONDS, remaining)
            server.handle_request()
            if auth_data.get("code") or auth_data.get("error"):
                break
    finally:
        server.server_close()

    code = auth_data.get("code")
    error = auth_data.get("error")
    if error:
        raise RuntimeError(f"OAuth error: {error}")
    if not code:
        raise TimeoutError(f"OAuth code not received within {timeout}s")
    return str(code)


def authorize_via_browser(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Открывает браузер, ожидает OAuth-код, обменивает на токены.

    Возвращает dict с ключами: access_token, refresh_token, expires_in, expires_at.

    Отправляет криптографически случайный ``state`` в authorize URL и требует
    его точного совпадения в колбэке (issue #241, F-02) — защита от
    Login-CSRF, когда злоумышленник подсовывает жертве ссылку на локальный
    callback-сервер со своим кодом авторизации.
    """
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    path = parsed.path or "/"

    state = secrets_module.token_urlsafe(32)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    auth_url = f"{API_HOST}/oauth2/authorize/?{urlencode(params)}"
    print(f"Открываю браузер: {auth_url}")
    with contextlib.suppress(OSError):
        webbrowser.open(auth_url)

    code = wait_for_auth_code(host, port, path, state)
    register_secret(client_secret)
    register_secret(code)
    _log.info("обмениваю authorization_code на токены через %s/oauth2/token/", API_HOST)
    response = requests.post(
        f"{API_HOST}/oauth2/token/",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"User-Agent": HEADERS["User-Agent"]},
        timeout=30,
    )
    response.raise_for_status()
    token_data: dict[str, Any] = response.json()
    # issue #943: второй сайт того же обмена — код-грант. Без проверки сюда
    # проходил бы ровно тот же ``200`` без ``access_token``, а ниже стоит
    # ``float(...)``, который на ``None`` даёт ``TypeError`` из недр.
    _validate_token_payload(token_data)
    register_secret(str(token_data.get("access_token", "")))
    register_secret(str(token_data.get("refresh_token", "")))
    token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
    _log.info("получены токены (expires_in=%s)", token_data.get("expires_in"))
    return token_data


def create_user_session(
    secrets: dict[str, Any],
    secrets_path: pathlib.Path,
) -> requests.Session:
    """Возвращает аутентифицированную requests.Session.

    Логика приоритетов:
    1. Валидный access_token → вернуть сессию сразу.
    2. Есть refresh_token → попробовать обновить.
    3. Иначе → полный OAuth flow через браузер.

    secrets обновляется на месте; новые токены сохраняются в secrets_path.
    """
    client_id = str(secrets["client_id"])
    client_secret = str(secrets["client_secret"])
    redirect_uri = str(secrets["redirect_uri"])

    if token_is_valid(secrets):
        return make_session(str(secrets["access_token"]))

    refresh_token = str(secrets.get("refresh_token", "")).strip()
    if refresh_token:
        try:
            token_data = refresh_access_token(client_id, client_secret, refresh_token)
            token_data["expires_at"] = time.time() + float(token_data.get("expires_in", 3600))
            secrets.update(token_data)
            save_secrets(secrets_path, secrets)
            return make_session(str(secrets["access_token"]))
        except requests.HTTPError:
            print("Refresh token истёк, выполняется повторная авторизация...")
        except (requests.ConnectionError, requests.Timeout) as exc:
            # issue #997 (DEV-3-06): обрыв связи, DNS или таймаут — это НЕ
            # «истёкший токен» и не повод открывать браузер: там пользователя
            # ждёт та же недоступная сеть. Прежде эти исключения доходили до
            # общего обработчика авторизации, который советует проверить
            # client_id/client_secret, — и человек шёл перевыпускать
            # OAuth-приложение из-за отключившегося Wi-Fi.
            raise StepikNetworkError(
                f"Нет связи со Stepik ({exc}). Проверьте интернет-соединение и "
                f"повторите — учётные данные здесь ни при чём."
            ) from exc
        except ValueError as exc:
            # issue #943: ответ 200 без пригодных полей. secrets НЕ трогаем —
            # прежний токен остаётся как есть, а не подменяется протухшим с
            # обновлённым expires_at, — и уходим на браузерную авторизацию.
            print(f"Ответ токен-эндпоинта непригоден ({exc}); нужна повторная авторизация.")

    token_data = authorize_via_browser(client_id, client_secret, redirect_uri)
    secrets.update(token_data)
    save_secrets(secrets_path, secrets)
    return make_session(str(secrets["access_token"]))


# ---------------------------------------------------------------------------
# HTTP helpers: retry + file cache
# ---------------------------------------------------------------------------


def _get_with_retry(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> requests.Response:
    """GET к Stepik API с ``raise_for_status`` и debug-логированием (issue #148).

    Повтор transient-сбоев (сетевые ошибки, 429, временные 5xx с backoff и
    ``Retry-After``) выполняет ТРАНСПОРТНЫЙ слой — ``urllib3.util.Retry`` на
    ``HTTPAdapter`` сессии (см. ``make_session``). Прежняя прикладная retry-петля
    здесь дублировала его (те же сетевые исключения, свой backoff) и снята
    (issue #404): единый источник истины повторов — транспорт. Функция оставляет
    за собой лишь то, чего транспорт не делает: ``raise_for_status`` для
    не-retryable 4xx (404/403 в forcelist не входят) и запись запроса в
    диагностический лог.
    """
    # URL/параметры санитизируются редакцией логгера (issue #148)
    _log.debug("GET %s params=%s", url, params)
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    _log.debug("GET %s → %d", url, response.status_code)
    return response


CACHE_DIR = pathlib.Path(".stepik_cache")
CACHE_TTL_SECONDS = 3600  # 1 hour
# issue #816 (DEV-11): потолок числа файлов кэша. TTL один рост не сдерживает:
# просроченная запись лишь игнорируется при чтении и перезаписывается, если
# повторится ТОТ ЖЕ ключ, — а каждая новая задача даёт новый URL и новый файл.
# Для сравнения, ``core/cache.py`` ограничивает ``.grader_cache`` 512 записями
# с ``prune()`` начиная с issue #553; здесь такого не было вовсе.
CACHE_MAX_ENTRIES = 512


def prune_cache(cache_dir: pathlib.Path | None = None) -> int:
    """Удалить просроченные и лишние файлы кэша; вернуть их число (issue #816).

    Сначала выбывают записи старше ``CACHE_TTL_SECONDS`` (их всё равно нельзя
    использовать), затем — самые давние сверх ``CACHE_MAX_ENTRIES``. Кэш
    регенерируем: удалённая запись стоит одного лишнего GET к Stepik.
    """
    directory = CACHE_DIR if cache_dir is None else cache_dir
    if not directory.is_dir():
        return 0
    now = time.time()
    alive: list[tuple[float, pathlib.Path]] = []
    removed = 0
    for entry in directory.glob("*.json"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if now - mtime >= CACHE_TTL_SECONDS:
            with contextlib.suppress(OSError):
                entry.unlink()
                removed += 1
            continue
        alive.append((mtime, entry))
    surplus = len(alive) - CACHE_MAX_ENTRIES
    if surplus > 0:
        for _mtime, entry in sorted(alive)[:surplus]:  # самые давние
            with contextlib.suppress(OSError):
                entry.unlink()
                removed += 1
    return removed


def clear_cache(cache_dir: pathlib.Path | None = None) -> int:
    """Удалить весь файловый кэш Stepik API; вернуть число удалённых файлов.

    issue #816: ``--clear-cache`` чистил только ``.grader_cache`` (результаты
    прогонов), а ``.stepik_cache`` (ответы API) оставался — при том что именно
    он рос от каждой новой скачанной задачи.
    """
    directory = CACHE_DIR if cache_dir is None else cache_dir
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.glob("*.json"):
        with contextlib.suppress(OSError):
            entry.unlink()
            removed += 1
    return removed


def _cached_api_get(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET-запрос с файловым кэшем (TTL=1ч) и retry при сетевых ошибках.

    Кэшируется полный JSON-ответ по ключу SHA-256(url + params).
    Используется только для read-only API-эндпоинтов Stepik.
    """
    key_data = _json_mod.dumps({"url": url, "params": params or {}}, sort_keys=True)
    key = hashlib.sha256(key_data.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{key}.json"

    CACHE_DIR.mkdir(exist_ok=True)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                return cast(
                    dict[str, Any],
                    _json_mod.loads(cache_file.read_text(encoding="utf-8")),
                )
            except (_json_mod.JSONDecodeError, OSError):
                pass

    response = _get_with_retry(session, url, params=params)
    data: dict[str, Any] = response.json()
    with contextlib.suppress(OSError):
        cache_file.write_text(_json_mod.dumps(data, ensure_ascii=False), encoding="utf-8")
    # issue #816: ленивая уборка на записи — отдельного фонового потока в
    # проекте нет ни у одного кэша (тот же best-effort приём, что у реестра
    # job'ов и очереди глоссария).
    prune_cache()
    return data


# ---------------------------------------------------------------------------
# Stepik REST API — fetch helpers
# ---------------------------------------------------------------------------


def fetch_step_data(
    session: requests.Session,
    lesson_id: int,
    step_position: int,
) -> dict[str, Any]:
    """Возвращает объект шага по позиции внутри урока (с пагинацией)."""
    page = 1
    while True:
        response = _get_with_retry(
            session,
            f"{API_HOST}/api/steps",
            params={"lesson": lesson_id, "page": page},
        )
        data = response.json()
        steps: list[dict[str, Any]] = data.get("steps", [])
        for step in steps:
            if step.get("position") == step_position:
                return step
        meta = data.get("meta", {})
        if not meta.get("has_next"):
            break
        page += 1
    raise ValueError(f"Шаг с позицией {step_position} не найден в уроке {lesson_id}")


def fetch_lesson_data(session: requests.Session, lesson_id: int) -> dict[str, Any]:
    """Возвращает объект урока по lesson_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/lessons/{lesson_id}")
    lessons: list[dict[str, Any]] = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"Урок {lesson_id} не найден")
    return lessons[0]


def fetch_unit_data(
    session: requests.Session,
    lesson_id: int,
    unit_id: int | None,
) -> dict[str, Any]:
    """Возвращает объект юнита для урока."""
    params: dict[str, int] = {"lesson": lesson_id}
    if unit_id is not None:
        params["id"] = unit_id
    data = _cached_api_get(session, f"{API_HOST}/api/units", params=params)
    units: list[dict[str, Any]] = data.get("units", [])
    if not units:
        raise ValueError(f"Юнит для урока {lesson_id} не найден")
    return units[0]


def fetch_section_data(session: requests.Session, section_id: int) -> dict[str, Any]:
    """Возвращает объект секции по section_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/sections/{section_id}")
    sections: list[dict[str, Any]] = data.get("sections", [])
    if not sections:
        raise ValueError(f"Секция {section_id} не найдена")
    return sections[0]


def fetch_section_units(session: requests.Session, section_id: int) -> list[dict[str, Any]]:
    """Возвращает юниты секции — связку «секция → уроки».

    ``fetch_unit_data`` умеет искать юнит по уже известному ``lesson_id``, но
    обход курса идёт в обратную сторону: курс → секции → юниты → уроки, и
    ``lesson_id`` как раз добывается из юнита (поле ``lesson``). Пустой список —
    не ошибка: секция без юнитов бывает у черновиков и скрытых модулей.
    """
    data = _cached_api_get(session, f"{API_HOST}/api/units", params={"section": section_id})
    units: list[dict[str, Any]] = data.get("units", [])
    return units


def fetch_course_data(session: requests.Session, course_id: int) -> dict[str, Any]:
    """Возвращает объект курса по course_id."""
    data = _cached_api_get(session, f"{API_HOST}/api/courses/{course_id}")
    courses: list[dict[str, Any]] = data.get("courses", [])
    if not courses:
        raise ValueError(f"Курс {course_id} не найден")
    return courses[0]


def fetch_submission_data(
    session: requests.Session,
    step_id: int,
) -> dict[str, Any] | None:
    """Возвращает последний сабмишн для шага или None если их нет.

    Один запрос, только первая страница: вызывающей стороне нужен лишь
    свежайший ответ. Вся история — ``fetch_submission_history``.
    """
    response = _get_with_retry(
        session,
        f"{API_HOST}/api/submissions",
        params={"step": step_id, "order": "desc"},
    )
    submissions: list[dict[str, Any]] = response.json().get("submissions", [])
    return submissions[0] if submissions else None


def fetch_submission_history(
    session: requests.Session,
    step_id: int,
    *,
    max_pages: int = _SUBMISSIONS_MAX_PAGES,
) -> list[dict[str, Any]]:
    """Возвращает ВСЕ отправки пользователя по шагу, новые первыми.

    Эндпоинт отдаёт историю постранично, а не только последний ответ
    (issue #1055): у каждой записи есть код (``reply.code``), вердикт
    платформы (``status``) и время — это и есть источник корпуса реальных
    решений вместе с эталонными вердиктами.

    ``max_pages`` ограничивает обход: страницы кончаются по ``meta.has_next``,
    но выдумывать доверие к чужому флагу незачем — при его залипании обход
    остановится на пределе, а не будет ходить по кругу.
    """
    collected: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        response = _get_with_retry(
            session,
            f"{API_HOST}/api/submissions",
            params={"step": step_id, "order": "desc", "page": page},
        )
        data = response.json()
        submissions: list[dict[str, Any]] = data.get("submissions", [])
        collected.extend(s for s in submissions if isinstance(s, dict))
        meta = data.get("meta") or {}
        if not meta.get("has_next"):
            break
        page += 1
    else:
        _log.warning(
            "история отправок шага %s оборвана на %d-й странице (has_next всё ещё true)",
            step_id,
            max_pages,
        )
    return collected


# ---------------------------------------------------------------------------
# Отправка решения на Stepik (issue #683): attempt → submission → poll вердикта.
# Пишущие POST-эндпоинты (в отличие от read-only GET выше). Авторизация — Bearer
# OAuth-токеном сессии (authorization_code flow, тот же токен, что скачивает
# задачи); CSRF для API-POST под Bearer не требуется. Сам сабмит — необратимое
# действие на платформе, поэтому инициируется явным действием пользователя.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmissionResult:
    """Итог отправки решения на Stepik (issue #683).

    ``status``: ``"correct"`` — зачтено, ``"wrong"`` — не зачтено,
    ``"evaluation"`` — всё ещё проверяется (poll не дождался вердикта),
    ``"error"`` — сбой отправки. ``hint`` — сообщение проверяющей системы
    Stepik, ``score`` — начисленный балл (если есть), ``submission_id`` — id
    сабмишна (для ссылки на решение).
    """

    status: str
    hint: str = ""
    score: str = ""
    submission_id: int | None = None


def _post_json(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    timeout: int = 30,
) -> requests.Response:
    """POST JSON к Stepik API с ``raise_for_status`` и debug-логом (issue #683).

    Зеркалит ``_get_with_retry``: повтор transient-сбоев — транспортный слой
    (``urllib3.util.Retry`` на ``HTTPAdapter`` сессии), здесь только
    ``raise_for_status`` для не-retryable 4xx и запись в диагностический лог.
    """
    _log.debug("POST %s", url)
    response = session.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    _log.debug("POST %s → %d", url, response.status_code)
    return response


def create_attempt(session: requests.Session, step_id: int) -> int:
    """Создать attempt для code-challenge шага (POST /api/attempts) → ``attempt_id``.

    Raises:
        ValueError: если Stepik не вернул attempt (напр. шаг не code-challenge).
    """
    resp = _post_json(session, f"{API_HOST}/api/attempts", {"attempt": {"step": step_id}})
    attempts: list[dict[str, Any]] = resp.json().get("attempts", [])
    if not attempts:
        raise ValueError(f"Stepik не вернул attempt для шага {step_id}")
    return int(attempts[0]["id"])


def submit_solution(
    session: requests.Session,
    attempt_id: int,
    code: str,
    language: str = "python3",
) -> int:
    """Отправить код в attempt (POST /api/submissions) → ``submission_id``.

    Raises:
        ValueError: если Stepik не вернул submission.
    """
    payload = {"submission": {"attempt": attempt_id, "reply": {"language": language, "code": code}}}
    resp = _post_json(session, f"{API_HOST}/api/submissions", payload)
    submissions: list[dict[str, Any]] = resp.json().get("submissions", [])
    if not submissions:
        raise ValueError("Stepik не вернул submission после отправки")
    return int(submissions[0]["id"])


def poll_submission(
    session: requests.Session,
    submission_id: int,
    *,
    timeout: float = 60.0,
    interval: float = 1.5,
    cancel_event: threading.Event | None = None,
) -> SubmissionResult:
    """Опрашивать статус сабмишна, пока он не выйдет из ``evaluation`` (или timeout).

    GET /api/submissions/{id} → ``status`` (correct/wrong/evaluation). По таймауту
    возвращает ``status="evaluation"`` — проверка не успела завершиться, клиент
    покажет «отправлено, оценивается».

    ``cancel_event`` (issue #797) прекращает ОПРОС, а не саму отправку: попытка
    на Stepik уже создана и живёт своей жизнью. Возвращается тот же
    ``status="evaluation"``, что и по таймауту, — вызывающая сторона отличает
    отмену по собственному событию, а не по результату.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            break
        resp = _get_with_retry(session, f"{API_HOST}/api/submissions/{submission_id}")
        subs: list[dict[str, Any]] = resp.json().get("submissions", [])
        if not subs:
            break
        last = subs[0]
        status = str(last.get("status", ""))
        if status and status != "evaluation":
            return SubmissionResult(
                status=status,
                hint=str(last.get("hint", "")),
                score=str(last.get("score", "")),
                submission_id=submission_id,
            )
        time.sleep(interval)
    return SubmissionResult(
        status=str(last.get("status") or "evaluation"),
        hint=str(last.get("hint", "")),
        submission_id=submission_id,
    )


def fetch_step_languages(session: requests.Session, step_id: int) -> list[str]:
    """Языки, разрешённые code-challenge шагом (issue #683).

    Ключи ``block.options.code_templates`` шага (напр. ``['python3.10',
    'python3.12']``). Пусто — шаг не code-challenge или без шаблонов. Знать
    точный идентификатор критично: Stepik отвергает неверный
    (``Unknown language: python3``), а версию (``python3.10``) знает только сам
    шаг — эмпирически подтверждено живым сабмитом.
    """
    resp = _get_with_retry(session, f"{API_HOST}/api/steps/{step_id}")
    steps: list[dict[str, Any]] = resp.json().get("steps", [])
    if not steps:
        return []
    options = steps[0].get("block", {}).get("options", {})
    templates = options.get("code_templates", {})
    return list(templates) if isinstance(templates, dict) else []


def _pick_python_language(languages: list[str]) -> str:
    """Выбрать python-язык из разрешённых шагом (issue #683).

    Предпочитает самую свежую версию (``python3.12`` перед ``python3.10`` —
    код решения совместим), fallback — ``python3`` (пусть Stepik ответит, если
    шаг вообще не про Python).
    """
    pythons = sorted((raw for raw in languages if "python" in raw.lower()), reverse=True)
    return pythons[0] if pythons else "python3"


def submit_and_wait(
    session: requests.Session,
    step_id: int,
    code: str,
    *,
    language: str | None = None,
    timeout: float = 60.0,
    cancel_event: threading.Event | None = None,
) -> SubmissionResult:
    """Полный поток отправки (issue #683): attempt → submission → poll до вердикта.

    ``language=None`` — определить автоматически из разрешённых языков шага
    (``fetch_step_languages`` → ``_pick_python_language``); иначе использовать
    заданный. Автоопределение обязательно: хардкод ``python3`` Stepik отвергает.

    ``cancel_event`` (issue #797) действует только на ожидание вердикта: к
    моменту опроса попытка уже отправлена и на платформе останется. Отменить
    саму отправку нельзя — и UI не должен утверждать обратное.
    """
    if language is None:
        language = _pick_python_language(fetch_step_languages(session, step_id))
    attempt_id = create_attempt(session, step_id)
    submission_id = submit_solution(session, attempt_id, code, language)
    return poll_submission(session, submission_id, timeout=timeout, cancel_event=cancel_event)


def read_step_id(task_dir: pathlib.Path) -> int | None:
    """Прочитать ``step_id`` из ``meta.json`` папки задачи (issue #683).

    ``meta.json`` пишется downloader'ом при скачивании шага. ``None`` — если
    файла нет, JSON битый или поля ``step_id`` нет/не int (задача скачана не
    downloader'ом или папка не является задачей Stepik).
    """
    try:
        raw = (task_dir / "meta.json").read_text(encoding="utf-8")
        data = _json_mod.loads(raw)
    except (OSError, ValueError):
        return None
    step_id = data.get("step_id") if isinstance(data, dict) else None
    return int(step_id) if isinstance(step_id, int) else None


# ---------------------------------------------------------------------------
# Discussions / solutions (issue #55): ветка обсуждений шага и закреплённые
# решения. Read-only публичные данные → кэшируются как fetch_lesson/course.
# ---------------------------------------------------------------------------


def fetch_discussion_threads(
    session: requests.Session,
    thread_ids: list[str],
) -> list[dict[str, Any]]:
    """Возвращает объекты discussion-thread'ов шага по их id.

    У шага ``discussion_threads`` — список id вида ``"77-2506803-1"``. Каждый
    объект несёт поле ``thread`` (``"default"`` — обычные обсуждения,
    ``"solutions"`` — ветка решений, открывающаяся после сдачи).
    """
    if not thread_ids:
        return []
    data = _cached_api_get(
        session,
        f"{API_HOST}/api/discussion-threads",
        params={"ids[]": thread_ids},
    )
    threads: list[dict[str, Any]] = data.get("discussion-threads", [])
    return threads


def fetch_discussion_proxy(
    session: requests.Session,
    proxy_id: str,
) -> dict[str, Any]:
    """Возвращает discussion-proxy по id (списки id комментариев ветки).

    Содержит ``discussions`` (все id), ``discussions_most_liked`` (топ по
    лайкам, отсортировано убыв.) и др. ``proxy_id`` берётся из объекта thread
    (``discussion_proxy``).
    """
    data = _cached_api_get(session, f"{API_HOST}/api/discussion-proxies/{proxy_id}")
    proxies: list[dict[str, Any]] = data.get("discussion-proxies", [])
    if not proxies:
        raise ValueError(f"discussion-proxy {proxy_id} не найден")
    return proxies[0]


def fetch_comments_with_submissions(
    session: requests.Session,
    comment_ids: list[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Возвращает ``(comments, submissions)`` для списка id комментариев.

    Ключ — параметр ``expand=submission`` (issue #55): без него сабмишн
    закреплённого решения приватен (``/api/submissions/{id}`` → 403,
    ``?ids[]=`` → пусто), а с ним код приходит в top-level массиве
    ``submissions`` ответа ``/api/comments``. Каждый comment ссылается на свой
    сабмишн полем ``submission`` (id), связываемым с ``submissions[].id``.
    """
    if not comment_ids:
        return [], []
    data = _cached_api_get(
        session,
        f"{API_HOST}/api/comments",
        params={"ids[]": comment_ids, "expand": "submission"},
    )
    return data.get("comments", []), data.get("submissions", [])
