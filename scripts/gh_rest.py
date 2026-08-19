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
   бессмысленно: счётчик растёт и после нуля.
4. **Условный запрос не расходует лимит вовсе.** Ответ ``304 Not Modified`` на
   ``If-None-Match`` GitHub не засчитывает, поэтому GET'ы носят ``ETag`` в
   кэше между запусками процесса.

Чего здесь **нет** и не будет: покрытия всего GitHub API (только операции
конвейера) и обхода лимитов вторым токеном. Auto-merge остаётся за MCP —
``enablePullRequestAutoMerge`` существует только в GraphQL, REST-эквивалента у
него нет; это ровно тот случай «чего нет в REST», ради которого исключение и
оговорено.

Запуск::

    python scripts/gh_rest.py pulls                  # открытые PR
    python scripts/gh_rest.py checks 1242            # проверки PR и состояние main
    python scripts/gh_rest.py compare 1242           # отставание ветки от базовой
    python scripts/gh_rest.py create-pr --title T --head branch --body B
    python scripts/gh_rest.py update-branch 1242     # подтянуть main в ветку PR
    python scripts/gh_rest.py merge 1242             # смержить (squash)
    python scripts/gh_rest.py rate                   # остаток квоты; сам её не тратит

Код возврата 0 — успех; 1 — ошибка; 2 — квота исчерпана, надо ждать сброса.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

__all__ = [
    "API",
    "DEFAULT_REPO",
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_WAIT",
    "Divergence",
    "GitHubError",
    "MissingToken",
    "PullSummary",
    "Quota",
    "RateLimited",
    "Response",
    "compare",
    "create_pull",
    "list_pulls",
    "main",
    "main_run",
    "merge_pull",
    "pull",
    "pull_checks",
    "rate_limit",
    "request",
    "resolve_token",
    "update_branch",
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

Opener = Callable[[urllib.request.Request], Any]


class GitHubError(RuntimeError):
    """GitHub ответил не так, как ожидалось (кроме исчерпанной квоты)."""


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


def _quota_from_headers(headers: Any) -> tuple[int, int, str]:
    """Из заголовков ответа — ``(remaining, reset, resource)``."""

    def _int(name: str) -> int:
        try:
            return int(headers.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return (
        _int("x-ratelimit-remaining"),
        _int("x-ratelimit-reset"),
        str(headers.get("x-ratelimit-resource") or "core"),
    )


def _raise_for_error(exc: urllib.error.HTTPError, path: str) -> None:
    """Перевести HTTP-ошибку в осмысленное исключение модуля."""
    remaining, reset, resource = _quota_from_headers(exc.headers)
    retry_after = exc.headers.get("retry-after") if exc.headers else None
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
        raise GitHubError(
            f"GitHub отказал ({exc.code}) на {path}: токен недействителен или без нужных прав"
        ) from exc
    detail = ""
    try:
        body = json.loads(exc.read().decode("utf-8"))
        detail = str(body.get("message", ""))
    except (OSError, ValueError, AttributeError):
        detail = ""
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


def _default_opener(req: urllib.request.Request) -> Any:
    """Реальный ``urlopen`` с таймаутом — подменяется в тестах."""
    return urllib.request.urlopen(req, timeout=_TIMEOUT)


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
        )
        for item in items
        if isinstance(item, dict)
    ]


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

    Отдельный дешёвый запрос: фильтр по workflow, ветке и событию делает сам
    GitHub, поэтому одна страница отвечает и на «занята ли очередь», и на
    «не красная ли ``main``».
    """
    data = _get(
        f"repos/{repo}/actions/workflows/{_CI_WORKFLOW}/runs?branch=main&event=push&per_page=10",
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
    """Создать PR. Возвращает ответ GitHub (с ``number`` и ``html_url``)."""
    data = request(
        "POST",
        f"repos/{repo}/pulls",
        body={"title": title, "head": head, "base": base, "body": body, "draft": draft},
        **kwargs,
    ).data
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


def rate_limit(**kwargs: Any) -> dict[str, Quota]:
    """Остаток квоты по ресурсам. Сам запрос лимит не расходует."""
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


def summarize_checks(check_runs: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Сводка по check-runs: ``(всего, завершено, красные имена)``."""
    listed = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    runs = [item for item in listed if isinstance(item, dict)]
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
    """Создать PR из ветки."""
    created = create_pull(
        args.repo,
        title=args.title,
        head=args.head,
        base=args.base,
        body=args.body,
        draft=args.draft,
    )
    if args.json:
        _print_json(created)
        return EXIT_OK
    print(f"PR #{created.get('number')} создан: {created.get('html_url')}")
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


def _cmd_rate(args: argparse.Namespace) -> int:
    """Показать остаток квоты — сам запрос её не расходует."""
    quotas = rate_limit()
    if args.json:
        _print_json({name: dataclasses.asdict(quota) for name, quota in quotas.items()})
        return EXIT_OK
    for name in ("core", "graphql", "search"):
        quota = quotas.get(name)
        if quota is not None:
            print(quota.describe())
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
    create.add_argument("--draft", action="store_true")
    create.set_defaults(handler=_cmd_create_pr)

    update = sub.add_parser("update-branch", help="подтянуть base в ветку PR")
    update.add_argument("pull", type=int)
    update.set_defaults(handler=_cmd_update_branch)

    merge = sub.add_parser("merge", help="смержить PR")
    merge.add_argument("pull", type=int)
    merge.add_argument("--method", default="squash", choices=["squash", "merge", "rebase"])
    merge.set_defaults(handler=_cmd_merge)

    rate = sub.add_parser("rate", help="остаток квоты (запрос её не тратит)")
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
    except GitHubError as exc:
        print(f"Ошибка GitHub: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
