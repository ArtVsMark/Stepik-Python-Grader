"""launcher.py — GUI-лаунчер веб-интерфейса без командной строки (issue #661).

Application-слой, entry point (``python -m stepik_grader.launcher`` и
gui-script ``stepik-grader-gui``). Небольшое tkinter-окно: выбор варианта
запуска (простой / с изоляцией ``--sandbox``), порт, рабочая папка, кнопка
Запустить/Остановить, статус со ссылкой и авто-открытие браузера.

Почему это НЕ часть веба (issue #661): чтобы открыть любую страницу
веб-интерфейса, сервер уже должен быть запущен — стартовый экран сам себя не
поднимет. Управлять запуском может только то, что живёт ВНЕ сервера.

Архитектура: лаунчер поднимает сервер как ОТДЕЛЬНЫЙ процесс
``python -m stepik_grader --serve --port … [--sandbox] --root …``, а не в
потоке своего процесса. Причины (ADR-логика, issue #661):

* ``run_server()`` блокирует (``serve_forever``) и не отдаёт наружу объект
  сервера — из потока его нельзя корректно остановить;
* ``--sandbox`` мутирует ПРОЦЕСС-ГЛОБАЛЬНЫЙ runner (``set_runner``) и историю;
  в общем процессе состояние изоляции «залипало» бы между перезапусками.

Отдельный процесс даёт чистый stop (terminate), ноль утечки глобального
состояния и переиспользует уже готовый CLI-путь (флаги ``--serve/--sandbox/
--port/--root`` и honest-fail на недоступном backend'е через
``SandboxUnavailableError``). Как следствие модуль — leaf: НИКАКИХ проектных
импортов, только stdlib (+ опциональный ``rich`` для fallback-вывода), то есть
ноль новых рёбер DAG.

``tkinter`` импортируется ЛЕНИВО (внутри GUI-путей), поэтому GUI-free ядро
(``build_server_command``/``port_available``/``ServerController``) импортируется
и тестируется в том числе в headless-окружении без дисплея.

Threat model: по умолчанию изоляция ВЫКЛЮЧЕНА (как и весь остальной проект —
``LocalRunner`` без OS-sandbox). Режим изоляции меняется только здесь/через
CLI, никогда из самого веб-интерфейса.
"""

from __future__ import annotations

import contextlib
import json
import locale
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LANG_ENV_VAR",
    "LauncherApp",
    "ServerController",
    "ServerState",
    "ServerStatus",
    "build_server_command",
    "count_tasks",
    "create_app",
    "default_workdir",
    "detect_lang",
    "load_ui_messages",
    "main",
    "port_available",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# -- Локализация окна (issue #821) -------------------------------------------
#
# GUI — самый низкобарьерный вход в продукт («без командной строки»), и он был
# единственной поверхностью вообще без переводов: окно, кнопки и ошибки только
# по-русски. Каталог читается ФАЙЛОМ, а не импортом `core.i18n`: модуль по
# замыслу leaf (см. докстринг), и импорт ядра добавил бы ребро DAG ради
# восемнадцати подписей. JSON — stdlib, ребра не возникает.
#
# Язык: переменная окружения перекрывает всё, иначе — системная локаль, иначе
# русский (прежнее поведение). У GUI нет argparse, поэтому флага здесь нет.
LANG_ENV_VAR = "STEPIK_GRADER_LANG"
_SUPPORTED_LANGS = ("ru", "en")
_FALLBACK_LANG = "ru"
_LOCALES_DIR = Path(__file__).parent / "core" / "locales"

# Конфиг загрузчика задач: из него берётся папка с задачами для дефолта окна
# (issue #823). Имя дублирует ``downloader.CONFIG_FILE`` намеренно — модуль
# leaf, проектных импортов в нём нет.
_STEPIK_CONFIG_NAME = "stepik_config.json"

