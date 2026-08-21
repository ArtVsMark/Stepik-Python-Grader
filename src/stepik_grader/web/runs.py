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

import pathlib
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core.ai_hints import explain_failure_detailed, is_configured
from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.failure_context import build_failure_context
from stepik_grader.web.grading import find_all_solution_files, trace_code
from stepik_grader.web.i18n import DEFAULT_LANG, message_fields
from stepik_grader.web.playground import run_playground
from stepik_grader.web.viewmodels import (
    estimate_run_count,
    grade_benchmark,
    grade_microbench,
    grade_path,
    history_db_path_if_enabled,
)

__all__ = ["Job", "TooManyRunsError", "cancel_job", "get_job", "shutdown_jobs", "submit_job"]

# issue #831 (DEV-12): диагностический логгер web-слоя. До него падение
# job'а оставляло в логе только строки запросов, а само место сбоя нигде не
# фиксировалось — `core/feedback` собирал для баг-репорта пустоту.
_log = get_logger("web")

# issue #262 добавил ровно 4 статуса ("cancelled" сообщался как status="error"
# + message_id="run_cancelled"); issue #296 выделяет отмену в отдельный
# терминальный статус — семантически это не провал решения/грейдера
# (клиентам server mode/будущего API нужно отличать "пользователь отменил"
# от "грейдер упал": ретраить имеет смысл только второе, UI должен подавать
# их по-разному — см. static/app.js). message_id="run_cancelled" по-прежнему
# заполняется в message_fields (см. _run_job()) — не только status.
_STATUSES = ("queued", "running", "done", "error", "cancelled")
# Терминальные статусы (issue #408): при переходе в любой из них Job штампует
# completed_at, и TTL-уборка (_sweep_expired_locked) меряется ОТ НЕГО, а не от
# постановки в очередь — иначе долгий bench выметается сразу после финиша.
_TERMINAL_STATUSES = ("done", "error", "cancelled")

_JOB_TTL_SECONDS = 15 * 60
# issue #811 (SECW-03): жёсткий потолок числа записей реестра. TTL один его не
# держит: терминальные job'ы живут 15 минут, а back-pressure их не считает,
# поэтому цикл коротких прогонов растил словарь без предела. Потолок с запасом
# выше ``max_active_runs`` (20) — история недавних результатов остаётся
# доступной UI, но не растёт бесконечно.
_JOBS_MAX_ENTRIES = 200


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
        self._status: str = "queued"
        self.created_at = time.monotonic()
        # issue #408: момент перехода в терминальный статус (None пока job не
        # завершилась) — от него меряется TTL-уборка, см. status.setter ниже.
        self.completed_at: float | None = None
        self.progress: dict[str, int] = {"done": 0, "total": 0}
        self.result: dict[str, Any] | None = None
        self.message_fields: dict[str, Any] | None = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.future: Future[None] | None = None

    @property
    def status(self) -> str:
        """Текущий статус job'ы (``queued``/``running``/``done``/``error``/``cancelled``)."""
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        # issue #408: штампуем completed_at при ПЕРВОМ переходе в терминал —
        # ДО присвоения _status, чтобы sweeper (читает без self.lock, под
        # _JOBS_LOCK), увидев терминальный статус, гарантированно видел и время.
        if value in _TERMINAL_STATUSES and self.completed_at is None:
            self.completed_at = time.monotonic()
        self._status = value

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

# issue #971: авторизация живёт в СВОЁМ пуле. Она блокирует поток до двух минут
# (ждёт, пока человек нажмёт кнопку в браузере), а пул проверок по умолчанию на
# два воркера — две таких job'ы занимали его целиком, и «Проверить» переставала
# отвечать, хотя грейдер простаивал. Один воркер: параллельных авторизаций не
# бывает по смыслу — браузер и callback-порт всё равно одни.
_auth_executor: ThreadPoolExecutor | None = None
_auth_executor_lock = threading.Lock()


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


def _get_auth_executor() -> ThreadPoolExecutor:
    """Пул под блокирующую авторизацию — отдельный от пула проверок (issue #971)."""
    global _auth_executor
    if _auth_executor is None:
        with _auth_executor_lock:
            if _auth_executor is None:
                _auth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grader-auth")
    return _auth_executor


