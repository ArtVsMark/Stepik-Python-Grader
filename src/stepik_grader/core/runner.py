"""runner.py — Runner Protocol + LocalRunner (issue #136/#137/#138).

Архитектурный слой: Infrastructure.

Явная абстракция запуска кода (`docs/dev/design/server-mode.md § Runner-слой`, issue
#140): ``grader_core.run_single_test()`` делегирует фактический
subprocess-запуск сюда через ``Runner.run(RunSpec) -> RunOutcome``, не меняя
поведение (issue #138). ``RunOutcome`` несёт сырой итог запуска
(stdout/stderr/returncode/wall time/peak memory/timed_out) — вычисление
verdict/diff остаётся выше по стеку (``grader_core.py``); ``Runner``
вердиктов не выносит (`server-mode.md` § Runner-слой, инвариант 3).

``LocalRunner`` — рефактор текущего subprocess-пути: subprocess.Popen с
принудительным UTF-8 в дочернем окружении, best-effort лимит адресного
пространства (``RLIMIT_AS`` через ``resource.prlimit``, POSIX-only, issue
#67), фоновый psutil-поток мониторинга пикового RSS (issue #48 R-05).
``SandboxRunner`` (issue #266, реализация требований дизайна #157) — в
``core/sandbox/``: тот же протокол ``Runner`` с ОС-уровневой изоляцией
(bubblewrap на Linux, sandbox-exec на macOS, Job Objects на Windows), без
изменений в логике ``grader_core.py`` (только новый ``set_runner()`` для
инъекции и маппинг ``sandbox_violation`` в отдельный verdict).
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import psutil

# resource — POSIX-only (RLIMIT_AS для best-effort memory cap, issue #43 S-01).
# На Windows модуль отсутствует; лимит памяти там не применяется — тот же
# паттерн graceful degradation, что и у POSIX-only лимитов песочницы.
try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

from stepik_grader.config import get_config
from stepik_grader.core import spawn

__all__ = [
    "TRUNCATION_MARKER",
    "LocalRunner",
    "RunOutcome",
    "RunSpec",
    "Runner",
    "active_runner",
    "run_spec",
    "set_runner",
    "spec_source_bytes",
]

# issue #418: после kill дерева процессов даём ограниченное время на reap —
# внук, унаследовавший pipe, может держать его открытым, и тогда
# communicate()/wait() без предела виснет (репро: 8.1 с при timeout=2.0).
_KILL_REAP_TIMEOUT = 5.0


@dataclass(frozen=True)
class RunSpec:
    """Что запустить (`server-mode.md` § Runner-слой): исходник решения, stdin,
    лимиты. Не зависит от механизма изоляции — одинаков для ``LocalRunner`` и
    ``SandboxRunner``.

    **Два слоя (issue #550/#638).** Сериализуемое ЯДРО — ``code``/``stdin``/
    ``timeout``/``measure_memory``/``max_memory_mb``/``max_output_bytes`` —
    полностью описывает запуск и может быть перекодировано (JSON/pickle) для
    отправки удалённому backend'у (server mode, #151). Ключевое здесь — ``code``
    (issue #638): само СОДЕРЖИМОЕ исполняемого скрипта. Раньше spec нёс только
    ``path`` — путь на ЛОКАЛЬНОЙ ФС раннера, которого на удалённой стороне не
    существует, поэтому ядро было не по-настоящему сериализуемым. Теперь удалённый
    раннер материализует ``code`` у себя (как это уже делают sandbox-backend'ы,
    ``spec_source_bytes``), а ``path`` — лишь ЛОКАЛЬНАЯ оптимизация: исполнить
    существующий файл на месте без лишней копии. Задан должен быть хотя бы один из
    ``path``/``code`` (``__post_init__``); при ``code`` путь можно не передавать.

    ``cancel_event`` — ЛОКАЛЬНЫЙ-only канал: ``threading.Event`` несериализуем и
    осмыслен лишь в текущем процессе; remote/Docker-backend отменяет прогон своим
    механизмом, а не переносом Event, поэтому сериализующий слой обязан пропускать
    ``cancel_event``.

    ``cancel_event`` (issue #262) — опциональный сигнал best-effort отмены
    для async job-модели (``web/runs.py``). Сбор вывода идёт ОДНИМ путём
    (``_run_with_polling``) при любых значениях полей: лимит капит накопление
    (issue #629), отмена прерывает ожидание (issue #262), а без них обоих путь
    просто ждёт процесс блокирующим ``proc.wait(timeout)`` — без poll-латентности.
    Прежде «ни лимита, ни отмены» обслуживал одиночный ``proc.communicate()``, и
    поведение расходилось: там, где дренаж отдавал вывод верного решения,
    ``communicate()`` ждал EOF от живого внука и возвращал пустой вывод с
    ``timed_out=True`` (issue #1248). Необязательное поле обязано менять предел,
    а не вердикт.
    """

    stdin: bytes | None
    timeout: float
    # issue #638: локальный путь к скрипту — оптимизация (исполнить на месте без
    # копии), НЕ обязательное поле. ``code`` ниже несёт содержимое для remote.
    path: pathlib.Path | None = None
    # issue #638: содержимое исполняемого скрипта. Задан либо он, либо ``path``
    # (``__post_init__``). Для сериализуемого ядра/remote — источник истины.
    code: bytes | None = None
    measure_memory: bool = True
    max_memory_mb: int | None = None
    # issue #629: потолок НАКОПЛЕНИЯ stdout+stderr. ``None`` — без ограничения
    # (прежнее поведение). Лимит приходит из спецификации, а не читается из
    # CONFIG внутри раннера: модуль намеренно config-agnostic, как timeout и
    # max_memory_mb выше.
    max_output_bytes: int | None = None
    # issue #992 (SBX-1-01): под каким именем материализовать скрипт. Backend'ы
    # изоляции всегда писали его как ``solution.py``, и в function-режиме это
    # ломало верные решения поголовно: исполняемый скрипт там — сгенерированная
    # обёртка, которая импортирует модуль решения по имени. Записанная под тем же
    # именем, она импортировала саму себя и падала «cannot import name ... from
    # partially initialized module» — 3/3 OK без изоляции превращались в 0/3 FAIL.
    # ``None`` — прежнее поведение (``solution.py``), оно верно для stdin-режима.
    script_name: str | None = None
    # issue #992 (SBX-1-02): файлы, которые обязаны лежать рядом со скриптом
    # внутри изоляции — сам модуль решения для обёртки и соседние модули, что
    # решение импортирует. ``{имя файла: содержимое}``. Вне изоляции решение
    # исполняется на месте и соседи доступны сами собой; изоляция видит только
    # то, что ей отдали, — поэтому список нужен явный.
    aux_files: tuple[tuple[str, bytes], ...] = ()
    cancel_event: threading.Event | None = None

    def __post_init__(self) -> None:
        """Инвариант: задан хотя бы один источник скрипта — ``path`` или ``code``.

        Без него spec не описывал бы, что исполнять. ``frozen``-датакласс
        допускает ``__post_init__`` для чистой валидации (без мутации полей).
        """
        if self.path is None and self.code is None:
            raise ValueError("RunSpec requires 'path' (local file) or 'code' (inline source)")


def spec_source_bytes(spec: RunSpec) -> bytes:
    """Содержимое исполняемого скрипта ``spec`` (issue #638).

    ``spec.code`` (переносимо на remote) в приоритете; иначе — байты локального
    ``spec.path``. Инвариант ``RunSpec.__post_init__`` гарантирует хотя бы один
    источник, поэтому чтение ``path`` безопасно, когда ``code`` пуст. Единая
    точка «достать исходник» для sandbox-backend'ов (материализация в
    ``run_dir``) и будущего remote-раннера.
    """
    if spec.code is not None:
        return spec.code
    assert spec.path is not None  # инвариант __post_init__: задан path или code
    return spec.path.read_bytes()


def materialize_spec(spec: RunSpec, run_dir: pathlib.Path) -> pathlib.Path:
    """Разложить скрипт и его файлы-спутники в ``run_dir``; вернуть путь скрипта.

    Единая точка материализации для всех backend'ов изоляции (issue #992). Раньше
    каждый писал ``run_dir / "solution.py"`` сам, и три копии одной строки
    разошлись бы при первой же правке — а именно эта строка и ломала
    function-режим: обёртка получала имя модуля решения и импортировала саму
    себя.

    Имена спутников берутся как есть, но только базовые: путь с разделителем или
    ``..`` означал бы запись за пределы ``run_dir`` — то есть побег из изоляции
    через её же механизм подготовки.

    Raises:
        OSError: не удалось записать файл (передаётся вызывающему backend'у,
            который превращает это в ``launch_error``, а не в провал решения).
        ValueError: имя файла-спутника не базовое.
    """
    script_path = run_dir / (spec.script_name or "solution.py")
    script_path.write_bytes(spec_source_bytes(spec))
    for name, content in spec.aux_files:
        if pathlib.Path(name).name != name or name in {"", ".", ".."}:
            raise ValueError(f"aux file name must be a bare file name, got {name!r}")
        (run_dir / name).write_bytes(content)
    return script_path


@dataclass
class RunOutcome:
    """Сырой итог запуска — без вердикта (маппинг в case result выше по стеку,
    см. [`docs/dev/result-contract.md`](../../../docs/dev/result-contract.md)).

    ``launch_error`` заполняется, если процесс не удалось даже запустить
    (``OSError`` при spawn) — тогда ``stdout``/``stderr``/``returncode``
    неопределены (остаются дефолтами).

    ``cancelled`` (issue #262) — процесс убит из-за ``RunSpec.cancel_event``,
    а не из-за истечения ``timeout`` (``timed_out`` в этом случае остаётся
    ``False`` — маппится в отдельный verdict ``CANCELLED``, не ``TLE``, выше
    по стеку в ``grader_core.run_single_test()``).

    ``sandbox_violation`` (issue #266) — заполняется реализациями ``Runner``,
    изолирующими выполнение на уровне ОС (``core/sandbox/``), когда САМ
    Runner проактивно распознал и оборвал превышение квоты: ``"memory"``
    (RSS перешёл порог — psutil-поллинг, общий для всех 3 backend'ов),
    ``"output_size"`` (накопленный stdout+stderr превысил лимит) или
    ``"cpu"`` (``SIGXCPU`` от ``RLIMIT_CPU``, POSIX). Нарушения сети/ФС/
    лимита процессов **не** попадают сюда — ядро отклоняет их ВНУТРИ
    песочницы, ребёнок падает с обычным ненулевым exit code/traceback,
    и это корректно классифицируется как обычный ``RE`` (см.
    `docs/dev/design/server-mode.md § Классы ошибок <../../../docs/dev/design/server-mode.md>`_) —
    Runner не заглядывает внутрь чужого traceback, чтобы отличить их.
    ``LocalRunner`` никогда его не выставляет (остаётся ``None``). Маппится в
    отдельный verdict ``SANDBOX_VIOLATION`` (аддитивно к AC/WA/RE/TLE/
    CANCELLED), не ``RE``/``TLE``, чтобы UI не путал нарушение,
    которое сам Runner детектировал и оборвал, с обычным провалом решения.
    """

    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = 0
    elapsed: float = 0.0
    peak_memory_mb: float = 0.0
    timed_out: bool = False
    launch_error: str | None = None
    cancelled: bool = False
    sandbox_violation: str | None = None


@runtime_checkable
class Runner(Protocol):
    """Протокол исполнения (`server-mode.md` § Runner-слой, issue #137).

    Контракт не зависит от subprocess — реализация вольна выбрать любой
    механизм (``LocalRunner`` — subprocess на этой машине, будущий
    ``SandboxRunner`` — контейнер/VM с сетевой изоляцией).

    ``supports_project_imports`` (issue #550) — capability-флаг: пробрасывает ли
    Runner пакет грейдера (site-packages проекта) в дочерний процесс. ``True`` у
    ``LocalRunner`` (общее окружение с сервером/CLI), ``False`` у
    ``SandboxRunner`` (ОС-изоляция намеренно НЕ даёт доступ к site-packages,
    SECURITY.md). Потребители (``core/tracer``) консультируют способность вместо
    хрупкого ``type(runner).__name__ == "SandboxRunner"`` — новый backend
    (Docker/remote) объявляет флаг сам и не обходит guard молча.
    """

    supports_project_imports: bool

    def run(self, spec: RunSpec) -> RunOutcome:
        """Запустить ``spec`` и вернуть сырой итог (без вычисления вердикта)."""
        ...


def _apply_memory_limit(pid: int, max_memory_mb: int | None) -> None:
    """Best-effort лимит адресного пространства (RLIMIT_AS) на дочерний pid
    ПОСЛЕ spawn — потокобезопасная замена preexec_fn (issue #67).

    ``preexec_fn`` форкает в многопоточном родителе (грейдер держит
    psutil-поток мониторинга памяти) — документированно небезопасно.
    ``resource.prlimit`` ставит лимит на уже запущенный pid извне, без fork в
    родителе.

    Linux-only: ``resource.prlimit`` отсутствует на macOS (``AttributeError``)
    и на Windows (нет самого модуля ``resource``) — там no-op, решение
    выполняется без лимита памяти, как раньше на Windows. Окно «ребёнок
    стартовал без лимита» ~мс до exec пользовательского кода — приемлемо для
    задач курса (issue #43 S-01 — best-effort, не OS-sandbox; нет изоляции
    ФС/сети).
    """
    if resource is None or max_memory_mb is None:
        return
    limit_bytes = max_memory_mb * 1024 * 1024
    # typeshed помечает prlimit/RLIMIT_AS Linux-only; на macOS prlimit
    # отсутствует (AttributeError), OSError — нет процесса/прав, ValueError —
    # некорректный лимит. Любую из них глотаем: лишь пропускаем cap.
    with contextlib.suppress(AttributeError, ValueError, OSError):
        resource.prlimit(pid, resource.RLIMIT_AS, (limit_bytes, limit_bytes))  # type: ignore[attr-defined]


def sample_tree_rss(proc: psutil.Process, *, children: list[Any] | None = None) -> float:
    """Суммарный RSS (МБ) процесса ``proc`` и всех его потомков — единый замер
    памяти для ``LocalRunner`` и ``SandboxRunner`` (issue #556).

    Замер одного ``proc.pid`` недостоверен под ``--sandbox``: наблюдаемый pid —
    процесс изоляции (bwrap/sandbox-exec), а решение исполняется его потомком
    (внук в отдельном PID-namespace на Linux при ``--unshare-pid``). С точки
    зрения хоста этот потомок всё равно виден как обычный host-PID и попадает в
    ``children(recursive=True)``, поэтому суммирование поддерева даёт память
    решения, а не только обёртки-изолятора. Для ``LocalRunner`` (решение —
    прямой ребёнок) дополнительно учитывается память любых порождённых им
    процессов (multiprocessing/subprocess) — тоже точнее прежнего.

    Память самого ``proc`` читается напрямую, и её ошибку (процесс исчез/зомби/
    нет доступа) НЕ глотаем — это сигнал вызывающей стороне (быстрый выход →
    warn в ``_measure_peak_memory``, обрыв поллинга в песочнице). Потомки —
    best-effort: исчезнувший в момент обхода узел пропускаем, не обнуляя итог.

    issue #996 (MTX-6-04): ``children`` — уже собранный список потомков.
    ``children(recursive=True)`` обходит таблицу процессов целиком и стоит на
    порядок дороже одного ``memory_info()``, а измеритель пика опрашивает
    процесс каждые 20 мс — на короткой задаче обход съедает заметную долю того
    самого времени, которое грейдер и показывает пользователю. Кому нужна
    скорость, а не мгновенная реакция на новорождённый процесс, обновляет
    список реже и передаёт его сюда. **Песочница список не кэширует**: там
    этот же замер — активное enforcement лимита памяти, и появившийся потомок
    обязан попасть под лимит сразу, а не через полсекунды.
    """
    total = float(proc.memory_info().rss) / 1024 / 1024
    if children is None:
        try:
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return total
    for child in children:
        try:
            total += float(child.memory_info().rss) / 1024 / 1024
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return total


# issue #996 (MTX-6-04): как часто измеритель перечитывает список потомков и
# как часто вообще опрашивает память. Обход дерева процессов дорог, а
# новорождённый потомок для ЗАМЕРА (в отличие от enforcement в песочнице)
# терпит полсекунды: пик, который держится меньше, всё равно не поймать
# опросом с любым разумным периодом.
_CHILDREN_REFRESH_SEC = 0.5
_POLL_INTERVAL_SEC = 0.02


def _measure_peak_memory(
    proc: subprocess.Popen[bytes], result: list[float], stop: threading.Event
) -> None:
    """Поток: замерять RSS дерева дочернего процесса (proc + потомки, issue
    #556) до его завершения.

    Делает первый замер немедленно (до первого sleep), чтобы уловить
    даже очень короткие процессы (< 20 мс). Затем продолжает опрос
    каждые 20 мс до сигнала stop.

    Записывает пик памяти (МБ) в result[0]. Замер идёт через общий
    ``sample_tree_rss`` — тот же helper, что и в песочнице, поэтому память
    решения, породившего внуков (multiprocessing/subprocess), не теряется.
    """

    # issue #48 R-05: proc.pid is read after Popen but before communicate() --
    # on a very short-lived child (especially on Windows) the process can exit
    # before psutil.Process(pid)/memory_info() ever samples it. The except
    # branches below already handle that, but previously did so silently,
    # returning peak=0.0 indistinguishable from "the process genuinely used
    # ~0 memory" -- warn so a caller doesn't mistake an unreliable reading for
    # a real measurement.
    #
    # issue: the message used to interpolate f"pid={proc.pid}", which made
    # every occurrence a distinct string -- Python's default warning filter
    # dedupes on the exact rendered message text, so a batch grading run full
    # of trivially-fast solutions (print(1), etc. -- common Stepik exercises)
    # printed one UserWarning PER test case instead of once. Keeping the
    # message text constant lets the stdlib's own "default" filter show it
    # once per interpreter session and silently drop the rest, with no extra
    # state to maintain here.
    def _warn_unreliable() -> None:
        warnings.warn(
            "peak memory measurement unreliable for a fast-exiting process: it "
            "exited before it could be sampled (reported peak may be 0.0 or an "
            "undercount, common for trivially fast solutions). Shown once per "
            "process by Python's default warning filter.",
            stacklevel=2,
        )

    # issue #996 (JRN-1-01/JRN-2-01): пик пишется в `result` СРАЗУ, на каждом
    # обновлении, а не одной строкой в конце функции. Прежний порядок был
    # неисполним по построению: `RunOutcome` собирается внутри
    # `_run_with_polling`, а `stop_event.set()` и `join` происходят уже после
    # его возврата — то есть результат читал `result[0]`, пока поток ещё
    # крутился и ничего туда не записал. Замер приходил нулевым ВСЕГДА:
    # проверено прогоном, решение на 24 МБ отдавало `peak_memory_mb=0.0`.
    peak = 0.0

    def remember(rss: float) -> None:
        """Запомнить пик и сразу отдать его наружу."""
        nonlocal peak
        if rss > peak:
            peak = rss
            result[0] = peak

    # issue #996 (MTX-6-04): список потомков перечитывается раз в
    # _CHILDREN_REFRESH_SEC, а не на каждой из пятидесяти итераций в секунду.
    # Обход дерева процессов — самая дорогая часть замера, и платить за неё
    # приходится тем самым временем, которое грейдер измеряет.
    children: list[Any] = []
    next_refresh = 0.0

    def sample(ps_proc: psutil.Process) -> float:
        """Замер поддерева со списком потомков из кэша.

        Кэш обновляется ПОСЛЕ замера, а не до: ошибка чтения собственной
        памяти — сигнал «процесс исчез», и она обязана дойти до вызывающей
        стороны первой, как и было до кэша. Первый замер идёт с пустым
        списком — потомков в этот момент ещё нет, а появившиеся попадут в
        следующий: пик считается максимумом, поэтому ничего не теряется.
        """
        nonlocal children, next_refresh
        rss = sample_tree_rss(ps_proc, children=children)
        now = time.monotonic()
        if now >= next_refresh:
            try:
                children = ps_proc.children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                children = []
            next_refresh = now + _CHILDREN_REFRESH_SEC
        return rss

    try:
        ps_proc = psutil.Process(proc.pid)
        try:
            remember(sample(ps_proc))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _warn_unreliable()
            return
        while not stop.is_set():
            try:
                remember(sample(ps_proc))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                _warn_unreliable()
                break
            stop.wait(_POLL_INTERVAL_SEC)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _warn_unreliable()


def _write_stdin(pipe: Any, data: bytes | None) -> None:
    """Записать stdin и закрыть pipe — в отдельном потоке (issue #419).

    Ребёнок, не читающий ввод, при ``stdin`` больше pipe-буфера заблокировал бы
    синхронный ``write`` в главном потоке до входа в poll-цикл — тогда ни
    ``spec.timeout``, ни ``spec.cancel_event`` не сработали бы. Вынос в
    daemon-поток разрывает этот deadlock: поток просто зависнет на ``write`` и
    умрёт вместе с процессом (``BrokenPipeError`` после kill дерева).
    """
    try:
        if data is not None:
            pipe.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            pipe.close()


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Убить всё дерево процессов решения, а не только прямого ребёнка (issue #418).

    Решение может породить внуков (multiprocessing/subprocess); внук,
    унаследовавший stdout/stderr, держит pipe открытым и вешает
    ``communicate()``/``wait()`` без таймаута, а осиротевший внук продолжает
    жечь CPU после TLE/cancel. Бьём по группе процессов на POSIX (``os.killpg``
    — работает благодаря ``start_new_session=True`` при spawn) и добиваем дерево
    через psutil (кросс-ОС, единственный путь на Windows). Best-effort:
    полностью демонизированный (double-fork + setsid) внук может уйти — это
    осознанный предел без OS-sandbox.
    """
    # Собираем детей ДО убийства родителя, пока связь parent->child ещё видна.
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        children = []

    if os.name == "posix":
        # os.killpg/getpgid + signal.SIGKILL — POSIX-only, отсутствуют в
        # typeshed под Windows; ветка защищена os.name, но CI гоняет mypy на
        # каждой ОС матрицы (та же причина, что SIGXCPU в _posix_common.py).
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]

    with contextlib.suppress(OSError):
        proc.kill()

    for child in children:
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            child.kill()


class _OutputBudget:
    """Общий на stdout+stderr бюджет накопления вывода (issue #629).

    Лимит применяется к ХРАНЕНИЮ, а не к чтению: дренаж продолжается и после
    исчерпания бюджета, просто лишнее отбрасывается. Перестать читать нельзя —
    OS pipe-буфер заполнится, ребёнок заблокируется на ``write``, и вместо
    ограничения памяти мы получим зависший до таймаута процесс (ровно тот
    deadlock, ради которого дренаж и вынесен в потоки).

    Бюджет общий для обоих потоков, поэтому доступ под ``Lock``.
    """

    def __init__(self, limit: int | None) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._used = 0
        self.truncated = False

    def take(self, chunk: bytes) -> bytes:
        """Вернуть часть ``chunk``, помещающуюся в бюджет (может быть пустой)."""
        if self._limit is None:
            return chunk
        with self._lock:
            room = self._limit - self._used
            if room <= 0:
                self.truncated = True
                return b""
            if len(chunk) <= room:
                self._used += len(chunk)
                return chunk
            self._used = self._limit
            self.truncated = True
            return chunk[:room]


# issue #935: маркер выделен в константу, потому что по нему теперь опознают
# факт обрезки выше по стеку (grader_core). Поиск по вольной подстроке
# развалился бы от любой правки текста; общая константа делает связь явной.
TRUNCATION_MARKER = "[stepik-grader] вывод обрезан"


def _truncation_note(limit: int | None) -> bytes:
    """Пометка в stderr о том, что вывод обрезан (issue #629)."""
    return f"\n{TRUNCATION_MARKER}: превышен лимит {limit} байт\n".encode()


# issue #632: типовые подстроки в ИМЕНИ env-переменной, выдающие секрет. Скраб
# по denylist, а не allowlist: дефолтный LocalRunner делит окружение с грейдером/
# сервером и должен сохранить project-import (PYTHONPATH/VIRTUAL_ENV и пр.) для
# трассировщика — вырезаем только заведомо секретное, не трогая остальное.
_SECRET_ENV_SUBSTRINGS: tuple[str, ...] = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "APIKEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)


def _scrub_secret_env(env: dict[str, str]) -> None:
    """Удалить из ``env`` секрет-переменные перед spawn решения (issue #632).

    Мутирует ``env`` на месте. Дефолтный ``LocalRunner`` исполняет код БЕЗ
    ОС-изоляции и наследует окружение грейдера (а под ``--serve`` без
    ``--sandbox`` — всё окружение сервера), но собственные секреты грейдера коду
    решения не нужны и не должны в него утекать. Убирается сконфигурированное имя
    AI-ключа (``CONFIG.ai_api_key_env``, даже если оператор его переименовал) и
    любая переменная, чьё имя содержит типовую секрет-подстроку. Sandbox-бэкенды
    чистят окружение целиком; здесь — консервативный denylist, чтобы не сломать
    project-import (см. ``supports_project_imports``).
    """
    # issue #996 (LNCH-3-03): имя переменной читается в момент ВЫЗОВА.
    # `CONFIG` связан на импорте, а `override_config()` (флаги CLI, `--config`)
    # создаёт НОВЫЙ объект — прежний остаётся со старым значением. Здесь это
    # не косметика: по этому имени из окружения решения вычищается ключ AI,
    # и устаревшее значение означает, что чистится не та переменная.
    ai_key_var = get_config().ai_api_key_env
    for name in list(env):
        if name == ai_key_var or any(sub in name.upper() for sub in _SECRET_ENV_SUBSTRINGS):
            env.pop(name, None)


class LocalRunner:
    """Subprocess-реализация ``Runner`` (текущее поведение, issue #138).

    Запускает ``sys.executable spec.path``, подаёт ``spec.stdin``, ждёт до
    ``spec.timeout`` секунд; при включённом ``spec.measure_memory`` — фоновый
    поток опроса RSS; при заданном ``spec.max_memory_mb`` — best-effort
    ``RLIMIT_AS`` (POSIX). Дочернему процессу принудительно ставится
    UTF-8 окружение (``PYTHONIOENCODING``/``PYTHONUTF8``), иначе на Windows по
    умолчанию используется cp1251, что ломает кириллицу в выводе.
    """

    # issue #550: LocalRunner делит окружение с сервером/CLI — site-packages
    # проекта доступны дочернему процессу (трассировщик импортируется). tracer
    # консультирует эту способность вместо проверки имени класса.
    supports_project_imports = True

    def run(self, spec: RunSpec) -> RunOutcome:
        """Исполнить ``spec`` в subprocess и вернуть сырой ``RunOutcome``.

        Реализация ``Runner``-протокола по умолчанию (без ОС-изоляции): запускает
        интерпретатор через ``sys.executable``, подаёт stdin, замеряет время и
        пиковую память (psutil-поток), при TLE/cancel убивает группу процессов.
        """
        peak_mb_result: list[float] = [0.0]
        stop_event = threading.Event()
        mem_thread: threading.Thread | None = None

        child_env = os.environ.copy()
        _scrub_secret_env(child_env)  # issue #632: не наследовать секреты грейдера
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        # issue #726: детерминированный stderr решения. Python 3.13+ красит
        # traceback, если у родителя выставлен FORCE_COLOR/PYTHON_COLORS=1 —
        # даже когда stderr это pipe. Унаследованный цвет доезжал до UI сырыми
        # ANSI-последовательностями («\x1b[35m» в ячейке таблицы), а под какой
        # оболочкой запущен грейдер (IDE, CI, dev-обёртка) — не наше дело.
        child_env["PYTHON_COLORS"] = "0"
        child_env["NO_COLOR"] = "1"

        # issue #638: spec может нести содержимое решения (``code``) вместо/помимо
        # локального ``path``. Есть ``code`` → материализуем во временный .py и
        # исполняем его (то же сделал бы remote-раннер у себя); иначе исполняем
        # существующий ``path`` без копии (локальная оптимизация). Инвариант
        # ``RunSpec.__post_init__`` гарантирует, что задан один из двух.
        tmp_code_dir: pathlib.Path | None = None
        if spec.code is not None:
            # issue #799 (SECC-01): каталог, где лежит скрипт, CPython ставит
            # ПЕРВЫМ в sys.path — а раньше это был общий системный temp. На
            # многопользовательском POSIX-хосте посторонний мог заранее
            # положить туда `/tmp/json.py`, и `import json` в решении подхватил
            # бы чужой код правами владельца грейдера. Права самого файла
            # (0600) от этого не спасают: атака идёт на каталог. Приватный
            # каталог 0700 (mkdtemp) закрывает вектор — так уже делают все три
            # sandbox-backend'а.
            try:
                tmp_code_dir = pathlib.Path(tempfile.mkdtemp(prefix="stepik-run-"))
                exec_path: pathlib.Path = tmp_code_dir / "solution.py"
                exec_path.write_bytes(spec.code)
            except OSError as exc:
                if tmp_code_dir is not None:
                    shutil.rmtree(tmp_code_dir, ignore_errors=True)
                return RunOutcome(launch_error=str(exc), timed_out=False)
        else:
            assert spec.path is not None  # инвариант __post_init__: задан path или code
            exec_path = spec.path

        start = time.perf_counter()
        popen_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            # issue #418: своя сессия/группа процессов, чтобы при TLE/cancel
            # убить всё дерево решения (os.killpg), а не только прямого ребёнка.
            popen_kwargs["start_new_session"] = True
        proc: subprocess.Popen[bytes] | None = None
        try:
            # issue #1149: запуск идёт через страховку, а не голым Popen. Свой
            # `timeout` ниже покрывает работу процесса, но подвиснуть можно
            # РАНЬШЕ — в самом `Popen.__init__`, на чтении errpipe после
            # fork/exec; тогда таймаут прогона до дела не доходит, и поток
            # остаётся заблокированным навсегда (под `--serve` это занятый
            # воркер, которого никто не отменит). Ловили на macOS/Windows с
            # Python 3.14 — здесь и в `run_lint` (issue #877).
            proc = spawn.guarded_popen(
                [sys.executable, str(exec_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                **popen_kwargs,
            )
            # issue #67: лимит памяти ставим на pid ПОСЛЕ spawn (prlimit), а не
            # через preexec_fn — тот небезопасен при активном psutil-потоке.
            _apply_memory_limit(proc.pid, spec.max_memory_mb)

            if spec.measure_memory:
                mem_thread = threading.Thread(
                    target=_measure_peak_memory,
                    args=(proc, peak_mb_result, stop_event),
                    daemon=True,
                )
                mem_thread.start()

            # issue #1248: путь ОДИН — bounded-дренаж. Прежде здесь стоял
            # быстрый `communicate()` для случая «нет отмены И нет лимита»,
            # и он вёл себя иначе: решение, оставившее живого внука с
            # открытым stdout, не давало EOF, `communicate()` ждал весь
            # таймаут и возвращал ПУСТОЙ вывод с `timed_out=True` — верное
            # решение получало TLE. Дренаж-потоки читают `read1` и такого
            # решения не теряют (issue #952). Боевой путь лимит задаёт
            # всегда, поэтому дефект жил в контракте `RunSpec`: поле
            # объявлено необязательным, а поведение без него было другим.

            try:
                outcome = self._run_with_polling(proc, spec, start, peak_mb_result)
            finally:
                stop_event.set()
            if mem_thread is not None:
                mem_thread.join(timeout=0.5)
            return outcome
        except (OSError, spawn.SpawnTimeout) as exc:
            # SpawnTimeout — это «не стартовал», то есть тот же класс, что и
            # OSError при спавне: вердикт получает причину вместо зависания.
            stop_event.set()
            return RunOutcome(launch_error=str(exc), timed_out=False)
        finally:
            # issue #624: гарантированная уборка. Внешний try ловил только
            # OSError (спавн) и TimeoutExpired (внутри) — KeyboardInterrupt,
            # ошибка из Thread.start() или неожиданное исключение в
            # communicate уходили наружу, оставляя живой процесс решения.
            # На сервере это прямая утечка ресурсов. На штатных путях процесс
            # уже завершён (poll() != None), поэтому kill не срабатывает.
            stop_event.set()
            if proc is not None and proc.poll() is None:
                _kill_process_tree(proc)
            # issue #638: убрать временный файл, материализованный из spec.code
            # (issue #799 — вместе с его приватным каталогом).
            if tmp_code_dir is not None:
                shutil.rmtree(tmp_code_dir, ignore_errors=True)

    def _run_with_polling(
        self,
        proc: subprocess.Popen[bytes],
        spec: RunSpec,
        start: float,
        peak_mb_result: list[float],
    ) -> RunOutcome:
        """Poll-версия ``proc.communicate()`` с bounded-дренажем вывода.

        Прерывается по ``spec.cancel_event`` (issue #262) и капит накопление
        stdout+stderr по ``spec.max_output_bytes`` (issue #629). Единственный
        путь сбора вывода (issue #1248): оба поля опциональны — ``None`` в
        лимите означает «без потолка», ``None`` в отмене — ожидание без опроса.

        Дренирует stdout/stderr в фоновых потоках всё время ожидания — как
        это делает сам ``communicate()`` внутри себя. Без этого дочерний
        процесс, пишущий много в stdout, застрял бы на заполненном OS
        pipe-буфере, пока мы просто опрашиваем ``proc.poll()`` каждые ~100мс.
        stdin пишется и закрывается до входа в цикл опроса (тот же порядок,
        что ``communicate()``), а не построчно синхронно с чтением — иначе
        возможен классический deadlock subprocess (записываем stdin, пока
        никто не читает переполненный stdout).
        """
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        # issue #629: до появления бюджета sink'и росли без границы — решение с
        # бесконечным print набивало RAM хоста за секунды таймаута, а при пуле
        # параллельных web-job'ов это клало весь процесс по OOM.
        budget = _OutputBudget(spec.max_output_bytes)

        def _drain(pipe: Any, sink: list[bytes]) -> None:
            # issue #952 (RUN-4-01): `read1`, а не `read`. `read(65536)` ждёт
            # ЛИБО полные 65536 байт, ЛИБО EOF — и отдаёт накопленное только
            # тогда. Решение, оставившее живого внука с открытым stdout, EOF не
            # даёт: «7\n» лежит в буфере, `proc.wait()` уже вернулся по
            # основному процессу, `reader.join(timeout=1.0)` истекает, поток
            # бросают — и sink остаётся ПУСТЫМ. Верное решение получает
            # `WA / Actual: (empty)`.
            #
            # `read1` возвращает то, что пришло за один системный вызов, — так
            # же читает и сам `communicate()`. Байты попадают в sink сразу,
            # независимо от того, кто ещё держит другой конец трубы.
            try:
                for chunk in iter(lambda: pipe.read1(65536), b""):
                    kept = budget.take(chunk)
                    if kept:
                        sink.append(kept)
            except (OSError, ValueError):
                pass

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True),
        ]
        for reader in readers:
            reader.start()

        if proc.stdin is not None:
            # issue #419: запись stdin — в отдельном потоке, а не синхронно в
            # главном (иначе не-читающий ребёнок при большом stdin повесил бы
            # write до входа в poll-цикл, и timeout/cancel не сработали бы).
            writer = threading.Thread(
                target=_write_stdin, args=(proc.stdin, spec.stdin), daemon=True
            )
            writer.start()

        # issue #629: путь обслуживает и bounded-вывод БЕЗ отмены (max_output_bytes
        # задан, cancel_event нет). Дренаж-потоки уже капят вывод; ждать завершения:
        #   • cancel_event нет → блокирующий proc.wait(timeout) без poll-латентности
        #     (тот же эффективный wait, что внутри communicate(), но с bounded-выводом;
        #     poll-цикл добавлял бы 0.1-с гранулярность опроса КАЖДОМУ синхронному грейду);
        #   • cancel_event есть → poll-цикл, чтобы реагировать на отмену между тиками.
        cancelled = False
        timed_out = False
        if spec.cancel_event is None:
            try:
                proc.wait(timeout=spec.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            while True:
                if proc.poll() is not None:
                    break
                remaining = spec.timeout - (time.perf_counter() - start)
                if remaining <= 0:
                    timed_out = True
                    break
                if spec.cancel_event.wait(min(0.1, remaining)):
                    cancelled = True
                    break

        if cancelled or timed_out:
            # issue #418: убить всё дерево, reap ограничен по времени.
            _kill_process_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_KILL_REAP_TIMEOUT)

        for reader in readers:
            reader.join(timeout=1.0)

        elapsed = time.perf_counter() - start
        # issue #421: reader'ы уже слили частичный вывод в память — вернуть его
        # и при TLE/cancel, а не выбрасывать.
        stdout = b"".join(stdout_chunks)
        stderr = b"".join(stderr_chunks)
        if budget.truncated:
            # Пометка идёт в stderr, а не в stdout: stdout сравнивается с
            # ожидаемым выводом, и служебная строка ломала бы вердикт.
            stderr += _truncation_note(spec.max_output_bytes)
        if timed_out:
            return RunOutcome(
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                elapsed=spec.timeout,
                peak_memory_mb=peak_mb_result[0],
            )
        if cancelled:
            return RunOutcome(
                stdout=stdout,
                stderr=stderr,
                cancelled=True,
                elapsed=elapsed,
                peak_memory_mb=peak_mb_result[0],
            )
        return RunOutcome(
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            elapsed=elapsed,
            peak_memory_mb=peak_mb_result[0],
            timed_out=False,
        )


# ---------------------------------------------------------------------------
# Реестр активного Runner'а — issue #830 (ARCH-03)
#
# Раньше синглтон и три функции доступа жили в ``core/grader_core`` —
# оркестраторе. Из-за этого ``microbench_runner`` и ``tracer`` (модули НИЖНЕГО
# уровня) импортировали оркестратор ради одного вызова ``run_spec``, и оба
# импорта приходилось делать ленивыми, чтобы не собрать цикл. DAG-guard такие
# рёбра не видит: он намеренно не спускается в тела функций, поэтому цикл
# существовал, а тест оставался зелёным — ацикличность держалась на дисциплине
# «не забыть сделать импорт ленивым», а не на структуре.
#
# Владелец протокола ``Runner`` — этот модуль, здесь реестру и место. В
# ``grader_core`` остаётся реэкспорт: он часть публичного фасада (ADR-0010), и
# менять его поверхность ради переезда внутренностей незачем.
# ---------------------------------------------------------------------------

# Runner активен на весь процесс — по умолчанию LocalRunner (issue #138);
# CLI подменяет его на SandboxRunner (issue #266, core/sandbox/) через
# set_runner() при --sandbox.
_RUNNER: Runner = LocalRunner()


def set_runner(runner: Runner) -> None:
    """Подменить активный ``Runner`` на весь процесс (issue #266).

    Единственная точка инъекции ``SandboxRunner``/иной реализации — вызывается
    один раз при старте CLI (``--sandbox``), до диспетчеризации в конкретный
    режим. Не влияет на поведение, если не вызывается: дефолт — ``LocalRunner``.
    """
    global _RUNNER
    _RUNNER = runner


def run_spec(spec: RunSpec) -> RunOutcome:
    """Исполнить один ``RunSpec`` через активный ``Runner`` и вернуть сырой итог.

    Публичная точка запуска для потребителей вне грейдинга (web-песочница,
    issue #317): прячет выбор backend'а (``LocalRunner``/``SandboxRunner``) за
    публичной поверхностью — вызывающему не нужно (и нельзя, ADR-0010) трогать
    приватный синглтон ``_RUNNER``. Читает module-global при каждом вызове,
    поэтому ``set_runner()`` и тестовые подмены ``_RUNNER`` видны немедленно.
    """
    return _RUNNER.run(spec)


def active_runner() -> Runner:
    """Активный ``Runner`` процесса — публичный аксессор его capability-флагов.

    Замена прямому доступу к приватному ``_RUNNER`` (issue #550): ``core/tracer``
    консультирует ``active_runner().supports_project_imports``, чтобы решить,
    доступен ли пошаговый трейс, вместо хрупкого ``type(_RUNNER).__name__ ==
    "SandboxRunner"``. Читает module-global при каждом вызове — ``set_runner()``
    и тестовые подмены видны немедленно.
    """
    return _RUNNER
