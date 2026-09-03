#!/usr/bin/env python3
"""scripts/gh_rest.py — конвейер PR по REST, а не по GraphQL (issue #1242).

Проблема не в объёме работы, а в **транспорте**. Лимит GraphQL — 5000 *points*
в час, и одна операция агента стоит около 300; лимит REST — 5000 **запросов**,
где та же операция стоит 1. Раньше рутина шла через ``gh api`` (REST), и
пятьдесят PR за день укладывались в пятую часть квоты. Через GraphQL пять PR
выжигают её трижды — замеры 19.08.2026 дали ``used=10 724`` при лимите 5000.

Причём ``used`` считает **попытки**, а не успехи: всё сверх лимита — отказы
``403``. Агент в таком состоянии не «работает с руганью», он не работает вовсе
и рискует бросить операцию на середине.

Отсюда правила, закодированные здесь:

1. **Один транспорт на всех.** Модуль — единственная точка REST-доступа для
   скриптов конвейера; ``check_pr_ready.py`` ходит через него же.
2. **``gh`` CLI не обязателен.** Облачная агентская сессия его не имеет вовсе,
   поэтому запрос уходит на ``urllib`` с токеном из окружения. ``gh`` остаётся
   удобством локального окна — у него спрашивается только токен, и то один раз
   за процесс.
3. **Исчерпанная квота — это «ждать», а не «упало».** ``403`` с
   ``x-ratelimit-remaining: 0`` распознаётся отдельно от прочих ошибок:
   печатается время сброса, код возврата — :data:`EXIT_WAIT`. Повторять запрос
   бессмысленно: счётчик растёт и после нуля. Но именно ``0``, а не
   «заголовка нет»: ``403`` без заголовков лимита — отказ по другой причине, и
   она берётся из тела ответа (issue #1273).
4. **Условный запрос не расходует лимит вовсе.** Ответ ``304 Not Modified`` на
   ``If-None-Match`` GitHub не засчитывает, поэтому GET'ы носят ``ETag`` в
   кэше между запусками процесса.
5. **Стоп-кран по остатку.** Остаток читается из заголовков каждого ответа
   даром; упав ниже порога (:data:`DEFAULT_QUOTA_FLOOR`), модуль предупреждает,
   а перед GraphQL-мутацией — отказывается работать с кодом
   :data:`EXIT_WAIT`. Смысл в том, чтобы конвейер вставал **до** нуля, а не на
   нуле: операция, начатая с остатком в пару запросов, бросается на середине
   (issue #1280).

Чего здесь **нет** и не будет: покрытия всего GitHub API (только операции
конвейера), обхода лимитов вторым токеном и **автоматического ослабления
TLS-проверки** (issue #1259 — доверенный набор задаёт окружение, а не модуль;
исключение включается только явным ``GH_REST_RELAXED_CA=1`` и не молча).

**GraphQL здесь тоже есть — но ровно в одну строку.** ``auto-merge``
(``enablePullRequestAutoMerge``) REST-эквивалента не имеет, поэтому уходит на
``POST /graphql`` напрямую: одна короткая мутация стоит единицы points, тогда
как та же операция через MCP-инструмент — около трёхсот. Sub-issues, наоборот,
из списка исключений выбыли: у них REST-эндпоинты появились, и подкоманды
``sub-issues``/``add-sub-issue`` ходят обычным транспортом.

Запуск::

    python scripts/gh_rest.py pulls                  # открытые PR
    python scripts/gh_rest.py checks 1242            # проверки PR и состояние main
    python scripts/gh_rest.py compare 1242           # отставание ветки от базовой
    python scripts/gh_rest.py create-pr --title T --head branch --body B
    python scripts/gh_rest.py update-branch 1242     # подтянуть main в ветку PR
    python scripts/gh_rest.py auto-merge 1242        # смержить самому, когда позеленеет
    python scripts/gh_rest.py merge 1242             # смержить (squash), см. ниже
    python scripts/gh_rest.py sub-issues 915         # дочерние issue эпика
    python scripts/gh_rest.py queue                  # очередь мержа: кого обновлять
    python scripts/gh_rest.py rate                   # остаток квоты; сам её не тратит

Код возврата 0 — успех; 1 — ошибка; 2 — квота исчерпана, надо ждать сброса.

**Мерж из облачной агентской сессии невозможен** и починке не подлежит: сервер
отвечает ``403`` с «Merging into a protected base branch is not permitted for
this session type». Это свойство типа сессии, а не прав токена и не настроек
репозитория, поэтому подкоманда ``merge`` полезна в локальном окне, а из облака
PR доводится до зелёного и мержится человеком.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

__all__ = [
    "API",
    "DEFAULT_QUOTA_FLOOR",
    "DEFAULT_REPO",
    "ENV_QUOTA_FLOOR",
    "ENV_RELAXED_CA",
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_WAIT",
    "FLAKE_LOG",
    "MAX_ATTEMPTS",
    "Divergence",
    "GitHubError",
    "MissingToken",
    "PullSummary",
    "QueueEntry",
    "QueueReport",
    "Quota",
    "RateLimited",
    "Response",
    "TlsVerificationError",
    "add_labels",
    "add_sub_issue",
    "append_flake_note",
    "branch_runs",
    "cancel_run",
    "close_issue",
    "comment_issue",
    "compare",
    "create_issue",
    "create_pull",
    "disable_auto_merge",
    "enable_auto_merge",
    "ensure_label",
    "ensure_quota",
    "graphql",
    "issue",
    "issue_comments",
    "issues_with_label",
    "latest_checks_by_name",
    "list_pulls",
    "main",
    "main_run",
    "merge_pull",
    "merge_queue",
    "merged_pulls",
    "pull",
    "pull_checks",
    "pull_files",
    "queue_order",
    "rate_limit",
    "relaxed_ca_enabled",
    "remove_label",
    "request",
    "rerun_failed_jobs",
    "resolve_token",
    "run_jobs",
    "sub_issues",
    "update_branch",
    "update_comment",
    "update_issue",
]

DEFAULT_REPO = "ArtVsMark/Stepik-Python-Grader"
API = "https://api.github.com"

EXIT_OK = 0
EXIT_FAIL = 1
# Отдельный код именно для квоты: вызывающая сторона (цикл агента, CI-шаг)
# должна отличать «подожди и повтори» от «сломалось и повторять незачем».
EXIT_WAIT = 2

# issue #1231: очередь, из-за которой прогоны main отменялись, принадлежит
# именно этому workflow — его и спрашиваем про занятость ветки.
_CI_WORKFLOW = "ci.yml"

# Пропущенный джоб — это условие в workflow, а не отказ.
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

_API_VERSION = "2022-11-28"
_TIMEOUT = 30

# issue #1259: ослабление проверки сертификата — только по явному требованию
# окружения. Значение ровно «1»: у переключателя безопасности не должно быть
# догадок о намерении, а опечатка обязана означать «выключено».
ENV_RELAXED_CA = "GH_REST_RELAXED_CA"

# issue #1280: стоп-кран. Останавливаться надо ДО нуля — на нуле операция уже
# брошена на середине, а счётчик попыток продолжает расти (замер 20.08.2026:
# used=10 435 при лимите 5000, то есть окна обращались и после нуля). Шестьсот
# — это цена двух GraphQL-операций через MCP-инструмент (~300 points каждая):
# ниже порога не хватит даже на две, а значит начинать нечего.
DEFAULT_QUOTA_FLOOR = 600
ENV_QUOTA_FLOOR = "GH_REST_QUOTA_FLOOR"

# Предупреждение о низком остатке печатается один раз на процесс: смысл в
# сигнале, а не в шуме на каждый запрос пакетной операции.
_WARNED_RESOURCES: set[str] = set()

Opener = Callable[[urllib.request.Request], Any]


class GitHubError(RuntimeError):
    """GitHub ответил не так, как ожидалось (кроме исчерпанной квоты)."""


class TlsVerificationError(GitHubError):
    """TLS-проверка не прошла — до GitHub запрос не дошёл вовсе.

    Отдельный класс, потому что и причина, и лечение здесь другие: это не
    «GitHub ответил не так», а «соединение не установлено». Сообщение такой
    ошибки называет причину и способ починки, поэтому CLI печатает его как
    есть, без префикса про GitHub.
    """


class MissingToken(GitHubError):
    """Токена нет ни в окружении, ни у ``gh`` — запрос отправить нечем."""


class RateLimited(GitHubError):
    """Квота исчерпана: повторять бессмысленно, надо ждать сброса.

    Отдельный класс, а не текст ошибки: это единственное состояние, в котором
    правильный ответ — «подожди», а не «почини». Счётчик ``used`` растёт и
    после нуля, поэтому повтор только отдаляет сброс.
    """

    def __init__(self, message: str, *, reset_at: int = 0, resource: str = "core") -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.resource = resource

    def wait_seconds(self, *, now: float | None = None) -> int:
        """Сколько секунд осталось до сброса квоты (0 — уже можно)."""
        moment = time.time() if now is None else now
        return max(0, int(self.reset_at - moment))

    def describe(self, *, now: float | None = None) -> str:
        """Человеческая строка: что исчерпано, когда отпустит."""
        seconds = self.wait_seconds(now=now)
        when = time.strftime("%H:%M:%S", time.localtime(self.reset_at)) if self.reset_at else "?"
        return (
            f"квота GitHub ({self.resource}) исчерпана — сброс в {when} "
            f"(через {seconds // 60} мин {seconds % 60} с). Ждать, а не повторять: "
            "счётчик попыток растёт и после нуля"
        )


@dataclasses.dataclass(frozen=True)
class Quota:
    """Остаток лимита по одному ресурсу (``core``, ``graphql``, ``search``)."""

    resource: str
    limit: int
    remaining: int
    used: int
    reset: int

    def describe(self) -> str:
        """Строка вида ``core: 4993/5000, сброс в 15:42``."""
        when = time.strftime("%H:%M:%S", time.localtime(self.reset)) if self.reset else "?"
        return f"{self.resource}: {self.remaining}/{self.limit} осталось, сброс в {when}"


@dataclasses.dataclass(frozen=True)
class Response:
    """Ответ REST: данные плюс то, что нужно для условных запросов и учёта."""

    status: int
    data: Any
    etag: str | None = None
    from_cache: bool = False


@dataclasses.dataclass(frozen=True)
class PullSummary:
    """Открытый PR глазами конвейера — без полей, за которые платят GraphQL."""

    number: int
    title: str
    branch: str
    base: str
    author: str
    draft: bool
    updated_at: str
    # Голова ветки приходит в том же ответе списка. Держим её здесь, чтобы
    # очередь спрашивала проверки сразу, не тратя по запросу на каждый PR.
    sha: str = ""
    # issue #1287: PR из форка живёт в чужом репозитории, и `update-branch` для
    # него нам недоступен. Из очереди он не выпадает — мержится как все, — но
    # обновлять его ветку должен владелец форка или мейнтейнер кнопкой.
    fork: bool = False
    # issue #1326: метки и тело приходят в том же ответе списка, лишних
    # запросов не стоят, а без них не вычислить приоритет: метка `blocker`
    # живёт на PR, а остальная шкала наследуется от issue через `Closes #N`.
    labels: tuple[str, ...] = ()
    body: str = ""

    def describe(self) -> str:
        """Одна строка списка: номер, состояние, ветка, заголовок."""
        mark = "черновик" if self.draft else "готов"
        return f"#{self.number} [{mark}] {self.branch} → {self.base} · {self.author} · {self.title}"


@dataclasses.dataclass(frozen=True)
class Divergence:
    """Расхождение ветки с базовой: сколько коммитов позади и впереди."""

    ahead: int
    behind: int

    @property
    def stale(self) -> bool:
        """Отстала ли ветка — её зелёный отвечает про состояние, которого нет."""
        return self.behind > 0


def resolve_token(*, env: dict[str, str] | None = None) -> str:
    """Токен для REST: окружение, иначе ``gh auth token``.

    Порядок именно такой. В облачной сессии ``gh`` нет вовсе, и там работает
    только окружение; в локальном окне ``gh`` обычно авторизован через keyring,
    и переменных не заведено — спросить у него дешевле, чем требовать ручной
    настройки. Сам вопрос квоту не тратит: это чтение локального конфига.

    Raises:
        MissingToken: токена нет нигде — с указанием, что именно сделать.
    """
    source = os.environ if env is None else env
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = source.get(name, "").strip()
        if value:
            return value
    if shutil.which("gh") is not None:
        try:
            raw = subprocess.check_output(
                ["gh", "auth", "token"],
                text=True,
                # Ответ читается как UTF-8 независимо от кодовой страницы
                # консоли: на cp1251 голый декод падал UnicodeDecodeError.
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            raw = ""
        if raw:
            return raw
    raise MissingToken(
        "нет токена GitHub: задайте GH_TOKEN (или GITHUB_TOKEN) в окружении, "
        "либо авторизуйте gh CLI (`gh auth login`)"
    )


def _cache_dir() -> pathlib.Path:
    """Куда складывать ETag'и. Переопределяется ``GH_REST_CACHE``."""
    override = os.environ.get("GH_REST_CACHE", "").strip()
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".cache" / "stepik-grader" / "gh-rest"