def shutdown_jobs(*, wait: bool = False) -> None:
    """Погасить активные прогоны и остановить пул воркеров (issue #806).

    Вызывается при остановке сервера (``serve()`` в ``finally``). Без этого
    Ctrl+C печатал «сервер остановлен», а процесс продолжал висеть: воркеры
    ``ThreadPoolExecutor`` не daemon, и atexit-хук ``concurrent.futures``
    join'ит их до конца текущего прогона (замерено: ~8 с на одном прогоне до
    таймаута, дольше — на bench с ``repeats``).

    Сначала взводится ``cancel_event`` каждой нетерминальной job'ы — тот же
    best-effort механизм, что у ``cancel_job``: дочерний процесс убивает
    поллинг раннера, и воркер освобождается почти сразу. Затем пул закрывается
    с ``cancel_futures=True`` — очередь ещё не начатых job'ов не запускается.
    ``wait=False`` по умолчанию: мы гасим прогоны, а не дожидаемся их.
    """
    global _executor
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    for job in jobs:
        with job.lock:
            if job.status not in _TERMINAL_STATUSES:
                job.cancel_event.set()
    with _executor_lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)
    # issue #971: второй пул гасится тем же заходом — иначе Ctrl+C оставлял бы
    # висеть поток авторизации, ради которого и заводился shutdown_jobs.
    global _auth_executor
    with _auth_executor_lock:
        auth_executor, _auth_executor = _auth_executor, None
    if auth_executor is not None:
        auth_executor.shutdown(wait=wait, cancel_futures=True)


def _evict_overflow_locked() -> None:
    """Держать реестр в пределах ``_JOBS_MAX_ENTRIES``, вытесняя старые терминальные.

    issue #811 (SECW-03): back-pressure считает только НЕтерминальные job'ы,
    поэтому цикл коротких прогонов лимит не трогал вовсе — замерено прогоном:
    30 последовательных playground-запусков оставили 21 запись в реестре при
    нуле активных. Каждая запись держит результат прогона (до 100 000 символов
    вывода) целых 15 минут TTL, и одна вкладка со скриптом раздувала память
    процесса — ровно тот цикл POST'ов, ради которого вводился #429.

    Вытесняем самые давно завершённые: свежие результаты ещё могут быть
    запрошены UI, старые уже никому не нужны. Активные не трогаем никогда —
    их удаление осиротило бы работающий воркер. Вызывать под ``_JOBS_LOCK``.
    """
    overflow = len(_JOBS) - _JOBS_MAX_ENTRIES
    if overflow <= 0:
        return
    finished = sorted(
        (job for job in _JOBS.values() if job.completed_at is not None),
        key=lambda job: job.completed_at or 0.0,
    )
    for job in finished[:overflow]:
        del _JOBS[job.id]


