"""runs.py — async job-модель для bench/microbench (issue #262).

Архитектурный слой: Application/UI (web-адаптер), как и ``viewmodels.py``.
Синхронный ``/api/grade`` держит HTTP-запрос открытым на всю длительность
бенчмарка (минуты) без прогресса и без возможности отмены — этот модуль
даёт асинхронную альтернативу: ``POST /api/v1/runs`` ставит job в очередь и
сразу возвращает ``run_id``, ``GET /api/v1/runs/{id}`` — опрос статуса и
прогресса, ``POST /api/v1/runs/{id}/cancel`` — best-effort отмена.

MVP без новых зависимостей: реестр job'ов — module-level ``dict`` под
``threading.Lock``, воркер-пул — ``concurrent.futures.ThreadPoolExecutor``
(размер — ``CONFIG.job_workers``, не CLI-флаг). TTL завершённых job'ов —
``_JOB_TTL_SECONDS``; уборка ленивая, на каждом обращении к реестру
(``submit_job``/``get_job``) — отдельного фонового потока для этого нет,
как и для ``glossary_missing_queue`` (тот же паттерн best-effort).

``web/server.py`` — единственный вызывающий: конфайнмент путей (issue #261)
и валидация входных данных (issue #259) — его забота, этот модуль
агностичен к политике (как и ``viewmodels.py``), принимает уже
провалидированный/сконфайненный ``path``.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core.test_loader import find_all_solution_files
from stepik_grader.core.tracer import trace_code
from stepik_grader.web.i18n import DEFAULT_LANG, message_fields
from stepik_grader.web.playground import run_playground
from stepik_grader.web.viewmodels import (
    estimate_run_count,
    grade_benchmark,
    grade_microbench,
    grade_path,
)

__all__ = ["Job", "submit_job", "get_job", "cancel_job"]

# issue #262 добавил ровно 4 статуса ("cancelled" сообщался как status="error"
# + message_id="run_cancelled"); issue #296 выделяет отмену в отдельный
# терминальный статус — семантически это не провал решения/грейдера
# (клиентам server mode/будущего API нужно отличать "пользователь отменил"
# от "грейдер упал": ретраить имеет смысл только второе, UI должен подавать
# их по-разному — см. static/app.js). message_id="run_cancelled" по-прежнему
# заполняется в message_fields (см. _run_job()) — не только status.
_STATUSES = ("queued", "running", "done", "error", "cancelled")

_JOB_TTL_SECONDS = 15 * 60


class Job:
    """Одна async job-задача. Мутируется на воркер-потоке executor'а под
    ``self.lock``; читается HTTP-хендлерами (другой поток) через тот же
    lock (``to_status_dict()``). Регистрируется в module-level ``_JOBS`` под
    отдельным ``_JOBS_LOCK`` — тот охраняет только членство в словаре
    (вставку/поиск/уборку по TTL), не поля самой ``Job``.
    """

    def __init__(self, job_id: str, kind: str) -> None:
        self.id = job_id
        self.kind = kind  # "bench" | "microbench" — для диагностики
        self.status: str = "queued"
        self.created_at = time.monotonic()
        self.progress: dict[str, int] = {"done": 0, "total": 0}
        self.result: dict[str, Any] | None = None
        self.message_fields: dict[str, Any] | None = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.future: Future[None] | None = None

    def to_status_dict(self) -> dict[str, Any]:
        """Форма ответа ``GET /api/v1/runs/{id}`` — ``{"status","progress",
        "result"}`` плюс ``message``/``message_id``/``message_params`` при
        ошибке/отмене (issue #264, тот же каталог сообщений, что у
        остального ``/api/*``)."""
        with self.lock:
            data: dict[str, Any] = {
                "status": self.status,
                "progress": dict(self.progress),
                "result": self.result,
            }
            if self.message_fields is not None:
                data.update(self.message_fields)
            return data


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Ленивый singleton (как ``config.get_config()``) — не создаёт пул
    потоков при простом ``import``, только при первой реальной job'е, и не
    читает ``CONFIG`` (а значит и ``pyproject.toml``) раньше необходимого."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=max(1, CONFIG.job_workers))
    return _executor


def _sweep_expired_locked() -> None:
    """Удалить завершённые job'ы старше TTL. Вызывать только под
    ``_JOBS_LOCK`` — иначе гонка на удаление/итерацию словаря."""
    now = time.monotonic()
    expired = [
        job_id
        for job_id, job in _JOBS.items()
        if job.status in ("done", "error", "cancelled") and now - job.created_at > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del _JOBS[job_id]


def submit_job(
    kind: str,
    path: pathlib.Path | None,
    params: dict[str, Any],
    *,
    code: str | None = None,
    stdin: str | None = None,
) -> Job:
    """Поставить tests/bench/microbench в очередь async job'ов (issue #262/#297).

    ``path`` — уже сконфайненный, резолвленный абсолютный путь (конфайнмент —
    забота ``server.py``); обязателен, даже если задан ``code`` — используется
    для резолюции ``test_dir`` (``core.test_loader.resolve_test_dir`` ищет
    ``tests/`` рядом с файлом/папкой, у произвольного временного файла своей
    ``tests/`` нет).

    ``code`` (доп. к телу запроса ``{path|code,...}`` из issue) — если задан,
    исполняемое содержимое ЗАМЕНЯЕТ то, что лежит по ``path`` на диске (как
    редактируемое окно кода режима 1 без записи на диск, тот же сценарий, что
    у ``POST /api/save-solution``): пишется во временный ``.py`` рядом с
    ``path`` (та же родительская папка — иначе ``resolve_test_dir()`` не
    найдёт ``tests/``), удаляется после завершения job'ы. Только режим
    одного файла — директорийное сравнение решений с ``code`` не имеет
    смысла (один код — одно решение).

    ``kind="tests"`` (issue #297) — грейд корректности режима 1 через ту же
    async-очередь с ``code`` в теле: «Проверить» больше не пишет в целевой
    файл (не гонится с параллельным окном на той же папке), исполняет из
    временного файла. ``kind="bench"``/``"microbench"`` (issue #262) — без
    изменений.

    ``kind="playground"`` (issue #317) — раздел «Песочница»: одиночный запуск
    ``code`` со ``stdin`` без тестов; ``path`` не нужен (``None``), результат
    — ``{status, stdout, stderr, ...}`` от ``run_playground``. Асинхронно —
    ради отмены зависшего прогона (``while True: pass``) и неблокирующего UI.

    ``params`` — ``repeats``/``reference``/``number``/``lang``, уже
    провалидированные/кламп'нутые вызывающей стороной (``server.py``),
    прокидываются в ``grade_path``/``grade_benchmark``/``grade_microbench``
    как есть (для ``tests`` значим только ``lang``).
    """
    job = Job(uuid.uuid4().hex, kind)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
        _sweep_expired_locked()
    job.future = _get_executor().submit(_run_job, job, kind, path, params, code, stdin)
    return job


def get_job(run_id: str) -> Job | None:
    """Найти job по id; ``None``, если не существует (или уже сметена TTL)."""
    with _JOBS_LOCK:
        _sweep_expired_locked()
        return _JOBS.get(run_id)


def cancel_job(run_id: str) -> bool:
    """Best-effort отмена (issue #262) — выставляет ``cancel_event`` и
    возвращает немедленно, не дожидаясь, пока воркер-поток заметит сигнал
    (реальная остановка дочернего процесса происходит асинхронно, через
    ``LocalRunner``-поллинг — см. ``core/runner.py``). ``False``, если job
    не найдена или уже терминальна (``done``/``error``/``cancelled`` —
    нечего отменять; повторный ``cancel`` уже отменённой job'ы тоже ``False``).
    """
    job = get_job(run_id)
    if job is None:
        return False
    with job.lock:
        if job.status in ("done", "error", "cancelled"):
            return False
        job.cancel_event.set()
    return True


def _run_job(
    job: Job,
    kind: str,
    path: pathlib.Path | None,
    params: dict[str, Any],
    code: str | None,
    stdin: str | None = None,
) -> None:
    """Тело job'ы — выполняется на потоке ``ThreadPoolExecutor`` (issue #262)."""
    with job.lock:
        job.status = "running"

    lang = params.get("lang", DEFAULT_LANG)

    if kind == "playground":
        _run_playground_job(job, code or "", stdin or "", lang)
        return
    if kind == "trace":
        _run_trace_job(job, code or "", stdin or "", lang)
        return
    if kind == "auth":
        _run_auth_job(job, params, lang)
        return

    assert path is not None  # tests/bench/microbench всегда с path (см. submit_job)
    temp_code_path: str | None = None
    try:
        graded_path = path
        if code is not None:
            parent = path if path.is_dir() else path.parent
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", encoding="utf-8", delete=False, dir=parent
            )
            try:
                tmp.write(code)
            finally:
                tmp.close()
            temp_code_path = tmp.name
            graded_path = pathlib.Path(temp_code_path)

        solutions = [graded_path] if graded_path.is_file() else find_all_solution_files(graded_path)
        total = estimate_run_count(solutions, kind=kind, repeats=int(params.get("repeats", 1)))
        with job.lock:
            job.progress["total"] = total

        done_counter = 0

        def _tick(n: int) -> None:
            nonlocal done_counter
            done_counter += n
            with job.lock:
                job.progress["done"] = done_counter

        if kind == "tests":
            result = grade_path(
                graded_path,
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
            )
        elif kind == "bench":
            result = grade_benchmark(
                graded_path,
                repeats=int(params.get("repeats", 15)),
                reference=params.get("reference"),
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
            )
        else:  # "microbench"
            result = grade_microbench(
                graded_path,
                number=int(params.get("number", 1000)),
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
            )

        with job.lock:
            if job.cancel_event.is_set():
                # issue #296: отдельный терминальный статус, не "error" —
                # отмена пользователем не провал грейдера/решения (клиент не
                # должен ретраить "error", но обязан не ретраить "cancelled").
                job.status = "cancelled"
                job.message_fields = message_fields("run_cancelled", lang)
            else:
                job.status = "done"
                job.result = result
    except Exception as exc:  # noqa: BLE001 — safety net (issue #262): a bug in
        # this worker thread must never leave the job stuck "running" forever
        # with no way for the poller to find out; surfaced via message_fields
        # the same way any other /api/* error is, not via logging (no
        # centralized web-layer logging exists yet — issue #150/#147-149).
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
    finally:
        if temp_code_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_code_path)


def _run_playground_job(job: Job, code: str, stdin: str, lang: str) -> None:
    """Тело playground-job'ы (issue #317): один запуск ``code`` со ``stdin``.

    ``run_playground`` уже создаёт/удаляет свой временный файл и уважает
    ``cancel_event``; здесь только маппинг исхода в статус job'ы (отмена —
    отдельный терминальный ``cancelled``, как у грейд-job'ов).
    """
    try:
        result = run_playground(code, stdin, cancel_event=job.cancel_event)
    except Exception as exc:  # noqa: BLE001 — safety net, как в _run_job
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
        return
    with job.lock:
        if result.get("status") == "CANCELLED" or job.cancel_event.is_set():
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
        else:
            job.status = "done"
            job.result = result


def _run_auth_job(job: Job, params: dict[str, Any], lang: str) -> None:
    """Тело auth-job'ы (issue #402): браузерный OAuth-flow первого запуска.

    Блокирующий (до 120с) поход в браузер вынесен на воркер-поток, чтобы не
    держать HTTP-обработчик ``--serve``. Креды/путь приходят в ``params`` от
    ``server._handle_auth_start`` (уже под ``_guard_request``). Ленивый импорт
    ``auth_adapter`` держит ``runs.py`` импортируемым в изоляции (тесты) без
    OAuth/requests-стека; в самом ``--serve`` он всё равно грузится через
    ``server.py`` (верхнеуровневый импорт).
    """
    from stepik_grader.web import auth_adapter

    try:
        result = auth_adapter.perform_browser_auth(
            pathlib.Path(str(params["secrets_path"])),
            str(params["client_id"]),
            str(params["client_secret"]),
            str(params["redirect_uri"]),
        )
    except Exception as exc:  # noqa: BLE001 — safety net, как _run_job/_run_playground_job
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
        return
    with job.lock:
        job.status = "done"
        job.result = result


def _run_trace_job(job: Job, code: str, stdin: str, lang: str) -> None:
    """Тело trace-job'ы (issue #318): пошаговый трейс исполнения ``code``.

    ``trace_code`` спавнит свой subprocess (``python -m …core.tracer``) и сам
    его убивает по таймауту — ``cancel_event`` этот путь пока не прерывает
    (трассировка ограничена ``max_steps`` + таймаутом; отмена — best-effort
    через таймаут). Результат job'ы — JSON-трейс ``{steps, stdout, …}``.
    """
    try:
        result = trace_code(code, stdin, timeout=float(CONFIG.timeout_seconds))
    except Exception as exc:  # noqa: BLE001 — safety net, как в _run_job
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
        return
    with job.lock:
        # issue #422: если пользователь отменил во время трассировки, финализируем
        # как cancelled, а не done — как _run_job/_run_playground_job. trace_code
        # cancel_event не прерывает (best-effort через таймаут), но статус обязан
        # отразить запрошенную отмену.
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
        else:
            job.status = "done"
            job.result = result