# Глубина обхода при подсчёте задач в рабочей папке: рабочей папкой может
# оказаться домашняя, и полный скан диска в GUI недопустим.
#
# Число не произвольное — это структура, которую создаёт собственный загрузчик:
# `<курс>/<секция>/<урок>/<шаг>`, то есть задача лежит на ЧЕТВЁРТОМ уровне от
# рабочей папки. При прежней глубине 3 счётчик до неё не доходил и показывал
# «Найдено задач: 0» на полной папке (issue #1018) — проверено прогоном на
# реально скачанном курсе. Пятый уровень — запас на одну обёртку сверху
# (например, папка-семестр над курсами).
_TASK_SCAN_DEPTH = 5

# Сколько ждём готовности сервера (bind + первый accept) после старта, и с каким
# шагом опрашиваем TCP. Импорт http-стека и чтение статики при старте --serve
# укладываются в доли секунды; 8с — щедрый запас на медленную ФС/антивирус.
_READY_TIMEOUT_S = 8.0
_PROBE_INTERVAL_S = 0.15
# terminate → (по таймауту) kill: локальный http.server гасится мгновенно.
_STOP_TIMEOUT_S = 5.0
# Сколько последних строк stderr дочернего процесса держим для диагностики
# отказа (порт занят / sandbox недоступен и т.п.).
_STDERR_TAIL_LINES = 50


# Вывод через rich с graceful fallback на print() (инвариант CLAUDE.md). Свой
# локальный _console — как в downloader.py (issue #354): не тянем core/reporter,
# чтобы модуль оставался leaf'ом.
try:
    from rich.console import Console

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False