def _cache_path(url: str, token: str) -> pathlib.Path:
    """Файл кэша для пары «URL, токен».

    Токен входит в ключ (отпечатком, не значением) намеренно: ответ ``304``
    отдаёт данные из кэша, не спрашивая GitHub, — и без разделения по токену
    второй аккаунт на той же машине получил бы из кэша то, чего его правами не
    видно. Отпечаток — хеш, сам токен на диск не попадает.
    """
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    digest = hashlib.sha256(f"{fingerprint}:{url}".encode()).hexdigest()[:32]
    return _cache_dir() / f"{digest}.json"


def _cache_read(url: str, token: str) -> tuple[str, Any] | None:
    """Прочитать ``(etag, данные)`` из кэша; ``None`` — кэша нет или он битый."""
    path = _cache_path(url, token)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    etag = payload.get("etag")
    if not isinstance(etag, str) or "data" not in payload:
        return None
    return etag, payload["data"]


def _cache_write(url: str, token: str, etag: str, data: Any) -> None:
    """Сохранить ответ под его ``ETag``. Сбой записи — не ошибка запроса."""
    path = _cache_path(url, token)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"etag": etag, "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        return


def _quota_from_headers(headers: Any) -> tuple[int | None, int, str]:
    """Из заголовков ответа — ``(remaining, reset, resource)``.

    ``remaining`` — ``None``, когда заголовка нет вовсе. Разница существенная:
    «ноль» и «неизвестно» ведут к противоположным действиям, и подмена второго
    первым уже выдавала отказ по политике за исчерпанную квоту (issue #1273).
    """

    def _optional_int(name: str) -> int | None:
        raw = headers.get(name) if headers else None
        if raw is None or not str(raw).strip():
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    resource = (headers.get("x-ratelimit-resource") if headers else None) or "core"
    return (
        _optional_int("x-ratelimit-remaining"),
        _optional_int("x-ratelimit-reset") or 0,
        str(resource),
    )


def quota_floor(*, env: dict[str, str] | None = None) -> int:
    """Порог стоп-крана: ниже него конвейер не начинает новую операцию.

    Берётся из ``GH_REST_QUOTA_FLOOR``; мусор в переменной — это
    :data:`DEFAULT_QUOTA_FLOOR`, а не ноль: опечатка не должна молча снимать
    защиту.
    """
    source = os.environ if env is None else env
    raw = str(source.get(ENV_QUOTA_FLOOR, "")).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_QUOTA_FLOOR
    return max(0, value)


def _note_quota(headers: Any) -> None:
    """Предупредить о низком остатке по заголовкам ответа (бесплатно)."""
    remaining, reset, resource = _quota_from_headers(headers)
    if remaining is None or remaining > quota_floor() or resource in _WARNED_RESOURCES:
        return
    _WARNED_RESOURCES.add(resource)
    when = time.strftime("%H:%M:%S", time.localtime(reset)) if reset else "?"
    print(
        f"ВНИМАНИЕ: квота GitHub ({resource}) на исходе — осталось {remaining} "
        f"при пороге {quota_floor()}, сброс в {when}. Длинные операции лучше "
        "не начинать: брошенная на середине дороже отложенной.",
        file=sys.stderr,
    )


def ensure_quota(
    resource: str = "core", *, floor: int | None = None, **kwargs: Any
) -> Quota | None:
    """Стоп-кран перед дорогой операцией: хватит ли остатка, чтобы начинать.

    Сверка бесплатна — ``rate_limit`` не расходует лимит вовсе, поэтому
    спрашивать можно свободно.

    Args:
        resource: ресурс лимита — ``core``, ``graphql``, ``search``.
        floor: порог; по умолчанию :func:`quota_floor`.

    Returns:
        Остаток по ресурсу, либо ``None``, если GitHub про него не сказал.

    Raises:
        RateLimited: остаток ниже порога — ждать сброса, а не начинать.
    """
    limit = quota_floor() if floor is None else floor
    quota = rate_limit(**kwargs).get(resource)
    if quota is None:
        return None
    if quota.remaining <= limit:
        raise RateLimited(
            f"стоп-кран: остаток {resource} — {quota.remaining} при пороге {limit}",
            reset_at=quota.reset,
            resource=resource,
        )
    return quota


def _error_message(exc: urllib.error.HTTPError) -> str:
    """``message`` из тела ответа — там названа настоящая причина отказа.

    Читается ОДИН раз и до классификации: поток одноразовый, а прежде ветка
    401/403 выходила раньше чтения — и единственная строка, объяснявшая отказ,
    терялась (issue #1273). Именно в ней приходило, например, «Merging into a
    protected base branch is not permitted for this session type».
    """
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (OSError, ValueError, AttributeError):
        return ""
    return str(body.get("message", "")) if isinstance(body, dict) else ""


def _raise_for_error(exc: urllib.error.HTTPError, path: str) -> None:
    """Перевести HTTP-ошибку в осмысленное исключение модуля."""
    remaining, reset, resource = _quota_from_headers(exc.headers)
    retry_after = exc.headers.get("retry-after") if exc.headers else None
    detail = _error_message(exc)
    # Квота — только когда сервер про неё СКАЗАЛ: заголовок есть и равен нулю
    # либо пришёл retry-after. Отсутствие заголовков — это «причина другая»,
    # а не «лимит кончился»: советовать ждать сброса там, где ждать нечего,
    # хуже, чем не советовать ничего.
    if exc.code in (403, 429) and (remaining == 0 or retry_after):
        reset_at = reset
        if not reset_at and retry_after:
            try:
                reset_at = int(time.time()) + int(retry_after)
            except (TypeError, ValueError):
                reset_at = 0
        raise RateLimited(
            f"лимит исчерпан на {path}", reset_at=reset_at, resource=resource
        ) from exc
    if exc.code in (401, 403):
        reason = detail or "причина не названа; обычно это права токена"
        raise GitHubError(f"GitHub отказал ({exc.code}) на {path}: {reason}") from exc
    suffix = f": {detail}" if detail else ""
    raise GitHubError(f"GitHub ответил {exc.code} на {path}{suffix}") from exc


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    opener: Opener | None = None,
    use_cache: bool = True,
) -> Response:
    """Один REST-запрос к GitHub.

    Args:
        method: HTTP-метод (``GET``, ``POST``, ``PUT``, ``PATCH``).
        path: путь API без хоста — ``repos/OWNER/NAME/pulls``.
        body: тело запроса; сериализуется в JSON.
        token: токен; по умолчанию берётся :func:`resolve_token`.
        opener: подменяемый ``urlopen`` — сеть в тестах не нужна.
        use_cache: слать ``If-None-Match`` для GET и хранить ``ETag``.

    Returns:
        Ответ с разобранными данными; при ``304`` — данные из кэша.

    Raises:
        RateLimited: квота исчерпана, надо ждать сброса.
        MissingToken: токена нет ни в окружении, ни у ``gh``.
        GitHubError: прочие отказы GitHub и сетевые сбои.
    """
    url = f"{API}/{path.lstrip('/')}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method.upper())
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", _API_VERSION)
    resolved = token or resolve_token()
    req.add_header("Authorization", f"Bearer {resolved}")
    if payload is not None:
        req.add_header("Content-Type", "application/json")

    cached: tuple[str, Any] | None = None
    if use_cache and req.get_method() == "GET":
        cached = _cache_read(url, resolved)
        if cached is not None:
            req.add_header("If-None-Match", cached[0])

    open_url = opener or _default_opener
    try:
        with open_url(req) as response:  # type: ignore[union-attr]
            raw = response.read().decode("utf-8")
            status = int(getattr(response, "status", 200) or 200)
            etag = response.headers.get("etag") if response.headers else None
            data = json.loads(raw) if raw.strip() else None
            _note_quota(response.headers)
    except urllib.error.HTTPError as exc:
        # 304 приходит именно ошибкой: urllib считает не-2xx исключением. Это
        # успех — и единственный ответ, который квоту не расходует вовсе.
        if exc.code == 304 and cached is not None:
            return Response(status=304, data=cached[1], etag=cached[0], from_cache=True)
        _raise_for_error(exc, path)
        raise  # pragma: no cover - _raise_for_error всегда бросает
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GitHubError(f"REST-запрос не удался ({method} {path}): {exc}") from exc

    if use_cache and req.get_method() == "GET" and etag:
        _cache_write(url, resolved, etag, data)
    return Response(status=status, data=data, etag=etag)