def _sweep_expired_locked() -> None:
    """Удалить завершённые job'ы старше TTL. Вызывать только под
    ``_JOBS_LOCK`` — иначе гонка на удаление/итерацию словаря."""
    now = time.monotonic()
    # issue #408: TTL от completed_at (момент финиша), не от created_at.
    # completed_at выставлен ⟺ job в терминальном статусе (Job.status.setter),
    # поэтому отдельная проверка статуса больше не нужна.
    expired = [
        job_id
        for job_id, job in _JOBS.items()
        if job.completed_at is not None and now - job.completed_at > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del _JOBS[job_id]


class TooManyRunsError(RuntimeError):
    """Достигнут лимит одновременных нетерминальных job'ов (issue #429).

    Несётся из :func:`submit_job` при превышении ``CONFIG.max_active_runs``;
    ``server.py`` ловит и отвечает ``429`` с ``too_many_runs``. ``limit`` —
    действующий лимит (для сообщения пользователю).
    """

    def __init__(self, limit: int) -> None:
        super().__init__(f"too many active runs (limit {limit})")
        self.limit = limit


def _count_active_locked() -> int:
    """Число нетерминальных (``queued``/``running``) job'ов в реестре.

    Вызывать под ``_JOBS_LOCK``. Чтение ``job.status`` без ``job.lock`` — тот же
    грязно-читающий приём, что и ``_sweep_expired_locked`` (атрибут-строка
    читается атомарно; off-by-one на границе running↔done безвреден для
    эвристического лимита back-pressure)."""
    return sum(1 for j in _JOBS.values() if j.status in ("queued", "running"))


def submit_job(
    kind: str,
    path: pathlib.Path | None,
    params: dict[str, Any],
    *,
    code: str | None = None,
    stdin: str | None = None,
    workspace: pathlib.Path | None = None,
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

    ``workspace`` — корень сервера (``server.workspace`` / ``--root``);
    прокидывается в grade-функции ради стабильного ``task_key`` истории,
    инвариантного к cwd процесса-воркера (issue #539).
    """
    job = Job(uuid.uuid4().hex, kind)
    with _JOBS_LOCK:
        _sweep_expired_locked()
        # issue #429 — back-pressure: сметаем истёкшие терминальные job'ы, затем
        # отказываем, если активных (нетерминальных) уже не меньше лимита. Иначе
        # цикл POST'ов растит реестр/очередь executor'а без отказа (TTL чистит
        # только терминальные, каждый результат живёт 15 мин).
        if _count_active_locked() >= max(1, CONFIG.max_active_runs):
            raise TooManyRunsError(CONFIG.max_active_runs)
        # issue #971: вторая авторизация не ставится в очередь, а переиспользует
        # текущую. Параллельных браузерных flow не бывает: браузер и
        # callback-порт одни, вторая попытка упёрлась бы в занятый порт. Отдать
        # уже идущую job'у честнее отказа — UI получает тот же run_id и
        # продолжает следить за тем же процессом.
        if kind == "auth":
            running = _active_auth_job_locked()
            if running is not None:
                return running
        _JOBS[job.id] = job
        # issue #811: потолок реестра — ПОСЛЕ вставки, иначе он держался бы с
        # точностью до одной записи (вытеснили до предела, тут же добавили).
        # Только что вставленная job не терминальна, поэтому себя не вытеснит.
        _evict_overflow_locked()
    # issue #971: авторизация уходит в свой пул — блокирующий OAuth не должен
    # занимать воркеры, которыми считаются решения.
    pool = _get_auth_executor() if kind == "auth" else _get_executor()
    job.future = pool.submit(_run_job, job, kind, path, params, code, stdin, workspace)
    return job


def _active_auth_job_locked() -> Job | None:
    """Идущая auth-job'а, если она есть (issue #971). Вызывать под ``_JOBS_LOCK``."""
    for existing in _JOBS.values():
        if existing.kind == "auth" and existing.status not in _TERMINAL_STATUSES:
            return existing
    return None


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


def _robust_unlink(path: pathlib.Path, *, attempts: int = 5, delay: float = 0.05) -> None:
    """Удалить файл, терпя транзиентную блокировку на Windows (issue #605).

    Только что вышедший субпроцесс может ещё миг держать хэндл temp-файла
    (антивирус/индексатор/задержка релиза), и на Windows ``unlink`` тогда кидает
    ``PermissionError`` (⊂ ``OSError``). Ретраим несколько раз с нарастающей
    паузой, затем тихо сдаёмся — best-effort, как и прежний ``suppress(OSError)``:
    утёкший temp не должен ронять job.
    """
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                return
            time.sleep(delay * (attempt + 1))


def _run_job(
    job: Job,
    kind: str,
    path: pathlib.Path | None,
    params: dict[str, Any],
    code: str | None,
    stdin: str | None = None,
    workspace: pathlib.Path | None = None,
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
    if kind == "hint":
        _run_hint_job(job, code or "", params, lang)
        return
    if kind == "stepik_submit":
        _run_stepik_submit_job(job, code or "", params, lang)
        return

    assert path is not None  # tests/bench/microbench всегда с path (см. submit_job)
    temp_code_path: str | None = None
    result: dict[str, Any] | None = None
    error: Exception | None = None
    try:
        graded_path = path
        if code is not None:
            parent = path if path.is_dir() else path.parent
            # delete=False намеренно: путь файла грейдится ниже, чистится в
            # finally — ДО публикации терминального статуса (issue #605).
            tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w", suffix=".py", encoding="utf-8", delete=False, dir=parent
            )
            # issue #831 (DEV-09): путь запоминается СРАЗУ, до записи. Файл уже
            # существует на диске (``delete=False``), и сбой ``write``/``close``
            # (диск полон, сетевой диск отвалился) иначе уходил в ``except`` с
            # ``temp_code_path is None`` — уборка в ``finally`` не срабатывала, и
            # ``tmpXXXXXX.py`` оставался в папке задачи пользователя.
            temp_code_path = tmp.name
            try:
                tmp.write(code)
            finally:
                tmp.close()
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

        # issue #641: per-run лимиты из тела запроса (parse/clamp — в server.py);
        # None → grade-слой падает на дефолт CONFIG. microbench лимиты не берёт
        # (run_microbench_mode держит серверные дефолты) — осознанно вне scope.
        timeout_s = params.get("timeout_s")
        memory_mb = params.get("memory_mb")
        if kind == "tests":
            result = grade_path(
                graded_path,
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
                workspace=workspace,
                timeout=timeout_s,
                max_memory_mb=memory_mb,
                # issue #1211: подписывать результат тем, что выбрал человек, а
                # не временным файлом, который тут исполняется. Без кода в теле
                # грейдится сам `path` — подменять нечего.
                display_path=path if temp_code_path is not None else None,
            )
        elif kind == "bench":
            result = grade_benchmark(
                graded_path,
                repeats=int(params.get("repeats", 15)),
                reference=params.get("reference"),
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
                workspace=workspace,
                timeout=timeout_s,
                max_memory_mb=memory_mb,
            )
        else:  # "microbench"
            result = grade_microbench(
                graded_path,
                number=int(params.get("number", 1000)),
                lang=lang,
                progress_callback=_tick,
                cancel_event=job.cancel_event,
                workspace=workspace,
            )
    except Exception as exc:
        # Рабочий поток не имеет права оставить job навсегда в "running" — ошибка
        # доезжает до поллера через message_fields, как любая другая ошибка /api/*.
        # issue #831 (DEV-12): плюс стек в диагностический лог (opt-in,
        # с редакцией секретов) — иначе от падения прогона остаётся одна строка
        # "внутренняя ошибка", и в баг-репорт нечего приложить.
        _log.exception("сбой job %s (kind=%s, path=%s)", job.id, kind, path)
        error = exc
    finally:
        # issue #605: чистим temp ДО публикации терминального статуса, чтобы
        # поллер, увидев done/error/cancelled, никогда не застал висящий temp
        # (раньше unlink шёл после job.status="done" — гонка видимости). А сам
        # unlink терпит транзиентную блокировку файла на Windows (субпроцесс мог
        # ещё держать хэндл сразу после выхода) — bounded-retry, не one-shot.
        if temp_code_path is not None:
            _robust_unlink(pathlib.Path(temp_code_path))

    # Терминальный статус публикуется ПОСЛЕ зачистки temp — инвариант "job
    # терминален ⇒ temp уже удалён" (issue #605).
    with job.lock:
        if error is not None:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(error))
        elif job.cancel_event.is_set():
            # issue #296: отдельный терминальный статус, не "error" — отмена
            # пользователем не провал грейдера/решения (клиент не должен ретраить
            # "error", но обязан не ретраить "cancelled").
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
        else:
            job.status = "done"
            job.result = result


def _run_playground_job(job: Job, code: str, stdin: str, lang: str) -> None:
    """Тело playground-job'ы (issue #317): один запуск ``code`` со ``stdin``.

    ``run_playground`` уже создаёт/удаляет свой временный файл и уважает
    ``cancel_event``; здесь только маппинг исхода в статус job'ы (отмена —
    отдельный терминальный ``cancelled``, как у грейд-job'ов).
    """
    try:
        result = run_playground(code, stdin, cancel_event=job.cancel_event)
    except Exception as exc:
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


def _run_hint_job(job: Job, code: str, params: dict[str, Any], lang: str) -> None:
    """Тело hint-job'ы (issue #543): AI-объяснение упавшего кейса.

    Контекст строится общим core-хелпером ``build_failure_context`` (issue #542)
    из ``params["case"]`` + кода решения (заземление промпта); ``explain_failure``
    глушит любые ошибки канала в ``None`` — UI/грейдинг не падают. Согласие
    (consent) проверено СИНХРОННО на входе (``server._handle_create_hint``) — сюда
    без согласия не доходит, в сеть без него ничего не уходит. Результат —
    ``{"hint": str|None, "configured": bool}`` (``hint=None`` при не настроенном
    провайдере или пустом ответе — graceful skip).
    """
    # issue #797: отмена, пришедшая ПОКА job стояла в очереди executor'а (при
    # дефолтных двух воркерах это обычное дело), не должна оборачиваться
    # походом к AI-провайдеру — сеть и токены тратятся зря.
    if job.cancel_event.is_set():
        with job.lock:
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
        return
    try:
        raw_case = params.get("case")
        case = raw_case if isinstance(raw_case, dict) else {}
        fc = build_failure_context(case, code=code, lang=lang)
        outcome = explain_failure_detailed(fc, CONFIG)
    except Exception as exc:
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
        return
    with job.lock:
        # Запрос к провайдеру прервать нельзя (он уже ушёл и ограничен своим
        # таймаутом), но отменённая job'а не должна публиковать результат —
        # иначе «Отмена» на глазах пользователя сменяется подсказкой.
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
            return
        job.status = "done"
        # issue #975: рядом с подсказкой едет причина её отсутствия. Без неё
        # интерфейс не мог отличить «провайдер отверг ключ» от «канал
        # выключен» — в обоих случаях приходил `hint: null`.
        job.result = {
            "hint": outcome.text,
            "configured": is_configured(CONFIG),
            "reason": outcome.reason,
        }


def _run_stepik_submit_job(job: Job, code: str, params: dict[str, Any], lang: str) -> None:
    """Тело job'ы отправки решения на Stepik (issue #683, часть 2).

    Создаёт Stepik-сессию из ``secrets.json`` рабочей директории
    (``params["secrets_path"]``) и отправляет ``code`` на шаг
    ``params["step_id"]`` через core-поток ``submit_and_wait`` (attempt →
    submission → poll вердикта, авто-язык из ``code_templates`` шага). Нет
    валидного токена → ``error`` ``stepik_auth_required`` (в сеть ничего не
    уходит). Ленивый импорт stepik/oauth-стека держит ``runs.py`` импортируемым
    в изоляции (как ``_run_auth_job``). Результат — ``{status, hint, score,
    submission_id}`` (``status`` = correct/wrong/evaluation).

    issue #1175: вердикт платформы попутно уходит в историю — через
    ``core/submission``, общий путь отправки, а не отдельной веткой веб-слоя.
    Гейт согласия остаётся у веба (``history_db_path_if_enabled``): только он
    знает про оверрайд ``--serve --no-history``.
    """
    import requests

    from stepik_grader.core import submission
    from stepik_grader.core.oauth_flow import (
        load_secrets_dict,
        try_create_session_without_browser,
    )

    try:
        secrets_path = pathlib.Path(str(params["secrets_path"]))
        step_id = int(params["step_id"])
        try:
            session = try_create_session_without_browser(
                load_secrets_dict(secrets_path), secrets_path
            )
        except requests.RequestException as exc:
            # issue #816 (DEV-05): порядок except обязателен — RequestException
            # ЯВЛЯЕТСЯ подклассом OSError, поэтому ветка ниже перехватывала и
            # обрыв сети, отправляя пользователя перевыпускать OAuth-токен,
            # которого проблема не касается. Сеть — отдельный диагноз.
            with job.lock:
                job.status = "error"
                job.message_fields = message_fields("stepik_network_error", lang, error=str(exc))
            return
        except (OSError, ValueError):
            # нет/битый secrets.json — трактуем как «нет авторизации», не как сбой
            session = None
        if session is None:
            with job.lock:
                job.status = "error"
                job.message_fields = message_fields("stepik_auth_required", lang)
            return
        raw_task_dir = str(params.get("task_dir") or "")
        raw_workspace = str(params.get("workspace") or "")
        result = submission.submit_and_record(
            session,
            step_id,
            code,
            task_dir=pathlib.Path(raw_task_dir) if raw_task_dir else None,
            base_dir=pathlib.Path(raw_workspace) if raw_workspace else None,
            history_db=history_db_path_if_enabled(),
            cancel_event=job.cancel_event,
        )
    except Exception as exc:
        with job.lock:
            job.status = "error"
            job.message_fields = message_fields("run_internal_error", lang, error=str(exc))
        return
    with job.lock:
        # issue #797: отмена прекращает ОЖИДАНИЕ вердикта, но не отправку —
        # попытка на Stepik уже создана и останется в истории решений. Статус
        # терминальный «cancelled», как у грейд- и playground-job'ов.
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
            return
        job.status = "done"
        job.result = {
            "status": result.status,
            "hint": result.hint,
            "score": result.score,
            "submission_id": result.submission_id,
        }


def _run_auth_job(job: Job, params: dict[str, Any], lang: str) -> None:
    """Тело auth-job'ы (issue #402): браузерный OAuth-flow первого запуска.

    Блокирующий (до 120с) поход в браузер вынесен на воркер-поток, чтобы не
    держать HTTP-обработчик ``--serve``. Креды/путь приходят в ``params`` от
    ``server._handle_auth_start`` (уже под ``_guard_request``). Ленивый импорт
    ``auth_adapter`` держит ``runs.py`` импортируемым в изоляции (тесты) без
    OAuth/requests-стека; в самом ``--serve`` он всё равно грузится через
    ``server.py`` (верхнеуровневый импорт).
    """
    from stepik_grader.core.stepik_client import OAuthCancelled
    from stepik_grader.web import auth_adapter

    try:
        result = auth_adapter.perform_browser_auth(
            pathlib.Path(str(params["secrets_path"])),
            str(params["client_id"]),
            str(params["client_secret"]),
            str(params["redirect_uri"]),
            cancel_event=job.cancel_event,
        )
    except OAuthCancelled:
        # issue #971: отмена — не сбой авторизации. Человек, сам нажавший
        # «отмена», не должен читать сообщение о поломке; и `error` тут
        # означал бы «повторите», хотя повторять нечего.
        with job.lock:
            job.status = "cancelled"
            job.message_fields = message_fields("run_cancelled", lang)
        return
    except Exception as exc:
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
    except Exception as exc:
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