def _system_lang() -> str | None:
    """Двухбуквенный код системного языка или ``None``, если определить нечем."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value.split(".")[0].split("_")[0].lower()
    with contextlib.suppress(ValueError, locale.Error):
        code = locale.getlocale()[0]
        if code:
            lowered = code.lower()
            # Windows отдаёт человекочитаемое имя («Russian_Russia»), POSIX —
            # код («ru_RU»): оба сводим к первым буквам названия языка.
            return "ru" if lowered.startswith(("ru", "russian")) else lowered[:2]
    return None


def detect_lang() -> str:
    """Язык окна: ``STEPIK_GRADER_LANG`` → системная локаль → русский (issue #821)."""
    env = (os.environ.get(LANG_ENV_VAR) or "").strip().lower()
    if env in _SUPPORTED_LANGS:
        return env
    system = _system_lang()
    if system is None:
        return _FALLBACK_LANG
    return "ru" if system.startswith("ru") else "en"


def load_ui_messages(lang: str) -> dict[str, str]:
    """Подписи окна из ``core/locales/<lang>.json`` (чтение файлом, не импортом).

    Graceful degradation в духе остального проекта: пропавший или битый файл —
    пустой словарь и откат на русский, а не исключение в GUI. Если недоступен и
    он, ``_t`` покажет сам ключ — окно всё равно откроется и сервер запустится.
    """
    for candidate in (lang, _FALLBACK_LANG):
        path = _LOCALES_DIR / f"{candidate}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, str)}
    return {}


def _find_stepik_config(start: Path) -> Path | None:
    """Найти ``stepik_config.json``: от ``start`` вверх, затем в домашней папке.

    Поиск вверх — тот же паттерн, что у ``config._find_pyproject``: лаунчер
    запускают ярлыком, и cwd тогда — папка ярлыка или домашняя, а не то место,
    где пользователь работает с задачами.
    """
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        path = candidate / _STEPIK_CONFIG_NAME
        if path.is_file():
            return path
    home = Path.home() / _STEPIK_CONFIG_NAME
    return home if home.is_file() else None


def default_workdir(cwd: Path | None = None) -> Path:
    """Рабочая папка по умолчанию: настроенная папка задач, иначе cwd (issue #823).

    Прежде окно всегда стартовало в cwd. Через Windows-ярлык это каталог
    ярлыка или домашняя папка: задач там нет, а веб-интерфейс ещё и конфайнит
    пути рабочей папкой — значит скачанные задачи давали 403, и первокурсник,
    ради которого лаунчер и сделан, видел пустой интерфейс.

    Выбирается папка **с** ``stepik_config.json``, если настроенный ``root_dir``
    лежит внутри неё (обычный случай — относительный ``StepikTasks``): так и
    задачи видны, и панель загрузчика продолжает находить свой конфиг. Если
    ``root_dir`` уводит наружу — берётся он сам: задачи важнее панели.
    """
    base = cwd if cwd is not None else Path.cwd()
    config = _find_stepik_config(base)
    if config is None:
        return base
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return base
    raw = str(data.get("root_dir") or "").strip() if isinstance(data, dict) else ""
    if not raw:
        return config.parent
    root = Path(raw)
    root = root if root.is_absolute() else config.parent / root
    if not root.is_dir():
        return config.parent
    return config.parent if root.resolve().is_relative_to(config.parent) else root


def count_tasks(workdir: Path, *, max_depth: int = _TASK_SCAN_DEPTH) -> tuple[int, int]:
    """Сколько папок задач видно в рабочей папке и сколько из них с тестами.

    Возвращает пару ``(всего, с тестами)``. Папка задачи — та, где лежит
    ``meta.json`` (его кладёт загрузчик) или подпапка ``tests``.

    Нужна, чтобы промах с папкой был виден сразу («найдено задач: 0»), а не
    после открытия пустого веб-интерфейса. Обход ограничен глубиной: рабочей
    папкой может оказаться домашняя, и полный скан диска в GUI недопустим.

    issue #1018: раньше считались только папки с ``tests``, поэтому свежескачанный
    шаг без публичных тестов давал «Найдено задач: 0» — как будто скачивание не
    сработало. Два числа вместо одного отличают «ничего не скачано» от «скачано,
    но проверять пока нечем».
    """
    if not workdir.is_dir():
        return 0, 0
    tasks = 0
    with_tests = 0
    stack: list[tuple[Path, int]] = [(workdir, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = [entry for entry in current.iterdir() if entry.is_dir()]
        except OSError:
            continue
        has_tests = any(child.name == "tests" for child in children)
        if has_tests:
            tasks += 1
            with_tests += 1
        elif (current / "meta.json").is_file():
            tasks += 1
        if depth < max_depth:
            stack.extend((child, depth + 1) for child in children if not child.name.startswith("."))
    return tasks, with_tests


def _print(text: str) -> None:
    """Печать статусной строки через rich (markup off — безопасно для путей/URL)."""
    if _RICH and _console is not None:
        _console.print(text, markup=False)
    else:
        print(text)


def build_server_command(port: int, *, sandbox: bool, workdir: Path) -> list[str]:
    """Собрать команду запуска web-сервера как отдельного процесса (issue #661).

    Сервер поднимается через уже готовый CLI-путь
    ``python -m stepik_grader --serve``, поэтому вариант «с изоляцией» — это
    просто наличие ``--sandbox`` в НОВОМ процессе: никакой мутации глобального runner'а
    в процессе лаунчера и никакой утечки состояния между перезапусками.

    ``workdir`` пробрасывается как ``--root`` (рабочая директория/конфайнмент
    путей сервера). Интерпретатор — ``sys.executable`` (инвариант CLAUDE.md),
    не строковый ``"python"``.
    """
    cmd = [
        sys.executable,
        "-m",
        "stepik_grader",
        "--serve",
        "--port",
        str(port),
        "--root",
        str(workdir),
    ]
    if sandbox:
        cmd.append("--sandbox")
    return cmd


def port_available(port: int, *, host: str = DEFAULT_HOST) -> bool:
    """Свободен ли TCP-порт на ``host`` — проактивная проверка «порт занят».

    Пытается забиндиться на ``(host, port)`` БЕЗ ``SO_REUSEADDR``: активно
    слушающий сервер даёт ``EADDRINUSE`` на всех платформах, поэтому занятый
    порт честно детектируется. Между этой проверкой и стартом сервера есть узкое
    TOCTOU-окно, но для локального однопользовательского лаунчера это приемлемо,
    а реальный отказ ``bind`` в дочернем процессе остаётся страховкой.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


class ServerState(Enum):
    """Состояние управляемого процесса web-сервера для UI-лаунчера."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass(frozen=True)
class ServerStatus:
    """Неизменяемый снимок состояния сервера для опроса из UI-потока."""

    state: ServerState = ServerState.STOPPED
    url: str = ""
    error: str = ""


class ServerController:
    """Жизненный цикл web-сервера как дочернего процесса (start/stop/snapshot).

    Тред-безопасность: состояние читается из Tk-потока через ``snapshot()``, а
    меняется из монитор-потока — весь доступ под ``_lock``. Tk-виджеты из
    монитор-потока НЕ трогаются: UI сам опрашивает ``snapshot()`` по таймеру
    (``root.after``), поэтому GUI и управление процессом развязаны.

    ``spawn`` инъектируется для тестов (по умолчанию — реальный ``subprocess``):
    тест может подменить его мини-процессом, который биндит порт («поднялся»)
    или сразу падает («отказ»), не поднимая настоящий сервер.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        spawn: Callable[[list[str]], subprocess.Popen[str]] | None = None,
    ) -> None:
        """Создать контроллер (процесс НЕ стартует до ``start()``)."""
        self._host = host
        self._spawn = spawn or self._default_spawn
        self._lock = threading.Lock()
        self._status = ServerStatus()
        self._proc: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail: list[str] = []
        self._stopping = False

    @property
    def host(self) -> str:
        """Хост, на котором ожидается сервер (для проверки порта в UI)."""
        return self._host

    def snapshot(self) -> ServerStatus:
        """Текущее состояние (тред-безопасно) — для опроса из UI по таймеру."""
        with self._lock:
            return self._status

    def start(self, port: int, *, sandbox: bool, workdir: Path) -> None:
        """Запустить сервер отдельным процессом; идемпотентно при уже активном.

        Немедленно переводит состояние в ``STARTING`` и стартует монитор-поток,
        который дождётся готовности (TCP-проба) → ``RUNNING`` либо раннего
        падения → ``ERROR`` с хвостом stderr. Не блокирует UI.
        """
        with self._lock:
            if self._status.state in (ServerState.STARTING, ServerState.RUNNING):
                return
            self._stopping = False
            self._stderr_tail = []
            self._status = ServerStatus(state=ServerState.STARTING)
        command = build_server_command(port, sandbox=sandbox, workdir=workdir)
        try:
            proc = self._spawn(command)
        except OSError as exc:
            with self._lock:
                self._status = ServerStatus(state=ServerState.ERROR, error=str(exc))
            return
        self._proc = proc
        monitor = threading.Thread(
            target=self._run_monitor, args=(proc, port), name="grader-launcher-monitor", daemon=True
        )
        monitor.start()

    def stop(self) -> None:
        """Остановить сервер (terminate → kill по таймауту). Идемпотентно."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is not None:
            self._terminate(proc)

    # -- внутреннее ---------------------------------------------------------

    def _default_spawn(self, command: list[str]) -> subprocess.Popen[str]:
        """Реальный запуск сервера: PIPE на потоки, без мигающей консоли на Win."""
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if sys.platform == "win32":
            # Дочерний процесс без своего консольного окна (лаунчер — GUI).
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        return subprocess.Popen(command, **kwargs)

    def _run_monitor(self, proc: subprocess.Popen[str], port: int) -> None:
        """Фоновая слежка: дренаж потоков + ожидание готовности/выхода."""
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(proc,), name="grader-launcher-stderr", daemon=True
        )
        self._stderr_thread.start()
        if proc.stdout is not None:
            threading.Thread(
                target=self._drain_stdout, args=(proc,), name="grader-launcher-stdout", daemon=True
            ).start()

        deadline = time.monotonic() + _READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                # Процесс завершился ДО готовности → отказ (или пользователь успел
                # нажать «Остановить»).
                self._settle_exit(proc, ready=False)
                return
            if self._probe(port):
                with self._lock:
                    if self._stopping:
                        # issue #823: выход монитора обязан быть терминальным.
                        # Прежде здесь был голый return, и остановка, попавшая
                        # между пробой и захватом лока, навсегда оставляла окно
                        # в «Запуск…»: «Остановить» уже no-op, «Запустить»
                        # заблокировано — оставалось закрыть окно.
                        self._status = ServerStatus(state=ServerState.STOPPED)
                        return
                    self._status = ServerStatus(
                        state=ServerState.RUNNING, url=f"http://{self._host}:{port}"
                    )
                proc.wait()
                self._settle_exit(proc, ready=True)
                return
            time.sleep(_PROBE_INTERVAL_S)

        # Таймаут: не поднялся и не упал сам — гасим и рапортуем отказ.
        self._terminate(proc)
        # ``_error_text`` сам берёт ``_lock`` (не-реентрантный) — считаем ДО входа
        # в блок под локом, иначе монитор-поток зависнет на повторном захвате.
        error = self._error_text(proc)
        with self._lock:
            if not self._stopping:
                self._status = ServerStatus(
                    state=ServerState.ERROR,
                    error=error or "сервер не ответил вовремя",
                )

    def _settle_exit(self, proc: subprocess.Popen[str], *, ready: bool) -> None:
        """Выставить финальное состояние после выхода процесса.

        Штатная остановка пользователем (``_stopping``) или чистый выход уже
        запущенного сервера → ``STOPPED``; неожиданный выход (в т.ч. до
        готовности) → ``ERROR`` с хвостом stderr.
        """
        with self._lock:
            stopping = self._stopping
        if stopping or (ready and proc.returncode in (0, None)):
            with self._lock:
                self._status = ServerStatus(state=ServerState.STOPPED)
            return
        # ``_error_text`` сам берёт ``_lock`` (не-реентрантный) — считаем ДО входа
        # в блок под локом, иначе монитор-поток зависнет на повторном захвате.
        error = self._error_text(proc)
        with self._lock:
            self._status = ServerStatus(
                state=ServerState.ERROR,
                error=error or f"процесс сервера завершился с кодом {proc.returncode}",
            )

    def _error_text(self, proc: subprocess.Popen[str]) -> str:
        """Хвост stderr дочернего процесса (дав дренажу дочитать)."""
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
        with self._lock:
            return "\n".join(self._stderr_tail).strip()

    def _probe(self, port: int) -> bool:
        """TCP-проба готовности: успешный connect → сервер принимает соединения."""
        try:
            with socket.create_connection((self._host, port), timeout=_PROBE_INTERVAL_S):
                return True
        except OSError:
            return False

    def _terminate(self, proc: subprocess.Popen[str]) -> None:
        """terminate, затем kill по таймауту.

        На Windows ``terminate()`` не убивает дочерние процессы грейдинга,
        которые сервер порождает под каждый прогон, — но они короткоживущие и
        имеют свой timeout; для локального лаунчера этого достаточно (полноценный
        process-tree kill — отдельная задача).
        """
        if proc.poll() is not None:
            return
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                proc.wait(timeout=_STOP_TIMEOUT_S)

    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        """Слить stderr в кольцевой хвост (не заполнить пайп + сохранить для ошибки)."""
        if proc.stderr is None:
            return
        for line in proc.stderr:
            with self._lock:
                self._stderr_tail.append(line.rstrip("\n"))
                # Держим только последние _STDERR_TAIL_LINES строк.
                del self._stderr_tail[:-_STDERR_TAIL_LINES]

    def _drain_stdout(self, proc: subprocess.Popen[str]) -> None:
        """Слить stdout (баннер сервера) — содержимое не нужно, лишь дренаж пайпа."""
        if proc.stdout is None:
            return
        for _line in proc.stdout:
            pass


def _last_line(text: str) -> str:
    """Последняя непустая строка (для статус-строки из многострочного stderr)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text


class LauncherApp:
    """tkinter-окно лаунчера: выбор запуска (простой/с изоляцией), порт, папка, старт/стоп, статус.

    Отделено от ``main`` ради тестируемости: ``create_app()`` строит окно, но не
    входит в блокирующий ``mainloop`` — smoke-тест прокачивает ``update()`` и
    проверяет виджеты. Из монитор-потока виджеты не трогаются: состояние
    сервера подтягивается опросом ``controller.snapshot()`` по ``root.after``.
    """

    def __init__(self, root: Any, controller: ServerController) -> None:
        """Построить виджеты в ``root`` и запустить периодический опрос статуса."""
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.controller = controller
        self._tk = tk
        self._opened_url = ""
        self._last_url = ""
        self._poll_id: str | None = None
        # issue #821: подписи окна — из каталога проекта; язык определяется один
        # раз при построении виджетов (переключателя языка в окне нет).
        self._messages = load_ui_messages(detect_lang())

        root.title(self._t("launcher_window_title"))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.sandbox_var = tk.BooleanVar(value=False)
        # issue #823: стартуем в настроенной папке задач, а не в cwd ярлыка.
        self.workdir_var = tk.StringVar(value=str(default_workdir()))
        self.status_var = tk.StringVar(value=self._t("launcher_status_stopped"))
        self.tasks_var = tk.StringVar(value="")
        self.workdir_var.trace_add("write", lambda *_: self._refresh_tasks_found())

        frame = ttk.Frame(root, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        # Как запустить сервер — явный выбор варианта (issue #661: вместо галки).
        # sandbox_var False = «простой» (LocalRunner, без изоляции), True = «с
        # изоляцией» (SandboxRunner). Режим изоляции задаётся ТОЛЬКО здесь/в CLI,
        # никогда из живого веб-сервера (он ставится process-global до старта).
        ttk.Label(frame, text=self._t("launcher_mode_heading")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        self.radio_simple = ttk.Radiobutton(
            frame,
            text=self._t("launcher_mode_simple"),
            variable=self.sandbox_var,
            value=False,
        )
        self.radio_simple.grid(row=1, column=0, columnspan=3, sticky="w")
        self.radio_sandbox = ttk.Radiobutton(
            frame,
            text=self._t("launcher_mode_sandbox"),
            variable=self.sandbox_var,
            value=True,
        )
        self.radio_sandbox.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 12))

        # Порт
        ttk.Label(frame, text=self._t("launcher_port")).grid(
            row=3, column=0, sticky="w", pady=(0, 8)
        )
        self.port_entry = ttk.Entry(frame, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 8))

        # Рабочая папка
        ttk.Label(frame, text=self._t("launcher_workdir")).grid(
            row=4, column=0, sticky="w", pady=(0, 8)
        )
        self.workdir_entry = ttk.Entry(frame, textvariable=self.workdir_var)
        self.workdir_entry.grid(row=4, column=1, sticky="ew", pady=(0, 8))
        self.browse_btn = ttk.Button(
            frame, text=self._t("launcher_browse"), command=self._browse_dir
        )
        self.browse_btn.grid(row=4, column=2, sticky="e", padx=(8, 0), pady=(0, 8))

        # Сколько задач видно в выбранной папке (issue #823): промах виден здесь,
        # а не после открытия пустого веб-интерфейса.
        self.tasks_label = ttk.Label(frame, textvariable=self.tasks_var)
        self.tasks_label.grid(row=5, column=1, columnspan=2, sticky="w", pady=(0, 8))
        self._refresh_tasks_found()

        # Действие + открыть в браузере
        self.action_btn = ttk.Button(frame, text=self._t("launcher_start"), command=self._on_action)
        self.action_btn.grid(row=6, column=0, sticky="w")
        self.open_btn = ttk.Button(
            frame,
            text=self._t("launcher_open_browser"),
            command=self._open_browser,
            state="disabled",
        )
        self.open_btn.grid(row=6, column=1, columnspan=2, sticky="e")

        # Статус
        self.status_label = ttk.Label(
            frame, textvariable=self.status_var, wraplength=420, justify="left"
        )
        self.status_label.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self._poll()

    def _t(self, key: str, **params: object) -> str:
        """Подпись по ключу каталога; пропавший ключ показывается как есть."""
        template = self._messages.get(key, key)
        return template.format(**params) if params else template

    def _refresh_tasks_found(self) -> None:
        """Обновить строку «найдено задач: N» под полем рабочей папки (issue #823)."""
        raw = self.workdir_var.get().strip()
        workdir = Path(raw or ".")
        if not workdir.is_dir():
            self.tasks_var.set(self._t("launcher_workdir_missing"))
            return
        tasks, with_tests = count_tasks(workdir)
        # issue #1018: пока все задачи с тестами — прежняя короткая строка;
        # расхождение показывается явно, иначе «задач 3» при нуле проверяемых
        # выглядит как обещание, которого интерфейс не выполнит.
        key = "launcher_tasks_found" if tasks == with_tests else "launcher_tasks_found_partial"
        self.tasks_var.set(self._t(key, count=tasks, with_tests=with_tests))

    def run(self) -> None:
        """Войти в блокирующий ``mainloop`` (используется ``main``, не тестами)."""
        self.root.mainloop()

    # -- обработчики --------------------------------------------------------

    def _on_action(self) -> None:
        """Кнопка Запустить/Остановить — по текущему состоянию сервера."""
        state = self.controller.snapshot().state
        if state in (ServerState.RUNNING, ServerState.STARTING):
            self.controller.stop()
        else:
            self._start()

    def _start(self) -> None:
        """Валидировать ввод, проверить порт и запустить сервер."""
        raw_port = self.port_var.get().strip()
        try:
            port = int(raw_port)
        except ValueError:
            self._set_status(self._t("launcher_port_invalid"), error=True)
            return
        if not 1 <= port <= 65535:
            self._set_status(self._t("launcher_port_range"), error=True)
            return
        workdir = Path(self.workdir_var.get().strip() or ".")
        if not workdir.is_dir():
            self._set_status(self._t("launcher_workdir_missing"), error=True)
            return
        if not port_available(port, host=self.controller.host):
            self._set_status(self._t("launcher_port_busy", port=port), error=True)
            return
        self.controller.start(port, sandbox=self.sandbox_var.get(), workdir=workdir)

    def _browse_dir(self) -> None:
        """Нативный диалог выбора рабочей папки (tkinter.filedialog)."""
        from tkinter import filedialog

        chosen = filedialog.askdirectory(
            title=self._t("launcher_browse_title"),
            initialdir=self.workdir_var.get() or str(Path.cwd()),
        )
        if chosen:
            self.workdir_var.set(chosen)

    def _open_browser(self) -> None:
        """Открыть текущий URL сервера в браузере вручную."""
        if self._last_url:
            webbrowser.open(self._last_url)

    def _on_close(self) -> None:
        """Закрытие окна: остановить опрос, погасить сервер, разрушить окно."""
        if self._poll_id is not None:
            with contextlib.suppress(self._tk.TclError):
                self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self.controller.stop()
        self.root.destroy()

    # -- отрисовка ----------------------------------------------------------

    def _poll(self) -> None:
        """Периодический опрос состояния сервера и перерисовка UI (``root.after``).

        Само-завершается, если окно уже уничтожено (иначе отложенный
        ``after``-таймер дёрнул бы ``.config`` на несуществующем виджете и
        уронил бы TclError — важно и для тестов с ``Toplevel``, и когда окно
        закрыли в обход ``_on_close``).
        """
        try:
            if not self.root.winfo_exists():
                return
        except self._tk.TclError:
            return
        self._render(self.controller.snapshot())
        self._poll_id = self.root.after(250, self._poll)

    def _render(self, status: ServerStatus) -> None:
        """Привести кнопки/поля/статус в соответствие снимку состояния."""
        state = status.state
        if state == ServerState.RUNNING:
            self.action_btn.config(text=self._t("launcher_stop"))
            self.open_btn.config(state="normal")
            self._set_inputs_enabled(False)
            self._set_status(self._t("launcher_status_running", url=status.url))
            if status.url and status.url != self._opened_url:
                # Авто-открытие браузера один раз на переход в RUNNING.
                self._opened_url = status.url
                self._last_url = status.url
                webbrowser.open(status.url)
        elif state == ServerState.STARTING:
            self.action_btn.config(text=self._t("launcher_stop"))
            self.open_btn.config(state="disabled")
            self._set_inputs_enabled(False)
            self._set_status(self._t("launcher_status_starting"))
        elif state == ServerState.ERROR:
            self.action_btn.config(text=self._t("launcher_start"))
            self.open_btn.config(state="disabled")
            self._set_inputs_enabled(True)
            self._opened_url = ""
            # Текст ошибки — из stderr сервера: он приходит на языке сервера и
            # переводу здесь не подлежит; переводится только запасная подпись.
            self._set_status(
                _last_line(status.error) or self._t("launcher_status_error"), error=True
            )
        else:  # STOPPED
            self.action_btn.config(text=self._t("launcher_start"))
            self.open_btn.config(state="disabled")
            self._set_inputs_enabled(True)
            self._opened_url = ""
            self._set_status(self._t("launcher_status_stopped"))

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Блокировать порт/папку/sandbox во время работы сервера."""
        state = "normal" if enabled else "disabled"
        self.port_entry.config(state=state)
        self.workdir_entry.config(state=state)
        self.browse_btn.config(state=state)
        self.radio_simple.config(state=state)
        self.radio_sandbox.config(state=state)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        """Обновить статус-строку (красный цвет при ошибке)."""
        self.status_var.set(text)
        self.status_label.config(foreground="#c0392b" if error else "")


def create_app(controller: ServerController | None = None, *, root: Any = None) -> LauncherApp:
    """Создать окно-лаунчер (без входа в ``mainloop``).

    ``root`` — использовать существующий Tk/Toplevel вместо нового окна (для
    встраивания и тестов); по умолчанию создаётся собственный ``tk.Tk()``.

    Бросает ``tkinter.TclError`` в headless-окружении без дисплея (как
    ``_pick_path_via_dialog`` в cli/interactive.py) и ``ImportError``, если
    ``tkinter`` не собран в этом Python — вызывающая сторона (``main``)
    отрабатывает оба случая понятным сообщением.
    """
    import tkinter as tk

    if root is None:
        root = tk.Tk()
    return LauncherApp(root, controller or ServerController())


def main() -> None:
    """Точка входа GUI-лаунчера (``python -m stepik_grader.launcher``, gui-script).

    В headless-окружении/сборке без ``tkinter`` — не падает трейсбеком, а
    подсказывает эквивалентную CLI-команду и выходит с кодом 1.
    """
    try:
        import tkinter
    except ImportError:
        _print("tkinter недоступен в этой сборке Python. Запустите веб-интерфейс командой:")
        _print(f"    {sys.executable} -m stepik_grader --serve")
        raise SystemExit(1) from None

    try:
        app = create_app()
    except tkinter.TclError:
        _print("Нет графического дисплея (headless). Запустите веб-интерфейс командой:")
        _print(f"    {sys.executable} -m stepik_grader --serve")
        raise SystemExit(1) from None

    app.run()


if __name__ == "__main__":
    main()