def graphql(
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    floor: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Одна GraphQL-операция — для того единственного, чего нет в REST.

    Это не лазейка обратно в GraphQL, а его экономная форма: дорого стоит не
    сам протокол, а запрос, тянущий десятки полей. Короткая мутация обходится
    в единицы points, поэтому ``auto-merge`` дешевле выполнить здесь, чем
    отдавать MCP-инструменту (~300 points за операцию).

    Перед отправкой срабатывает стоп-кран: начинать операцию на исходе квоты
    хуже, чем отложить её.

    Args:
        query: текст операции.
        variables: переменные операции.
        floor: порог стоп-крана; по умолчанию :func:`quota_floor`.

    Returns:
        Содержимое поля ``data`` ответа.

    Raises:
        RateLimited: остаток ниже порога либо GitHub ответил ``RATE_LIMITED``.
        GitHubError: операция вернула ``errors``.
    """
    kwargs.pop("use_cache", None)
    ensure_quota("graphql", floor=floor, **kwargs)
    payload: dict[str, Any] = {"query": query, "variables": variables or {}}
    answer = request("POST", "graphql", body=payload, use_cache=False, **kwargs).data
    body = answer if isinstance(answer, dict) else {}
    errors = body.get("errors")
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else {}
        detail = str(first.get("message", errors)) if isinstance(first, dict) else str(errors)
        if isinstance(first, dict) and first.get("type") == "RATE_LIMITED":
            raise RateLimited(f"GraphQL отказал по лимиту: {detail}", resource="graphql")
        raise GitHubError(f"GraphQL вернул ошибку: {detail}")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _relaxed_context() -> ssl.SSLContext:
    """Проверка сертификата БЕЗ строгих требований RFC 5280 к его форме.

    Снимается ровно один флаг — ``VERIFY_X509_STRICT``. Цепочка по-прежнему
    проверяется против настроенного бандла, имя хоста сверяется, срок действия
    сверяется: выключено не доверие, а придирчивость к оформлению CA. Контекст
    строится только по явному ``GH_REST_RELAXED_CA=1``; сам по себе модуль его
    не применяет никогда.
    """
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def relaxed_ca_enabled() -> bool:
    """Ослабление строгой проверки формы CA запрошено окружением явно.

    Ровно ``GH_REST_RELAXED_CA=1``: «true», «yes» и опечатки читаются как
    «выключено». Переключатель безопасности не угадывает намерение — цена
    ошибочного «включено» несравнимо выше цены повторить с правильным
    значением, а сообщение об отказе называет ожидаемое значение прямо.
    """
    return os.environ.get(ENV_RELAXED_CA, "").strip() == "1"


def _relaxed_ca_warning() -> str:
    """Текст предупреждения о работе с ослабленной проверкой формы CA."""
    return (
        f"ВНИМАНИЕ: {ENV_RELAXED_CA}=1 — строгая проверка формы CA "
        "(VERIFY_X509_STRICT) снята для этого запроса. Доверие к бандлу, имя "
        "хоста и срок действия проверяются по-прежнему; подробности и риск — "
        "SECURITY.md § Транспорт агентских скриптов."
    )


def _request_context() -> ssl.SSLContext:
    """TLS-контекст запроса: системный набор доверия, прочитанный сейчас.

    Модуль намеренно **не** задаёт ``cafile`` и не тянет ``certifi``: доверенный
    набор настраивается окружением (``SSL_CERT_FILE``), тем же, которым ходят
    ``git`` и остальной инструментарий сессии. ``create_default_context()`` этот
    набор и читает — то есть контекст не переопределяет доверие, а повторяет
    системное.

    Строится он на каждый запрос осознанно: ``urlopen`` **без** явного контекста
    кэширует opener на весь процесс, и доверенный набор в нём фиксируется первым
    же запросом. Явный контекст стоит несколько миллисекунд и делает поведение
    честным — окружение решает, а не порядок вызовов.

    Единственное отклонение — явное ``GH_REST_RELAXED_CA=1``, и о нём говорится
    вслух: предупреждение печатается на КАЖДОМ запросе, а не однажды за процесс.
    Работа с ослабленной проверкой не должна выглядеть как обычная — молчаливое
    ослабление ровно и чинит issue #1259.
    """
    if not relaxed_ca_enabled():
        return ssl.create_default_context()
    print(_relaxed_ca_warning(), file=sys.stderr)
    return _relaxed_context()


def _is_strict_ca_rejection(exc: urllib.error.URLError) -> bool:
    """Отказ именно из-за строгой проверки ФОРМЫ CA, а не из-за недоверия."""
    reason = exc.reason
    if not isinstance(reason, ssl.SSLCertVerificationError):
        return False
    return "key usage" in str(reason).lower()


def _tls_help(exc: urllib.error.URLError) -> str:
    """Что именно не так с TLS и что с этим делать — без тихого обхода.

    Два разных отказа лечатся по-разному, и общая подсказка про
    ``SSL_CERT_FILE`` для второго из них была бы ложным следом.
    """
    if not _is_strict_ca_rejection(exc):
        return (
            f"TLS: сертификат сервера не прошёл проверку ({exc.reason}). "
            "Обычная причина — доверенный набор без вашего корневого "
            "сертификата: укажите бандл штатной переменной SSL_CERT_FILE "
            "(так же настраиваются git и остальной инструментарий). "
            "Автоматически проверка не ослабляется."
        )
    return (
        "TLS: CA-сертификат отвергнут строгой проверкой ФОРМЫ, а не как "
        f"недоверенный ({exc.reason}). Python 3.13 включил VERIFY_X509_STRICT "
        "в ssl.create_default_context(); флаг требует от CA расширения "
        "keyUsage по RFC 5280, а у перехватывающих корпоративных прокси его "
        "часто нет. SSL_CERT_FILE здесь не поможет: бандл уже тот, что нужно, "
        "не проходит именно его оформление. Варианты — исправить CA прокси "
        "либо, приняв риск, выставить "
        f"{ENV_RELAXED_CA}=1 (что именно ослабляется — SECURITY.md "
        "§ Транспорт агентских скриптов). Молча модуль этого не делает."
    )


def _default_opener(req: urllib.request.Request) -> Any:
    """Реальный ``urlopen`` с таймаутом — подменяется в тестах.

    Отката на ослабленную проверку здесь нет ни при каком отказе (issue
    #1259). Прежняя редакция повторяла запрос со снятым ``VERIFY_X509_STRICT``
    сама, и это шло против инварианта проекта: невыполнимая гарантия — громкий
    отказ, а не автоматический обход (ср. недоступный backend песочницы, где
    ответ — ``parser.error``, а не молчаливый ``LocalRunner``).

    Ослабление осталось возможным, но стало решением окружения: только по
    ``GH_REST_RELAXED_CA=1`` и с предупреждением на каждый запрос. Без
    переменной сбой TLS превращается в :class:`TlsVerificationError`, чей текст
    называет настоящую причину и способ починки.
    """
    try:
        return urllib.request.urlopen(req, timeout=_TIMEOUT, context=_request_context())
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLError):
            raise TlsVerificationError(_tls_help(exc)) from exc
        raise


def _get(path: str, **kwargs: Any) -> Any:
    """GET, возвращающий сразу данные — сокращение для операций ниже."""
    return request("GET", path, **kwargs).data


def list_pulls(
    repo: str = DEFAULT_REPO,
    *,
    state: str = "open",
    **kwargs: Any,
) -> list[PullSummary]:
    """Открытые PR: номер, ветка, автор, черновик, время правки."""
    query = urllib.parse.urlencode({"state": state, "per_page": 100, "sort": "created"})
    data = _get(f"repos/{repo}/pulls?{query}", **kwargs)
    items = data if isinstance(data, list) else []
    return [
        PullSummary(
            number=int(item.get("number", 0)),
            title=str(item.get("title", "")),
            branch=str(item.get("head", {}).get("ref", "")),
            base=str(item.get("base", {}).get("ref", "")),
            author=str(item.get("user", {}).get("login", "")),
            draft=bool(item.get("draft", False)),
            updated_at=str(item.get("updated_at", "")),
            sha=str(item.get("head", {}).get("sha", "")),
            fork=str(item.get("head", {}).get("repo", {}).get("full_name", "")) != repo,
            labels=tuple(
                str(label.get("name", ""))
                for label in (item.get("labels") or [])
                if isinstance(label, dict) and label.get("name")
            ),
            body=str(item.get("body") or ""),
        )
        for item in items
        if isinstance(item, dict)
    ]


def merged_pulls(
    repo: str = DEFAULT_REPO,
    *,
    limit: int = 30,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Последние слитые PR — номер, тело и время слияния (issue #1419).

    Отдельно от :func:`list_pulls`, потому что предмет другой: тому нужен
    открытый конвейер, а здесь — судьба задач ПОСЛЕ слияния, и ``PullSummary``
    времени слияния не несёт. Закрытый без слияния PR ничего не обещал и в
    выборку не входит.

    Один запрос на всё: список закрытых приходит страницей, ходить за каждым
    PR отдельно значило бы платить по запросу там, где хватает одного.

    Args:
        repo: владелец/репозиторий.
        limit: сколько последних слитых вернуть.

    Returns:
        Слитые PR, свежие первыми.
    """
    query = urllib.parse.urlencode(
        {"state": "closed", "per_page": 100, "sort": "updated", "direction": "desc"}
    )
    data = _get(f"repos/{repo}/pulls?{query}", **kwargs)
    items = data if isinstance(data, list) else []
    merged = [item for item in items if isinstance(item, dict) and item.get("merged_at")]
    merged.sort(key=lambda item: str(item.get("merged_at")), reverse=True)
    return merged[:limit]


def pull(repo: str, number: int, **kwargs: Any) -> dict[str, Any]:
    """Сам PR — состояние, черновик, ``mergeable_state``, head/base."""
    data = _get(f"repos/{repo}/pulls/{number}", **kwargs)
    return data if isinstance(data, dict) else {}


def pull_checks(repo: str, sha: str, **kwargs: Any) -> dict[str, Any]:
    """Check-runs коммита — сырой ответ, разбор остаётся за вызывающим."""
    data = _get(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", **kwargs)
    return data if isinstance(data, dict) else {}


def workflow_runs(repo: str, sha: str, **kwargs: Any) -> dict[str, Any]:
    """Прогоны Actions для коммита — сырой ответ."""
    data = _get(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100", **kwargs)
    return data if isinstance(data, dict) else {}


def main_run(repo: str = DEFAULT_REPO, **kwargs: Any) -> dict[str, Any]:
    """Последние прогоны ``ci.yml`` на ``main`` — сырой ответ.

    Отдельный дешёвый запрос: фильтр по workflow и ветке делает сам GitHub,
    поэтому одна страница отвечает и на «занята ли очередь», и на «не красная
    ли ``main``».

    **Событие намеренно НЕ фильтруется** (issue #1347). Прежний
    ``event=push`` означал, что здоровье ``main`` доказывается только прогоном
    от мержа, — и заморозка очереди по красной ``main`` (issue #1326) снималась
    ровно тем действием, которое сама же и блокировала:

    - push в ``main`` бывает только от мержа, а мерж заморожен;
    - ночной ``schedule`` и ручной ``workflow_dispatch`` идут на том же
      состоянии ``main``, зеленеют — и под фильтр не попадали;
    - оставался повтор упавшего прогона, а это ``actions:write``, которого у
      облачной сессии нет: запись закрывает прокси.

    Живой замер: восемь готовых PR простояли пятый час на прогоне, упавшем в
    одной ячейке matrix из-за коммита, менявшего три файла документации и ноль
    строк кода.

    Отбрасывать прогоны PR фильтр по событию не помогал и раньше: у них
    ``branch`` — это head-ветка, поэтому ``branch=main`` их и так не выбирает.
    Проверено запросом обоих вариантов: ответы совпадают.
    """
    data = _get(
        f"repos/{repo}/actions/workflows/{_CI_WORKFLOW}/runs?branch=main&per_page=10",
        **kwargs,
    )
    return data if isinstance(data, dict) else {}


def compare(repo: str, base: str, head: str, **kwargs: Any) -> Divergence:
    """Насколько ``head`` отстал от ``base`` и ушёл вперёд.

    ``behind_by`` спрашивается напрямую, а не выводится из ``mergeable_state``:
    тот показывает ``behind`` только при включённой защите «Require branches to
    be up to date», и без неё отставание было бы не видно вовсе.
    """
    data = _get(f"repos/{repo}/compare/{base}...{head}", **kwargs)
    payload = data if isinstance(data, dict) else {}
    ahead = payload.get("ahead_by")
    behind = payload.get("behind_by")
    return Divergence(
        ahead=ahead if isinstance(ahead, int) else 0,
        behind=behind if isinstance(behind, int) else 0,
    )


def pull_files(repo: str, number: int, **kwargs: Any) -> list[str]:
    """Файлы, которые меняет PR — для правила «сначала пересекающиеся»."""
    data = _get(f"repos/{repo}/pulls/{number}/files?per_page=100", **kwargs)
    items = data if isinstance(data, list) else []
    return sorted(str(item.get("filename", "")) for item in items if isinstance(item, dict))


# issue #1326: шкала приоритета очереди. Порядок ровно тот, что записан в
# CLAUDE.md, — механизм исполняет правило, а не пересказывает его своими
# словами. Метки ставит владелец: `blocker` на PR означает «чинит красный
# main», остальное наследуется от закрываемой задачи.
PRIORITY_LABELS: tuple[str, ...] = (
    "blocker",
    "P0",
    "priority-high",
    "P1",
    "priority-medium",
    "P2",
    "P3",
)
_DEFAULT_PRIORITY = len(PRIORITY_LABELS)

# `Closes #12`, `fixes #12`, `resolve #12` — формы, которые GitHub понимает как
# закрытие задачи. Приоритет наследуется по этой же связи: дублировать метку на
# PR не нужно, а рассинхрону тогда неоткуда взяться.
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def closes_issues(body: str) -> list[int]:
    """Номера задач, которые PR закрывает по своему телу — без сети."""
    return [int(number) for number in _CLOSES_RE.findall(body or "")]


def priority_rank(labels: Iterable[str]) -> tuple[int, str]:
    """Место в шкале и его обоснование; чем меньше число, тем раньше мержить."""
    known = {label.lower(): label for label in labels}
    for rank, name in enumerate(PRIORITY_LABELS):
        if name.lower() in known:
            return rank, f"приоритет {known[name.lower()]}"
    return _DEFAULT_PRIORITY, "по готовности"


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """Один PR глазами очереди мержа."""

    number: int
    title: str
    ready: bool
    #: Почему не готов; у готового пусто.
    reason: str = ""
    #: Изменённые файлы — спрашиваются только у готовых, остальным не нужны.
    files: tuple[str, ...] = ()
    #: Номера готовых PR, с которыми есть общий файл.
    overlaps: tuple[int, ...] = ()
    #: PR из форка: место в очереди у него обычное, а ветку из `main` за него
    #: не обновить — репозиторий чужой (issue #1287).
    fork: bool = False
    #: Место в шкале приоритета: меньше — раньше (issue #1326).
    priority: int = _DEFAULT_PRIORITY
    #: Чем приоритет обоснован — метка или «по готовности». Показывается в
    #: выводе: иначе порядок выглядит произвольным, как до #1326.
    priority_reason: str = "по готовности"

    def describe(self, position: int, total_ahead: int) -> str:
        """Строка списка: место, номер, заголовок и что с ним делать."""
        head = "  ← обновлять только этот" if position == 1 else f"  впереди: {total_ahead}"
        shared = ""
        if self.overlaps:
            names = ", ".join(f"#{n}" for n in self.overlaps)
            shared = f" · общий файл с {names}"
        why = "" if self.priority >= _DEFAULT_PRIORITY else f" · {self.priority_reason}"
        return f"{position}. #{self.number}  {self.title}{head}{why}{shared}"


@dataclasses.dataclass(frozen=True)
class QueueReport:
    """Очередь мержа целиком: кого обновлять, кто ждёт, что с ``main``."""

    ready: tuple[QueueEntry, ...]
    waiting: tuple[QueueEntry, ...]
    main_busy: bool
    main_red: bool

    @property
    def head(self) -> QueueEntry | None:
        """Первый в очереди — единственный, кого обновляют из ``main``."""
        return self.ready[0] if self.ready else None

    def position(self, number: int) -> int:
        """Место PR в очереди, считая с 1; 0 — его в очереди нет."""
        for index, entry in enumerate(self.ready, start=1):
            if entry.number == number:
                return index
        return 0

    def ahead_of(self, number: int) -> list[int]:
        """Номера PR, стоящих перед этим."""
        place = self.position(number)
        return [entry.number for entry in self.ready[: place - 1]] if place else []


def queue_order(entries: list[QueueEntry]) -> list[QueueEntry]:
    """Порядок мержа среди готовых PR — чистая функция, без сети.

    Правило порядка ровно то, что записано в ``CLAUDE.md``:

    1. **приоритет** (issue #1326) — ``blocker``, затем ``P0``,
       ``priority-high`` и далее по шкале :data:`PRIORITY_LABELS`. Метку ставит
       владелец на задачу, а PR наследует её через ``Closes #N``;
    2. **общий файл** с другим готовым PR: их конфликт вскроется всё равно, и
       дешевле вскрыть его сразу;
    3. **готовность** — стабильно по номеру, то есть кто раньше пришёл.

    «Чинит красный ``main``» решается не здесь, а в :func:`merge_queue`: при
    красной базе очередь не переупорядочивается, а **замирает** — двигать
    можно только PR с меткой ``blocker``.
    """
    by_number = {entry.number: entry for entry in entries}
    linked: dict[int, list[int]] = {number: [] for number in by_number}
    for first in entries:
        for second in entries:
            if first.number >= second.number:
                continue
            if set(first.files) & set(second.files):
                linked[first.number].append(second.number)
                linked[second.number].append(first.number)
    marked = [
        dataclasses.replace(entry, overlaps=tuple(sorted(linked[entry.number])))
        for entry in entries
    ]
    # issue #1326: приоритет — первый ключ сортировки. До него порядок считался
    # только по готовности, и срочный PR стоял в общей череде наравне с
    # косметикой: владелец мог сколько угодно называть задачу приоритетной, на
    # очередь это не влияло никак.
    return sorted(
        marked,
        key=lambda entry: (entry.priority, 0 if entry.overlaps else 1, entry.number),
    )


def merge_queue(repo: str = DEFAULT_REPO, **kwargs: Any) -> QueueReport:
    """Собрать очередь мержа из состояния API (issue #1282).

    Очередь **вычисляется, а не хранится**: ни меток, ни файла состояния, ни
    табло в issue. Хранимый реестр расходился бы между окнами и протухал, а
    порядок и так однозначно выводится из того, что уже есть в API.

    Цена запроса: один список PR плюс по одному запросу проверок на каждый
    открытый PR и по одному запросу файлов на каждый **готовый**. У неготовых
    файлы не спрашиваются — их порядок всё равно не считается.
    """
    pulls = [item for item in list_pulls(repo, **kwargs) if not item.draft]
    entries: list[QueueEntry] = []
    waiting: list[QueueEntry] = []
    # Метки закрываемых задач спрашиваются один раз на задачу: два PR могут
    # закрывать одну и ту же, а лишний запрос здесь — это лишний запрос на
    # каждом обходе очереди.
    issue_labels: dict[int, tuple[str, ...]] = {}
    for item in pulls:
        total, completed, red = summarize_checks(pull_checks(repo, item.sha, **kwargs))
        if not total:
            waiting.append(
                QueueEntry(item.number, item.title, False, "проверок нет — CI не стартовал")
            )
        elif red:
            waiting.append(QueueEntry(item.number, item.title, False, "красные: " + ", ".join(red)))
        elif completed < total:
            waiting.append(
                QueueEntry(item.number, item.title, False, f"проверки идут ({completed}/{total})")
            )
        else:
            files = pull_files(repo, item.number, **kwargs)
            rank, why = _priority_for(repo, item, issue_labels, **kwargs)
            entries.append(
                QueueEntry(
                    item.number,
                    item.title,
                    True,
                    files=tuple(files),
                    fork=item.fork,
                    priority=rank,
                    priority_reason=why,
                )
            )

    runs = main_run(repo, **kwargs)
    listed = [run for run in runs.get("workflow_runs", []) if isinstance(run, dict)]
    busy = any(run.get("status") != "completed" for run in listed)
    done = [run for run in listed if run.get("status") == "completed"]
    red_main = bool(done) and done[0].get("conclusion") not in _OK_CONCLUSIONS
    ordered = queue_order(entries)
    if red_main:
        # issue #1326: красный `main` — не «пропусти вперёд», а «очередь
        # замирает». Мержить поверх сломанной базы нельзя вообще: проверки
        # остальных всё равно пройдут на ней же. Двигаем только то, что её
        # чинит, — это метка `blocker` на самом PR, потому что срочно здесь
        # конкретное исправление, а не задача.
        frozen = [entry for entry in ordered if "blocker" not in _entry_labels(entry, pulls)]
        ordered = [entry for entry in ordered if entry not in frozen]
        waiting.extend(
            dataclasses.replace(
                entry,
                ready=False,
                reason="красный main — очередь ждёт фикса (метка blocker двигает вне очереди)",
            )
            for entry in frozen
        )
    return QueueReport(
        ready=tuple(ordered),
        waiting=tuple(sorted(waiting, key=lambda entry: entry.number)),
        main_busy=busy,
        main_red=red_main,
    )


def _entry_labels(entry: QueueEntry, pulls: list[PullSummary]) -> tuple[str, ...]:
    """Метки самого PR по уже полученному списку — без дополнительного запроса."""
    for item in pulls:
        if item.number == entry.number:
            return item.labels
    return ()


def _priority_for(
    repo: str,
    item: PullSummary,
    cache: dict[int, tuple[str, ...]],
    **kwargs: Any,
) -> tuple[int, str]:
    """Приоритет PR: свои метки плюс метки задач, которые он закрывает.

    Дублировать приоритет на PR не нужно (issue #1326): он живёт на задаче, где
    и принимается решение о важности, а PR связан с ней строкой ``Closes #N``.
    Одна пометка вместо двух — и рассинхрону неоткуда взяться. Исключение одно:
    ``blocker`` на самом PR означает «чинит красный main», то есть срочность
    конкретного исправления.
    """
    labels = list(item.labels)
    for number in closes_issues(item.body):
        if number not in cache:
            try:
                data = issue(repo, number, **kwargs)
            except GitHubError:
                # Задача недоступна (удалена, приватная, опечатка в номере) —
                # это не повод ронять расчёт всей очереди.
                cache[number] = ()
            else:
                cache[number] = tuple(
                    str(label.get("name", ""))
                    for label in (data.get("labels") or [])
                    if isinstance(label, dict) and label.get("name")
                )
        labels.extend(cache[number])
    return priority_rank(labels)


def create_pull(
    repo: str = DEFAULT_REPO,
    *,
    title: str,
    head: str,
    base: str = "main",
    body: str = "",
    draft: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Создать PR. Возвращает ответ GitHub (с ``number`` и ``html_url``).

    **Автором становится владелец токена.** В облачной агентской сессии токен
    принадлежит приложению ``claude[bot]``, и созданный так PR получает автора
    ``claude[bot]`` (тип ``Bot``). Последствие не косметическое: workflow
    код-ревью отказывается работать для PR, инициированных ботом —
    «Workflow initiated by non-human actor: claude (type: Bot)», — и
    обязательная проверка ``claude-review`` краснеет, а мерж-гейт встаёт.

    Поймано на живом PR: тот же набор изменений, созданный через MCP (то есть
    от человека), ревью проходит, а созданный здесь — нет.

    Поэтому **создание PR из облачной сессии остаётся за MCP**, а этот вызов —
    для локального окна и для случаев, где ревью не требуется. Экономии на
    квоте от него почти нет: PR создаётся однажды, а платит окно за повторный
    опрос статусов, который REST и забирает.
    """
    data = request(
        "POST",
        f"repos/{repo}/pulls",
        body={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        **kwargs,
    ).data
    return data if isinstance(data, dict) else {}


def edit_pull(
    repo: str,
    number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Поправить заголовок или тело уже открытого PR (``PATCH .../pulls/N``).

    Понадобилось из-за связи с задачей. Тело PR веток ``agent/**`` берётся из
    сообщения коммита один раз, при открытии, — а `Closes #N` иногда становится
    известен позже (задача заведена после, прежняя закрыта). Без этой команды
    оставалось советовать человеку открыть браузер, хотя операция ровно на один
    REST-запрос: инструмент, который может сделать сам, делает.

    Args:
        repo: ``владелец/репозиторий``.
        number: номер PR.
        title: новый заголовок; ``None`` — оставить прежний.
        body: новое тело; ``None`` — оставить прежнее.

    Returns:
        Ответ GitHub по обновлённому PR.

    Raises:
        ValueError: не названо, что менять.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if not payload:
        raise ValueError("нечего менять: назовите --title или --body/--body-file")
    data = request("PATCH", f"repos/{repo}/pulls/{number}", body=payload, **kwargs).data
    return data if isinstance(data, dict) else {}


def update_branch(repo: str, number: int, **kwargs: Any) -> dict[str, Any]:
    """Подтянуть базовую ветку в ветку PR (``PUT .../update-branch``).

    Обновление обязательно перед мержем: устаревшая ветка проверена на
    состоянии, которого после мержа не будет.
    """
    data = request("PUT", f"repos/{repo}/pulls/{number}/update-branch", body={}, **kwargs).data
    return data if isinstance(data, dict) else {}


def merge_pull(
    repo: str,
    number: int,
    *,
    method: str = "squash",
    title: str | None = None,
    body: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Смержить PR. По умолчанию ``squash`` — как принято в проекте."""
    payload: dict[str, Any] = {"merge_method": method}
    if title is not None:
        payload["commit_title"] = title
    if body is not None:
        payload["commit_message"] = body
    data = request("PUT", f"repos/{repo}/pulls/{number}/merge", body=payload, **kwargs).data
    return data if isinstance(data, dict) else {}


_AUTO_MERGE_MUTATION = """
mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
  enablePullRequestAutoMerge(
    input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}
  ) {
    pullRequest {
      number
      autoMergeRequest { enabledAt mergeMethod }
    }
  }
}
"""


def enable_auto_merge(
    repo: str,
    number: int,
    *,
    method: str = "squash",
    **kwargs: Any,
) -> dict[str, Any]:
    """Включить авто-мерж: PR смержится сам, когда пройдут проверки.

    Именно эта функция снимает нужду сидеть и опрашивать статусы — а опрос и
    есть то, что дважды за день выжигало квоту до нуля.

    ``node_id`` берётся из REST (один дешёвый запрос), мутация уходит на
    ``POST /graphql``: REST-эквивалента у ``enablePullRequestAutoMerge`` не
    существует.

    Raises:
        GitHubError: у PR нет ``node_id`` — отвечать мутации нечем.
    """
    node_id = pull(repo, number, **kwargs).get("node_id")
    if not node_id:
        raise GitHubError(f"PR #{number}: GitHub не вернул node_id, включать авто-мерж нечему")
    data = graphql(
        _AUTO_MERGE_MUTATION,
        {"pullRequestId": str(node_id), "mergeMethod": method.upper()},
        **kwargs,
    )
    enabled = data.get("enablePullRequestAutoMerge", {})
    result = enabled.get("pullRequest", {}) if isinstance(enabled, dict) else {}
    return result if isinstance(result, dict) else {}


_DISABLE_AUTO_MERGE_MUTATION = """
mutation($pullRequestId: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId}) {
    pullRequest { number autoMergeRequest { enabledAt } }
  }
}
"""


def disable_auto_merge(repo: str, number: int, **kwargs: Any) -> dict[str, Any]:
    """Выключить авто-мерж — обратная сторона :func:`enable_auto_merge`.

    Живёт здесь по той же причине, что и включение: REST-эквивалента у
    ``disablePullRequestAutoMerge`` нет, а короткая мутация стоит единицы
    points против ~300 за ту же операцию через MCP-инструмент.

    Зачем это нужно (issue #1303): метка ``merge-when-green`` — выраженное
    согласие на мерж, и оно обязано быть **обратимым**. Снял метку — согласие
    отозвано; если бы авто-мерж при этом оставался включённым, отозвать решение
    было бы нечем, и PR уехал бы в ``main`` вопреки автору.

    Raises:
        GitHubError: у PR нет ``node_id`` — отвечать мутации нечем.
    """
    node_id = pull(repo, number, **kwargs).get("node_id")
    if not node_id:
        raise GitHubError(f"PR #{number}: GitHub не вернул node_id, выключать авто-мерж нечему")
    data = graphql(_DISABLE_AUTO_MERGE_MUTATION, {"pullRequestId": str(node_id)}, **kwargs)
    disabled = data.get("disablePullRequestAutoMerge", {})
    result = disabled.get("pullRequest", {}) if isinstance(disabled, dict) else {}
    return result if isinstance(result, dict) else {}


def issues_with_label(
    repo: str, label: str, *, state: str = "open", **kwargs: Any
) -> list[dict[str, Any]]:
    """Issue и PR с указанной меткой (один запрос вместо перебора).

    GitHub отдаёт PR через тот же эндпоинт issue, помечая их полем
    ``pull_request`` — по нему вызывающая сторона и отличает одно от другого.

    Args:
        repo: владелец/репозиторий.
        label: метка.
        state: ``open`` (по умолчанию), ``closed`` или ``all``. Умолчание
            открытое: почти всем зовущим нужна живая работа, а закрытые нужны
            там, где предмет — само закрытие (`check_container_closure.py`).
    """
    query = urllib.parse.urlencode({"labels": label, "state": state, "per_page": 100})
    data = _get(f"repos/{repo}/issues?{query}", **kwargs)
    items = data if isinstance(data, list) else []
    return [item for item in items if isinstance(item, dict)]


def sub_issues(repo: str, number: int, **kwargs: Any) -> list[dict[str, Any]]:
    """Дочерние issue эпика. С 2025 года это REST, а не GraphQL."""
    data = _get(f"repos/{repo}/issues/{number}/sub_issues?per_page=100", **kwargs)
    items = data if isinstance(data, list) else []
    return [item for item in items if isinstance(item, dict)]


def add_sub_issue(repo: str, parent: int, child: int, **kwargs: Any) -> dict[str, Any]:
    """Подчинить issue ``child`` эпику ``parent``.

    GitHub ждёт внутренний ``id`` дочернего issue, а человек оперирует его
    номером — номер и превращается в ``id`` одним дополнительным GET.

    Raises:
        GitHubError: у дочернего issue нет ``id``.
    """
    child_id = issue(repo, child, **kwargs).get("id")
    if not child_id:
        raise GitHubError(f"issue #{child}: GitHub не вернул id, подчинять нечего")
    data = request(
        "POST",
        f"repos/{repo}/issues/{parent}/sub_issues",
        body={"sub_issue_id": int(child_id)},
        **kwargs,
    ).data
    return data if isinstance(data, dict) else {}


_CLOSE_REASONS = ("completed", "not_planned", "duplicate")


def close_issue(
    repo: str,
    number: int,
    *,
    reason: str = "completed",
    **kwargs: Any,
) -> dict[str, Any]:
    """Закрыть issue с указанием причины.

    Причина обязательна по смыслу: «сделано» и «не будем делать» — разные
    исходы, и трекер, где всё закрыто без разбора, перестаёт отвечать на
    вопрос «что мы решили не делать».

    Raises:
        ValueError: причина не из набора, который принимает GitHub. Отказ
            здесь дешевле, чем ``422`` после отправки.
    """
    if reason not in _CLOSE_REASONS:
        raise ValueError(f"причина закрытия должна быть одной из {_CLOSE_REASONS}: {reason!r}")
    payload = {"state": "closed", "state_reason": reason}
    data = request("PATCH", f"repos/{repo}/issues/{number}", body=payload, **kwargs).data
    return data if isinstance(data, dict) else {}


def comment_issue(repo: str, number: int, text: str, **kwargs: Any) -> dict[str, Any]:
    """Оставить комментарий к issue или PR — в REST это один и тот же ресурс."""
    data = request(
        "POST", f"repos/{repo}/issues/{number}/comments", body={"body": text}, **kwargs
    ).data
    return data if isinstance(data, dict) else {}


def update_comment(repo: str, comment_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
    """Переписать существующий комментарий (issue и PR — один ресурс).

    Нужна тем механизмам, которые ведут ОДИН комментарий и обновляют его:
    новый на каждый прогон превращает тред в ленту (``rules_inbox``,
    ``report_failed_tests``).
    """
    data = request(
        "PATCH", f"repos/{repo}/issues/comments/{comment_id}", body={"body": text}, **kwargs
    ).data
    return data if isinstance(data, dict) else {}


def issue(repo: str, number: int, **kwargs: Any) -> dict[str, Any]:
    """Одно issue: заголовок, состояние, метки. Без тела и полей — их не просим."""
    data = _get(f"repos/{repo}/issues/{number}", **kwargs)
    return data if isinstance(data, dict) else {}


def create_issue(
    repo: str,
    *,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Завести issue. Метки — сразу, чтобы не платить вторым запросом."""
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    data = request("POST", f"repos/{repo}/issues", body=payload, **kwargs).data
    return data if isinstance(data, dict) else {}


def update_issue(
    repo: str,
    number: int,
    *,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Обновить заголовок, тело и/или состояние issue.

    ``state`` нужен переоткрытию: адресат находок один, и его история читается,
    только пока номер не меняется. Обход, не умевший открыть закрытую задачу,
    заводил вместо неё соседнюю (issue #1404).

    Raises:
        ValueError: не задано ни одного поля — пустой ``PATCH`` потратил бы
            запрос и ничего не изменил.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    if not payload:
        raise ValueError("нечего обновлять: задайте title и/или body")
    data = request("PATCH", f"repos/{repo}/issues/{number}", body=payload, **kwargs).data
    return data if isinstance(data, dict) else {}


def ensure_label(
    repo: str,
    name: str,
    *,
    color: str = "ededed",
    description: str = "",
    **kwargs: Any,
) -> bool:
    """Создать метку, если её ещё нет; ``True`` — создали, ``False`` — была.

    Нужна тем, кто ставит метку автоматически (issue #1313: очередь мержа метит
    конфликтный PR). ``POST /labels`` на существующее имя отвечает ``422`` —
    это не ошибка, а «уже есть», и трактовать её как отказ значило бы ронять
    механизм на второй же метке.
    """
    try:
        request(
            "POST",
            f"repos/{repo}/labels",
            body={"name": name, "color": color, "description": description},
            **kwargs,
        )
    except GitHubError as exc:
        if "422" in str(exc):
            return False
        raise
    return True


def add_labels(repo: str, number: int, labels: list[str], **kwargs: Any) -> list[str]:
    """Добавить метки, не трогая уже стоящие; вернуть итоговый набор."""
    data = request(
        "POST", f"repos/{repo}/issues/{number}/labels", body={"labels": labels}, **kwargs
    ).data
    items = data if isinstance(data, list) else []
    return [str(item.get("name", "")) for item in items if isinstance(item, dict)]


def remove_label(repo: str, number: int, label: str, **kwargs: Any) -> bool:
    """Снять одну метку. Отсутствующая метка — не ошибка, а «уже снята»."""
    try:
        request(
            "DELETE",
            f"repos/{repo}/issues/{number}/labels/{urllib.parse.quote(label)}",
            **kwargs,
        )
    except GitHubError as exc:
        if "404" in str(exc):
            return False
        raise
    return True


def issue_comments(repo: str, number: int, **kwargs: Any) -> list[dict[str, Any]]:
    """Комментарии issue или PR — автор, время, текст."""
    data = _get(f"repos/{repo}/issues/{number}/comments?per_page=100", **kwargs)
    return [item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)]


def branch_runs(
    repo: str = DEFAULT_REPO,
    *,
    branch: str,
    event: str | None = None,
    limit: int = 10,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Прогоны ``ci.yml`` по ветке: статус, заключение, время старта.

    Фильтрует GitHub, а не мы: одна страница вместо выборки из всех прогонов
    репозитория.
    """
    params: dict[str, Any] = {"branch": branch, "per_page": limit}
    if event is not None:
        params["event"] = event
    query = urllib.parse.urlencode(params)
    data = _get(f"repos/{repo}/actions/workflows/{_CI_WORKFLOW}/runs?{query}", **kwargs)
    runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
    return [run for run in runs if isinstance(run, dict)]


def run_jobs(repo: str, run_id: int, **kwargs: Any) -> list[dict[str, Any]]:
    """Job'ы прогона: имя, статус, заключение — чтобы видеть, что именно упало."""
    data = _get(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", **kwargs)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [job for job in jobs if isinstance(job, dict)]


def cancel_run(repo: str, run_id: int, **kwargs: Any) -> bool:
    """Отменить прогон. Нужна не для удобства, а потому что зависший держит очередь.

    Прецедент: прогон висел ``in_progress`` два с половиной часа, и следующие
    мержи в это время вытеснялись из очереди, не начавшись.

    Уже завершённый прогон GitHub отменить не даёт (``409``) — это не сбой, а
    «отменять нечего».
    """
    try:
        request("POST", f"repos/{repo}/actions/runs/{run_id}/cancel", **kwargs)
    except GitHubError as exc:
        if "409" in str(exc):
            return False
        raise
    return True


#: Журнал нестабильности: сюда пишется каждый частичный перезапуск.
FLAKE_LOG = pathlib.Path(__file__).resolve().parent.parent / "docs" / "agent" / "flaky-runs.md"

#: Больше двух попыток означает не мигание, а дефект — и разбирать надо его.
MAX_ATTEMPTS = 2

#: 403 «прав нет»: у облачной сессии закрыта запись в Actions. Отличается от
#: 403 «перезапускать нечего» только текстом, и спутать их — значит утверждать
#: «упавших джобов нет» на прогоне, где джоб упал.
_NO_WRITE_RE = re.compile(r"not accessible by integration|must have admin|forbidden", re.I)

#: 403, которым GitHub отвечает на прогон, где перезапускать действительно нечего.
_NOTHING_TO_RERUN_RE = re.compile(r"no failed jobs|not in a failed state|already", re.I)


def rerun_failed_jobs(repo: str, run_id: int, **kwargs: Any) -> bool:
    """Перезапустить ТОЛЬКО упавшие джобы прогона (issue #1344).

    Упала одна ячейка матрицы — полный перезапуск стоит десятков минут и
    занимает исполнителей, которыми пользуются все остальные PR. REST это
    умеет: ``POST /actions/runs/{id}/rerun-failed-jobs`` сохраняет результаты
    прошедших.

    Возвращает ``False``, только если перезапускать действительно **нечего**:
    прогон без упавших джобов либо уже перезапускаемый.

    **403 бывает двух разных смыслов, и путать их нельзя.** У облачной сессии
    нет ``actions:write`` — прокси закрывает запись, и GitHub отвечает
    ``Resource not accessible by integration``. Прежняя редакция считала любой
    ``403`` за «нечего» и печатала «упавших джобов нет» на прогоне, где джоб
    только что упал: команда врала о состоянии, а окно уходило чинить не то.
    Отказ по правам поднимается ошибкой и называет причину вслух.

    **Перезапуск не чинит, он меняет исход, не меняя причины.** Поэтому вызов
    из CLI обязан сопровождаться записью в :data:`FLAKE_LOG`; см.
    :func:`append_flake_note`.
    """
    try:
        request("POST", f"repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs", **kwargs)
    except GitHubError as exc:
        message = str(exc)
        if _NO_WRITE_RE.search(message):
            raise GitHubError(
                "перезапуск недоступен: у сессии нет прав на запись в Actions "
                f"({message}). Из облака это штатно — прокси закрывает запись; "
                "запустите повтор кнопкой в интерфейсе или из локального окна"
            ) from exc
        if "409" in message or _NOTHING_TO_RERUN_RE.search(message):
            return False
        raise
    return True


def append_flake_note(run_id: int, why: str, *, log: pathlib.Path | None = None) -> None:
    """Дописать строку в журнал нестабильности.

    Зелёное со второго раза — факт о системе, а не о коде: проверка зависит не
    только от него. Без записи экономия оборачивается потерей доверия к набору,
    и через полгода «перезапусти, оно иногда падает» становится нормальным
    ответом.

    Журнал ведётся не памятью, а этой функцией: CLI не даёт перезапустить, не
    записав.
    """
    target = log if log is not None else FLAKE_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(_FLAKE_LOG_HEADER, encoding="utf-8")
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"| {run_id} | {why.strip()} |\n")


_FLAKE_LOG_HEADER = """# Журнал нестабильности прогонов

> Пишется механизмом, а не памятью: `python scripts/gh_rest.py rerun-failed
> <run-id> --why "<что мигнуло>"` дописывает строку сам и без `--why` не
> работает.

**Зачем.** Перезапуск не чинит — он меняет исход, не меняя причины. Прошло со
второго раза значит, что проверка зависит не только от кода, и это факт о
системе, который иначе теряется. Доля прогонов, потребовавших перезапуска, —
измеримая величина, и она не должна расти.

**Как читать.** Строка — один частичный перезапуск. Повторяющийся сюжет здесь
и есть кандидат в задачу: чинить надо причину, а не исход.

| Прогон | Что мигнуло |
|---|---|
"""


def rate_limit(**kwargs: Any) -> dict[str, Quota]:
    """Остаток квоты по ресурсам. Сам запрос лимит не расходует.

    Кэш здесь выключен принудительно, даже если вызывающий просил обратное:
    ``304`` вернул бы остаток на момент прошлого запроса, а вопрос всегда про
    «сейчас».
    """
    kwargs.pop("use_cache", None)
    data = _get("rate_limit", use_cache=False, **kwargs)
    resources = data.get("resources", {}) if isinstance(data, dict) else {}
    quotas: dict[str, Quota] = {}
    for name, payload in resources.items():
        if not isinstance(payload, dict):
            continue
        quotas[str(name)] = Quota(
            resource=str(name),
            limit=int(payload.get("limit", 0)),
            remaining=int(payload.get("remaining", 0)),
            used=int(payload.get("used", 0)),
            reset=int(payload.get("reset", 0)),
        )
    return quotas


def _check_freshness(item: dict[str, Any]) -> tuple[str, int]:
    """Ключ свежести записи проверки: время старта, при равенстве — идентификатор."""
    return str(item.get("started_at") or ""), int(item.get("id") or 0)


def latest_checks_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """По одной, самой свежей записи на каждое имя проверки.

    ``ci.yml`` держит concurrency-группу и гасит устаревшие прогоны, а
    перезапуск (снятие черновика, повторный пуш) создаёт вторую запись с тем же
    именем на том же коммите. В ответе REST обе лежат рядом, и без этого отбора
    отменённый предшественник считался бы красным — то есть свежий зелёный PR
    выпадал бы из очереди мержа навсегда. Тот же приём и по той же причине живёт
    в ``check_pr_ready.py`` (issue #1115).
    """
    freshest: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", ""))
        current = freshest.get(name)
        if current is None or _check_freshness(item) >= _check_freshness(current):
            freshest[name] = item
    return list(freshest.values())


def summarize_checks(check_runs: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Сводка по check-runs: ``(всего, завершено, красные имена)``."""
    listed = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    runs = latest_checks_by_name([item for item in listed if isinstance(item, dict)])
    completed = sum(1 for item in runs if item.get("status") == "completed")
    red = sorted(
        str(item.get("name", "?"))
        for item in runs
        if item.get("status") == "completed" and item.get("conclusion") not in _OK_CONCLUSIONS
    )
    return len(runs), completed, red


def _force_utf8_stdio() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли (issue #1108).

    Вывод русский и со стрелками ``→``; в консоли cp1251 таких символов нет, и
    ``print`` падал ``UnicodeEncodeError`` вместо ответа. No-op на потоках без
    ``reconfigure`` (перехваченных pytest).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _print_json(payload: Any) -> None:
    """Машинный вывод одной строкой."""
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _read_body_file(path: str) -> str:
    """Тело из файла в UTF-8; ``-`` — со стандартного ввода.

    Чтение файлом убирает целый класс бед: длинный текст, переданный
    ``"$(cat file)"``, в PowerShell разбирается иначе, чем в bash, и упирается
    в кавычки и кодовую страницу (issue #1281).
    """
    if path == "-":
        return sys.stdin.read()
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise GitHubError(f"не читается файл тела {path}: {exc}") from exc


def _resolve_body(args: argparse.Namespace, *, positional: str | None = None) -> str:
    """Единый способ получить тело: ``--body``, ``--body-file`` или позиционный.

    Два источника разом — явная ошибка, а не молчаливый выбор одного: молчание
    здесь означает «опубликовали не тот текст», а это уже не откатить.

    Raises:
        GitHubError: тело задано больше чем одним способом.
    """
    body = getattr(args, "body", None)
    body_file = getattr(args, "body_file", None)
    given = [name for name, value in (("--body", body), ("--body-file", body_file)) if value]
    if positional:
        given.append("позиционный текст")
    if len(given) > 1:
        raise GitHubError(f"тело задано несколькими способами ({', '.join(given)}) — оставьте один")
    if body_file:
        return _read_body_file(body_file)
    return positional or body or ""


def _cmd_pulls(args: argparse.Namespace) -> int:
    """Список открытых PR."""
    pulls = list_pulls(args.repo, state=args.state)
    if args.json:
        _print_json([dataclasses.asdict(item) for item in pulls])
        return EXIT_OK
    if not pulls:
        print("Открытых PR нет.")
        return EXIT_OK
    for item in pulls:
        print(item.describe())
    return EXIT_OK


def _cmd_checks(args: argparse.Namespace) -> int:
    """Проверки PR плюс состояние очереди на ``main``."""
    data = pull(args.repo, args.pull)
    sha = str(data.get("head", {}).get("sha", ""))
    if not sha:
        print(f"PR #{args.pull} не найден или без head-коммита.", file=sys.stderr)
        return EXIT_FAIL
    checks = pull_checks(args.repo, sha)
    total, completed, red = summarize_checks(checks)
    runs = main_run(args.repo)
    listed = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    active = [
        str(run.get("name", "workflow")) for run in listed if run.get("status") != "completed"
    ]
    if args.json:
        _print_json(
            {
                "pull": args.pull,
                "sha": sha,
                "total": total,
                "completed": completed,
                "failed": red,
                "main_busy": bool(active),
            }
        )
        return EXIT_OK if not red else EXIT_FAIL
    print(f"PR #{args.pull}: проверок {completed}/{total} завершено")
    if red:
        print("Красные: " + ", ".join(red))
    print("main: " + ("идёт прогон — ждать" if active else "свободна"))
    return EXIT_FAIL if red else EXIT_OK


def _cmd_queue(args: argparse.Namespace) -> int:
    """Очередь мержа: кого обновлять из main, кто ждёт неподвижно."""
    report = merge_queue(args.repo)
    if args.json:
        _print_json(
            {
                "ready": [dataclasses.asdict(entry) for entry in report.ready],
                "waiting": [dataclasses.asdict(entry) for entry in report.waiting],
                "head": report.head.number if report.head else None,
                "main_busy": report.main_busy,
                "main_red": report.main_red,
            }
        )
        return EXIT_OK

    if report.main_red:
        print(
            "main КРАСНАЯ — первым мержится то, что её чинит. Порядок ниже "
            "действует после починки: определить чинящий PR из API нельзя."
        )
    if report.main_busy:
        print("на main идёт прогон — мерж ждёт его завершения")

    print(f"Очередь мержа: готовых {len(report.ready)}, ждут проверок {len(report.waiting)}")
    for position, entry in enumerate(report.ready, start=1):
        print("  " + entry.describe(position, position - 1))
    if report.waiting:
        print("\nВ очередь не входят:")
        for entry in report.waiting:
            print(f"  #{entry.number}  {entry.title} — {entry.reason}")

    head = report.head
    if head is None:
        print("\nОбновлять некого: готовых PR нет.")
        return EXIT_OK
    print(
        f"\nОбновляется ТОЛЬКО голова очереди — остальные стоят неподвижно:\n"
        f"  python scripts/gh_rest.py update-branch {head.number}"
    )
    return EXIT_OK


def _cmd_compare(args: argparse.Namespace) -> int:
    """Отставание ветки PR от базовой."""
    data = pull(args.repo, args.pull)
    sha = str(data.get("head", {}).get("sha", ""))
    base = str(data.get("base", {}).get("ref", "main"))
    if not sha:
        print(f"PR #{args.pull} не найден или без head-коммита.", file=sys.stderr)
        return EXIT_FAIL
    divergence = compare(args.repo, base, sha)
    if args.json:
        _print_json(dataclasses.asdict(divergence) | {"stale": divergence.stale})
        return EXIT_OK
    if divergence.stale:
        print(
            f"Ветка отстала от {base} на {divergence.behind} коммит(ов) — "
            "обновить перед мержем (update-branch)"
        )
        return EXIT_FAIL
    print(f"Ветка свежая относительно {base} (впереди на {divergence.ahead}).")
    return EXIT_OK


def _cmd_create_pr(args: argparse.Namespace) -> int:
    """Создать PR из ветки и сказать правду об авторстве.

    Заранее авторство не узнать: в облачной сессии токены проксированы, и
    ``GET /user`` отвечает человеком, тогда как запись атрибутируется
    приложению. Поэтому проверяем по факту — автор приходит в ответе на
    создание, лишнего запроса не нужно.
    """
    created = create_pull(
        args.repo,
        title=args.title,
        head=args.head,
        base=args.base,
        body=_resolve_body(args),
        draft=args.draft,
    )
    number = created.get("number")
    labels = getattr(args, "label", None) or []
    if number and labels:
        add_labels(args.repo, int(number), labels)
    if args.json:
        _print_json(created)
        return EXIT_OK
    print(f"PR #{number} создан: {created.get('html_url')}")
    author = created.get("user", {}) if isinstance(created.get("user"), dict) else {}
    if author.get("type") == "Bot":
        print(
            f"ВНИМАНИЕ: автор PR — {author.get('login')} (Bot). Workflow код-ревью "
            "отказывается работать для PR, инициированных ботом, и обязательная "
            "проверка claude-review покраснеет.\n"
            "  Авторство должно быть авторским: создавайте PR через MCP "
            "(инструмент create_pull_request), а этой подкомандой пользуйтесь "
            "там, где токен принадлежит человеку.",
            file=sys.stderr,
        )
        return EXIT_FAIL
    return EXIT_OK


def _cmd_edit_pr(args: argparse.Namespace) -> int:
    """Обновить заголовок/тело PR."""
    body = _resolve_body(args) if (args.body or args.body_file) else None
    try:
        updated = edit_pull(args.repo, args.pull, title=args.title, body=body)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(updated)
        return EXIT_OK
    print(f"#{updated.get('number', args.pull)}: обновлено — {updated.get('title', '')}")
    return EXIT_OK


def _cmd_update_branch(args: argparse.Namespace) -> int:
    """Подтянуть базовую ветку в ветку PR."""
    result = update_branch(args.repo, args.pull)
    if args.json:
        _print_json(result)
        return EXIT_OK
    print(f"PR #{args.pull}: {result.get('message', 'ветка обновлена')}")
    return EXIT_OK


def _cmd_merge(args: argparse.Namespace) -> int:
    """Смержить PR."""
    result = merge_pull(args.repo, args.pull, method=args.method)
    if args.json:
        _print_json(result)
        return EXIT_OK if result.get("merged") else EXIT_FAIL
    if result.get("merged"):
        print(f"PR #{args.pull} смержен ({args.method}).")
        return EXIT_OK
    print(f"PR #{args.pull} не смержен: {result.get('message', 'причина неизвестна')}")
    return EXIT_FAIL


def _cmd_issue(args: argparse.Namespace) -> int:
    """Показать issue: состояние, заголовок, метки."""
    found = issue(args.repo, args.number)
    if args.json:
        _print_json(found)
        return EXIT_OK
    labels = ", ".join(str(label.get("name", "")) for label in found.get("labels", []))
    state = found.get("state_reason") or found.get("state")
    print(f"#{found.get('number')} [{state}] {found.get('title', '')}")
    if labels:
        print(f"  метки: {labels}")
    return EXIT_OK


def _cmd_close_issue(args: argparse.Namespace) -> int:
    """Закрыть issue с причиной, при желании — сразу с комментарием.

    Комментарий уходит ПЕРЕД закрытием: правило проекта требует называть исход,
    а комментарий к уже закрытому issue легко потерять из виду.
    """
    if args.comment:
        comment_issue(args.repo, args.number, args.comment)
    closed = close_issue(args.repo, args.number, reason=args.reason)
    if args.json:
        _print_json(closed)
        return EXIT_OK
    print(f"#{args.number} закрыт ({closed.get('state_reason', args.reason)}).")
    return EXIT_OK


def _cmd_auto_merge(args: argparse.Namespace) -> int:
    """Включить авто-мерж на PR."""
    result = enable_auto_merge(args.repo, args.pull, method=args.method)
    if args.json:
        _print_json(result)
        return EXIT_OK
    request_info = result.get("autoMergeRequest") or {}
    method = str(request_info.get("mergeMethod", args.method)).lower()
    print(f"PR #{args.pull}: авто-мерж включён ({method}) — смержится сам, когда позеленеет.")
    return EXIT_OK


def _cmd_sub_issues(args: argparse.Namespace) -> int:
    """Показать дочерние issue эпика."""
    children = sub_issues(args.repo, args.number)
    if args.json:
        _print_json(children)
        return EXIT_OK
    if not children:
        print(f"#{args.number}: дочерних issue нет.")
        return EXIT_OK
    for item in children:
        state = item.get("state_reason") or item.get("state")
        print(f"  #{item.get('number')} [{state}] {item.get('title', '')}")
    return EXIT_OK


def _cmd_add_sub_issue(args: argparse.Namespace) -> int:
    """Подчинить issue эпику."""
    add_sub_issue(args.repo, args.parent, args.child)
    print(f"#{args.child} подчинён эпику #{args.parent}.")
    return EXIT_OK


def _cmd_comment(args: argparse.Namespace) -> int:
    """Оставить комментарий к issue или PR."""
    text = _resolve_body(args, positional=args.text)
    if not text.strip():
        print(
            "Пустой комментарий не отправляю: задайте --body, --body-file или текст.",
            file=sys.stderr,
        )
        return EXIT_FAIL
    posted = comment_issue(args.repo, args.number, text)
    if args.json:
        _print_json(posted)
        return EXIT_OK
    print(f"#{args.number}: комментарий добавлен — {posted.get('html_url', '')}")
    return EXIT_OK


def _cmd_create_issue(args: argparse.Namespace) -> int:
    """Завести issue."""
    created = create_issue(args.repo, title=args.title, body=_resolve_body(args), labels=args.label)
    if args.json:
        _print_json(created)
        return EXIT_OK
    print(f"issue #{created.get('number')} заведён: {created.get('html_url')}")
    return EXIT_OK


def _cmd_label(args: argparse.Namespace) -> int:
    """Проставить или снять метку."""
    if args.remove:
        removed = remove_label(args.repo, args.number, args.remove)
        print(f"#{args.number}: метка {args.remove} " + ("снята." if removed else "и не стояла."))
        return EXIT_OK
    labels = add_labels(args.repo, args.number, args.add)
    if args.json:
        _print_json(labels)
        return EXIT_OK
    print(f"#{args.number}: метки — {', '.join(labels)}")
    return EXIT_OK


def _cmd_comments(args: argparse.Namespace) -> int:
    """Показать комментарии issue или PR."""
    found = issue_comments(args.repo, args.number)
    if args.json:
        _print_json(found)
        return EXIT_OK
    for item in found:
        author = item.get("user", {}).get("login", "?")
        first = str(item.get("body", "")).strip().splitlines()[:1]
        print(f"  {item.get('created_at', '')} · {author}: {first[0] if first else ''}")
    if not found:
        print(f"#{args.number}: комментариев нет.")
    return EXIT_OK


def _cmd_runs(args: argparse.Namespace) -> int:
    """Прогоны CI по ветке."""
    found = branch_runs(args.repo, branch=args.branch, event=args.event, limit=args.limit)
    if args.json:
        _print_json(found)
        return EXIT_OK
    for run in found:
        state = run.get("conclusion") or run.get("status")
        print(f"  {run.get('id')} [{state}] {run.get('event')} · {run.get('run_started_at', '')}")
    if not found:
        print(f"прогонов ci.yml на {args.branch} нет.")
    return EXIT_OK


def _cmd_run_jobs(args: argparse.Namespace) -> int:
    """Job'ы прогона — видно, что именно упало."""
    jobs = run_jobs(args.repo, args.run)
    if args.json:
        _print_json(jobs)
        return EXIT_OK
    for job in jobs:
        state = job.get("conclusion") or job.get("status")
        print(f"  {job.get('id')} [{state}] {job.get('name')}")
    return EXIT_OK


def _cmd_cancel_run(args: argparse.Namespace) -> int:
    """Отменить зависший прогон — он держит очередь мержей."""
    if cancel_run(args.repo, args.run):
        print(f"прогон {args.run} отменён.")
        return EXIT_OK
    print(f"прогон {args.run} уже завершён — отменять нечего.")
    return EXIT_OK


def _cmd_rerun_failed(args: argparse.Namespace) -> int:
    """Перезапустить упавшие джобы — и записать, что именно мигнуло.

    Запись не «заодно», а условие: без неё команда превращается в удобную
    кнопку, и «перезапусти, оно иногда падает» становится нормальным ответом.
    Поэтому ``--why`` обязателен, а третья попытка отклоняется — она означает
    не мигание, а дефект.
    """
    attempt = int(getattr(args, "attempt", 1) or 1)
    if attempt > MAX_ATTEMPTS:
        print(
            f"попытка {attempt} — это уже не мигание, а дефект: "
            f"перезапуск больше {MAX_ATTEMPTS} раз ничего не доказывает, "
            "разбирайте причину падения",
            file=sys.stderr,
        )
        return EXIT_FAIL

    if not rerun_failed_jobs(args.repo, args.run):
        print(f"прогон {args.run}: упавших джобов нет — перезапускать нечего.")
        return EXIT_OK

    append_flake_note(args.run, args.why)
    print(f"прогон {args.run}: упавшие джобы перезапущены, прошедшие сохранены.")
    print(f"записано в журнал нестабильности ({FLAKE_LOG.name}): {args.why}")
    return EXIT_OK


def _cmd_rate(args: argparse.Namespace) -> int:
    """Показать остаток квоты — сам запрос её не расходует.

    Заодно это стоп-кран, пригодный как гейт: ресурс ниже порога — код
    возврата :data:`EXIT_WAIT`, то есть «ждать», а не «упало».
    """
    quotas = rate_limit()
    floor = quota_floor() if args.floor is None else args.floor
    low = [q for name, q in quotas.items() if name in ("core", "graphql") and q.remaining <= floor]
    if args.json:
        _print_json({name: dataclasses.asdict(quota) for name, quota in quotas.items()})
        return EXIT_WAIT if low else EXIT_OK
    for name in ("core", "graphql", "search"):
        quota = quotas.get(name)
        if quota is not None:
            print(quota.describe())
    if low:
        names = ", ".join(quota.resource for quota in low)
        print(
            f"Стоп-кран: {names} ниже порога {floor}. Новые операции не начинать — "
            "дождаться сброса; что при этом всё ещё доступно, описано в "
            "docs/agent/preflight.md § Маршрут при исчерпании лимитов.",
            file=sys.stderr,
        )
        return EXIT_WAIT
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    """Разбор аргументов: подкоманды конвейера."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo (по умолчанию проект)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    sub = parser.add_subparsers(dest="command", required=True)

    pulls = sub.add_parser("pulls", help="список PR")
    pulls.add_argument("--state", default="open", choices=["open", "closed", "all"])
    pulls.set_defaults(handler=_cmd_pulls)

    checks = sub.add_parser("checks", help="проверки PR и состояние main")
    checks.add_argument("pull", type=int)
    checks.set_defaults(handler=_cmd_checks)

    comparison = sub.add_parser("compare", help="отстала ли ветка PR от базовой")
    comparison.add_argument("pull", type=int)
    comparison.set_defaults(handler=_cmd_compare)

    create = sub.add_parser("create-pr", help="создать PR")
    create.add_argument("--title", required=True)
    create.add_argument("--head", required=True, help="ветка-источник")
    create.add_argument("--base", default="main")
    create.add_argument("--body", default="")
    create.add_argument("--body-file", help="тело из файла UTF-8; '-' — со стандартного ввода")
    create.add_argument("--label", action="append", default=[], help="метка (повторяемый)")
    create.add_argument("--draft", action="store_true")
    create.set_defaults(handler=_cmd_create_pr)

    edit = sub.add_parser("edit-pr", help="поправить заголовок или тело PR")
    edit.add_argument("pull", type=int)
    edit.add_argument("--title")
    edit.add_argument("--body", default="")
    edit.add_argument("--body-file", help="тело из файла UTF-8; '-' — со стандартного ввода")
    edit.set_defaults(handler=_cmd_edit_pr)

    update = sub.add_parser("update-branch", help="подтянуть base в ветку PR")
    update.add_argument("pull", type=int)
    update.set_defaults(handler=_cmd_update_branch)

    auto = sub.add_parser("auto-merge", help="включить авто-мерж: смержится сам по зелёному")
    auto.add_argument("pull", type=int)
    auto.add_argument("--method", default="squash", choices=["squash", "merge", "rebase"])
    auto.set_defaults(handler=_cmd_auto_merge)

    merge = sub.add_parser("merge", help="смержить PR")
    merge.add_argument("pull", type=int)
    merge.add_argument("--method", default="squash", choices=["squash", "merge", "rebase"])
    merge.set_defaults(handler=_cmd_merge)

    show_issue = sub.add_parser("issue", help="состояние issue, заголовок, метки")
    show_issue.add_argument("number", type=int)
    show_issue.set_defaults(handler=_cmd_issue)

    close = sub.add_parser("close-issue", help="закрыть issue с причиной")
    close.add_argument("number", type=int)
    close.add_argument("--reason", default="completed", choices=_CLOSE_REASONS)
    close.add_argument("--comment", help="комментарий перед закрытием: чем закончилось")
    close.set_defaults(handler=_cmd_close_issue)

    comment = sub.add_parser("comment", help="комментарий к issue или PR")
    comment.add_argument("number", type=int)
    comment.add_argument("text", nargs="?", help="текст комментария (синоним --body)")
    comment.add_argument("--body", default="", help="текст комментария")
    comment.add_argument("--body-file", help="тело из файла UTF-8; '-' — со стандартного ввода")
    comment.set_defaults(handler=_cmd_comment)

    new_issue = sub.add_parser("create-issue", help="завести issue")
    new_issue.add_argument("--title", required=True)
    new_issue.add_argument("--body", default="")
    new_issue.add_argument("--body-file", help="тело из файла UTF-8; '-' — со стандартного ввода")
    new_issue.add_argument("--label", action="append", default=[])
    new_issue.set_defaults(handler=_cmd_create_issue)

    label = sub.add_parser("label", help="проставить или снять метку")
    label.add_argument("number", type=int)
    label.add_argument("--add", action="append", default=[])
    label.add_argument("--remove")
    label.set_defaults(handler=_cmd_label)

    comments = sub.add_parser("comments", help="комментарии issue или PR")
    comments.add_argument("number", type=int)
    comments.set_defaults(handler=_cmd_comments)

    children = sub.add_parser("sub-issues", help="дочерние issue эпика")
    children.add_argument("number", type=int)
    children.set_defaults(handler=_cmd_sub_issues)

    adopt = sub.add_parser("add-sub-issue", help="подчинить issue эпику")
    adopt.add_argument("parent", type=int)
    adopt.add_argument("--child", type=int, required=True, help="номер дочернего issue")
    adopt.set_defaults(handler=_cmd_add_sub_issue)

    runs = sub.add_parser("runs", help="прогоны ci.yml по ветке")
    runs.add_argument("--branch", default="main")
    runs.add_argument("--event")
    runs.add_argument("--limit", type=int, default=10)
    runs.set_defaults(handler=_cmd_runs)

    jobs = sub.add_parser("run-jobs", help="job'ы прогона: что именно упало")
    jobs.add_argument("run", type=int)
    jobs.set_defaults(handler=_cmd_run_jobs)

    cancel = sub.add_parser("cancel-run", help="отменить зависший прогон")
    cancel.add_argument("run", type=int)
    cancel.set_defaults(handler=_cmd_cancel_run)

    rerun = sub.add_parser(
        "rerun-failed",
        help="перезапустить ТОЛЬКО упавшие джобы прогона (с записью в журнал)",
    )
    rerun.add_argument("run", type=int)
    rerun.add_argument(
        "--why",
        required=True,
        help="что именно мигнуло: строка уходит в журнал нестабильности",
    )
    rerun.add_argument(
        "--attempt",
        type=int,
        default=1,
        help=f"номер попытки; больше {MAX_ATTEMPTS} — это дефект, а не мигание",
    )
    rerun.set_defaults(handler=_cmd_rerun_failed)

    queue = sub.add_parser("queue", help="очередь мержа: кого обновлять, кто ждёт")
    queue.set_defaults(handler=_cmd_queue)

    rate = sub.add_parser("rate", help="остаток квоты (запрос её не тратит)")
    rate.add_argument(
        "--floor", type=int, help=f"порог стоп-крана (по умолчанию {DEFAULT_QUOTA_FLOOR})"
    )
    rate.set_defaults(handler=_cmd_rate)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Выполнить подкоманду; 0 — успех, 1 — ошибка, 2 — ждать сброса квоты."""
    _force_utf8_stdio()
    args = _build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except RateLimited as exc:
        print(exc.describe(), file=sys.stderr)
        return EXIT_WAIT
    except MissingToken as exc:
        print(f"Не могу обратиться к GitHub: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except TlsVerificationError as exc:
        # Без префикса «Ошибка GitHub»: до GitHub запрос не дошёл, а текст уже
        # называет и причину, и способ починки.
        print(str(exc), file=sys.stderr)
        return EXIT_FAIL
    except GitHubError as exc:
        print(f"Ошибка GitHub: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
