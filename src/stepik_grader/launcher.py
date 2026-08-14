"""launcher.py — GUI-лаунчер веб-интерфейса без командной строки (issue #661).

Application-слой, entry point (``python -m stepik_grader.launcher`` и
gui-script ``stepik-grader-gui``). Небольшое tkinter-окно: выбор варианта
запуска (простой / с изоляцией ``--sandbox``), запись истории, язык, порт,
рабочая папка, предпросмотр команды, кнопка Запустить/Остановить, статус со
ссылкой и авто-открытие браузера.

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
--port/--root/--lang/--history`` и honest-fail на недоступном backend'е через
``SandboxUnavailableError``). Из проекта модуль тянет только
``stdio_encoding``, ``config.workspace_root`` (общий корень настроек) и
``core/user_settings`` (память выбора между запусками, issue #1133: свой формат
хранения был бы вторым источником истины рядом с меню и вебом, ADR-0012) —
ядро грейдера в процесс окна не импортируется.

``tkinter`` импортируется ЛЕНИВО (внутри GUI-путей), поэтому GUI-free ядро
(``build_server_command``/``port_available``/``ServerController``) импортируется
и тестируется в том числе в headless-окружении без дисплея.

Threat model: по умолчанию изоляция ВЫКЛЮЧЕНА (как и весь остальной проект —
``LocalRunner`` без OS-sandbox). Режим изоляции меняется только здесь/через
CLI, никогда из самого веб-интерфейса.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
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

from stepik_grader.config import workspace_root
from stepik_grader.core import settings_resolver
from stepik_grader.core.user_settings import (
    LaunchChoice,
    ProfileLimitError,
    default_settings_path,
    delete_launch_profile,
    load_settings,
    save_fields,
    save_launch_profile,
)
from stepik_grader.stdio_encoding import force_utf8_stdio

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "LANG_ENV_VAR",
    "LaunchDefaults",
    "LauncherApp",
    "ServerController",
    "ServerState",
    "ServerStatus",
    "build_arg_parser",
    "build_server_command",
    "count_tasks",
    "create_app",
    "default_workdir",
    "detect_lang",
    "initial_launch_values",
    "launch_choice_from_profile",
    "load_ui_messages",
    "main",
    "next_free_port",
    "our_server_on",
    "port_available",
    "profile_names",
    "remember_launch_choice",
    "remember_profile_name",
    "remembered_launch_choice",
    "remembered_profile_name",
    "resolve_version",
    "serve_without_gui",
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
    # issue #1135 (LNCH-1-04): английский — только когда локаль ДЕЙСТВИТЕЛЬНО
    # английская. Прежнее «всё, что не ru, — en» превращало `LANG=C` (обычное
    # дело в CI, контейнерах и по ssh) и любую третью локаль в английское окно,
    # хотя документация обещает русский fallback.
    if system.startswith("ru"):
        return "ru"
    if system.startswith("en"):
        return "en"
    return _FALLBACK_LANG


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
    """Рабочая папка по умолчанию: настроенная папка задач, иначе корень проекта.

    issue #823: прежде окно всегда стартовало в cwd. Через Windows-ярлык это
    каталог ярлыка или домашняя папка: задач там нет, а веб-интерфейс ещё и
    конфайнит пути рабочей папкой — значит скачанные задачи давали 403, и
    первокурсник, ради которого лаунчер и сделан, видел пустой интерфейс.

    Выбирается папка **с** ``stepik_config.json``, если настроенный ``root_dir``
    лежит внутри неё (обычный случай — относительный ``StepikTasks``): так и
    задачи видны, и панель загрузчика продолжает находить свой конфиг. Если
    ``root_dir`` уводит наружу — берётся он сам: задачи важнее панели.

    issue #1132: фолбэк — не голый cwd, а ``workspace_root()``, тот же корень
    настроек, от которого резолвятся ``pyproject.toml`` и
    ``.grader_settings.json`` (issue #1009). Прежний cwd означал, что запуск из
    подпапки проекта открывал окно в подпапке, а не в проекте, — и лечило это
    только наличие ``stepik_config.json``, то есть сценарий «задачи скачаны
    загрузчиком». Своя папка с задачами, собранная руками, в него не попадала.
    """
    base = cwd if cwd is not None else Path.cwd()
    fallback = workspace_root(base)
    config = _find_stepik_config(base)
    if config is None:
        return fallback
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return fallback
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


def _print_err(text: str) -> None:
    """То же, но в stderr — единственный канал headless-ветки (issue #1134).

    Точка входа объявлена в ``[project.gui-scripts]``, а такой скрипт на Windows
    запускается через ``pythonw.exe`` **без консоли**: писать в ``stdout``
    буквально некуда. ``stderr`` переживает больше сценариев (перенаправление в
    файл, запуск из терминала, ``2>&1``), но и он не гарантирован — поэтому
    headless-ветка не ограничивается сообщением, а делает работу и отдаёт
    осмысленный код возврата.
    """
    if _RICH and _console is not None:
        Console(stderr=True).print(text, markup=False)
    else:
        print(text, file=sys.stderr)


def build_server_command(
    port: int,
    *,
    sandbox: bool,
    workdir: Path,
    lang: str | None = None,
    record_history: bool | None = None,
) -> list[str]:
    """Собрать команду запуска web-сервера как отдельного процесса (issue #661).

    Сервер поднимается через уже готовый CLI-путь
    ``python -m stepik_grader --serve``, поэтому вариант «с изоляцией» — это
    просто наличие ``--sandbox`` в НОВОМ процессе: никакой мутации глобального runner'а
    в процессе лаунчера и никакой утечки состояния между перезапусками.

    ``workdir`` пробрасывается как ``--root`` (рабочая директория/конфайнмент
    путей сервера). Интерпретатор — ``sys.executable`` (инвариант CLAUDE.md),
    не строковый ``"python"``.

    ``lang`` и ``record_history`` (issue #1131) — выбор пользователя в окне.
    ``None`` означает «не выбирал»: флаг не добавляется, и значение резолвится
    сервером по обычной лестнице (явный флаг → ``.grader_settings.json`` →
    ``pyproject.toml`` → дефолт поверхности, ADR-0012). Именно поэтому
    параметры трёхзначные, а не ``bool``/``str`` с дефолтом: запекать текущий
    дефолт в команду нельзя — тогда правка ``pyproject.toml`` перестала бы
    действовать, а сохранённый профиль заморозил бы дефолты дня своего
    создания.
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
    if lang is not None:
        cmd += ["--lang", lang]
    if record_history is not None:
        cmd.append("--history" if record_history else "--no-history")
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


def our_server_on(port: int, *, host: str = DEFAULT_HOST, timeout: float = 1.0) -> bool:
    """Стоит ли на ``port`` наш собственный веб-интерфейс (issue #1134).

    Занятый порт чаще всего означает не чужую программу, а наш же сервер с
    прошлого запуска — и тогда правильное действие не «смени порт», а «открой
    его». Отличаем по маркеру, который страница несёт всегда:
    ``data-sandbox`` инжектится сервером в HTML (issue #565).

    Любая сетевая ошибка — «не наш»: лаунчер не должен падать из-за пробы, а
    ошибочное «наш» хуже ошибочного «чужой» (открыли бы чужую страницу).
    """
    import http.client

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/")
        response = conn.getresponse()
        if response.status != 200:
            return False
        return b"data-sandbox" in response.read(4096)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def next_free_port(start: int, *, host: str = DEFAULT_HOST, attempts: int = 20) -> int | None:
    """Ближайший свободный порт начиная со ``start`` (issue #1134).

    Лаунчер умеет проверять порты — значит на «порт занят» он обязан предлагать
    конкретный следующий, а не заставлять пользователя угадывать. ``None``, если
    все ``attempts`` подряд заняты: гадать дальше бессмысленно, там что-то
    системное.
    """
    for candidate in range(start, min(start + attempts, 65536)):
        if port_available(candidate, host=host):
            return candidate
    return None


def serve_without_gui(
    messages: dict[str, str],
    *,
    lang: str | None = None,
    port: int = DEFAULT_PORT,
    workdir: Path | None = None,
) -> int:
    """Поднять веб-интерфейс без окна и вернуть код возврата (issue #1134).

    Прежде отсутствие дисплея или ``tkinter`` заканчивалось советом набрать
    ``python -m stepik_grader --serve`` — командой, которую лаунчер знает
    целиком и может выполнить сам. Хуже: на Windows gui-script идёт через
    ``pythonw.exe`` без консоли, поэтому совет уходил в никуда, а пользователь
    видел молча закрывшееся окно.

    Порт выбирается тем же способом, что и в окне: занят чужим — берётся
    ближайший свободный (``next_free_port``); занят НАШИМ сервером с прошлого
    запуска — второй не поднимается, печатается адрес уже работающего и
    возвращается ``0``. Свободного порта не нашлось — ``1``: это уже не та
    ситуация, в которой можно угадать за пользователя.

    Args:
        messages: каталог локали для сообщений (окно ещё не создано).
        lang: язык интерфейса сервера; ``None`` — «не выбирал» (ADR-0012).
        port: желаемый порт; занятый заменяется ближайшим свободным.
        workdir: рабочая папка сервера; ``None`` — ``default_workdir()``.

    Returns:
        Код возврата дочернего процесса сервера, ``0`` (сервер уже работал)
        или ``1`` (свободный порт не найден).
    """

    def _t(key: str, **params: object) -> str:
        template = messages.get(key, key)
        return template.format(**params) if params else template

    if our_server_on(port):
        _print_err(_t("launcher_headless_already_running", url=f"http://{DEFAULT_HOST}:{port}/"))
        return 0

    chosen = port if port_available(port) else next_free_port(port)
    if chosen is None:
        _print_err(_t("launcher_headless_no_free_port", port=port))
        return 1

    command = build_server_command(
        chosen,
        sandbox=False,
        workdir=workdir if workdir is not None else default_workdir(),
        lang=lang,
    )
    _print_err(_t("launcher_headless_starting", url=f"http://{DEFAULT_HOST}:{chosen}/"))
    # Дочерний процесс держит консоль до Ctrl+C — как и `--serve`, запущенный
    # руками. Код возврата пробрасывается: молчаливый 0 при упавшем сервере
    # был бы тем же самым «сообщение вместо действия», от которого уходим.
    return subprocess.call(command)


def launcher_settings_path() -> Path:
    """Файл настроек, в котором живёт память окна (issue #1133).

    Якорь — папка, вычисленная ``default_workdir()`` при СТАРТЕ, а не та, что
    выбрана в окне: иначе смена рабочей папки уводила бы память в другой файл,
    и следующий запуск открывал бы окно с нуля — ровно то, что issue и чинит.
    Расположение файла — ADR-0012: настройки, выбираемые в лаунчере, живут в
    ``.grader_settings.json`` рядом с историей, которой они управляют.
    """
    return default_settings_path(default_workdir())


def remembered_launch_choice() -> LaunchChoice:
    """Прочитать сохранённый выбор окна; нет файла или мусор — пустой выбор."""
    return load_settings(launcher_settings_path()).last_launch or LaunchChoice()


def profile_names() -> list[str]:
    """Имена сохранённых профилей по алфавиту (issue #1133).

    Порядок именно алфавитный, а не порядок создания: список читают глазами, и
    «где-то в середине» — худший способ искать своё имя.
    """
    return sorted(load_settings(launcher_settings_path()).launch_profiles)


def remembered_profile_name() -> str:
    """Профиль, выбранный в прошлый раз; пустая строка — «свой набор».

    Пустая строка, а не ``None``: значение уходит прямо в ``StringVar`` окна, а
    там отсутствие выбора и есть пустая строка.
    """
    settings = load_settings(launcher_settings_path())
    name = settings.last_profile or ""
    # Профиль мог быть удалён другим окном или правкой файла — не показываем
    # имя, за которым уже ничего нет.
    return name if name in settings.launch_profiles else ""


def launch_choice_from_profile(name: str) -> LaunchChoice | None:
    """Сохранённый выбор по имени профиля; ``None`` — профиля нет."""
    return load_settings(launcher_settings_path()).launch_profiles.get(name.strip())


@dataclass(frozen=True)
class LaunchDefaults:
    """С чем открывается окно: запомненное, где есть, иначе обычные дефолты."""

    port: int
    sandbox: bool
    workdir: Path
    lang: str
    record_history: bool | None


def initial_launch_values(
    remembered: LaunchChoice,
    *,
    lang_flag: str | None = None,
    fallback_dir: Path | None = None,
) -> LaunchDefaults:
    """Начальные значения полей окна по запомненному выбору (issue #1133).

    Отдельно от ``LauncherApp.__init__``, потому что это единственная часть
    восстановления, где есть решения: приоритет флага, откат исчезнувшей папки,
    отличие «не выбирал» от «выключено». В конструкторе окна её проверял бы
    только тот, у кого есть дисплей, — то есть никто из CI.

    Args:
        remembered: сохранённый выбор (пустой, если окно ещё не запускали).
        lang_flag: язык из ``--lang``; сильнее запомненного — флаг это решение
            «на сейчас», а память лишь предлагает прошлое.
        fallback_dir: чем заменить отсутствующую/исчезнувшую папку; по
            умолчанию ``default_workdir()``.

    Returns:
        ``LaunchDefaults`` — ровно то, что подставляется в поля окна.
    """
    fallback = fallback_dir if fallback_dir is not None else default_workdir()
    workdir = remembered.workdir
    # Исчезнувшая папка (внешний диск, переезд проекта) откатывается к дефолту:
    # окно, открытое на несуществующем пути, показало бы ноль задач и повод
    # думать, что пропали они, а не папка.
    if workdir is None or not workdir.is_dir():
        workdir = fallback
    return LaunchDefaults(
        port=remembered.port or DEFAULT_PORT,
        sandbox=bool(remembered.sandbox),
        workdir=workdir,
        lang=lang_flag or remembered.lang or detect_lang(),
        # `None` — «не выбирал», отдельное значение «унаследовать»: не то же
        # самое, что выключено (ADR-0012).
        record_history=remembered.record_history,
    )


def remember_profile_name(name: str) -> None:
    """Запомнить, какой профиль выбран сейчас (issue #1133, best-effort)."""
    with contextlib.suppress(OSError, ValueError):
        save_fields(launcher_settings_path(), last_profile=name.strip() or None)


def remember_launch_choice(choice: LaunchChoice) -> None:
    """Сохранить выбор окна, не тронув остальные ключи файла (issue #1133).

    Пишется одним полем через ``save_fields``: файл общий с веб-интерфейсом и
    интерактивным меню, а те могли изменить его, пока окно было открыто.
    Запись целого снапшота вернула бы на диск их состояние часовой давности —
    включая отозванное согласие на отправку кода AI-провайдеру. Гонку двух
    одновременных писателей это не закрывает (``LNCH-3-06``), но окно сузилось
    до времени одной записи.

    Best-effort: неудачная запись памяти окна не должна мешать запуску сервера,
    ради которого пользователь сюда и пришёл.
    """
    with contextlib.suppress(OSError, ValueError):
        save_fields(launcher_settings_path(), last_launch=choice)


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

    def start(
        self,
        port: int,
        *,
        sandbox: bool,
        workdir: Path,
        lang: str | None = None,
        record_history: bool | None = None,
    ) -> None:
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
        command = build_server_command(
            port,
            sandbox=sandbox,
            workdir=workdir,
            lang=lang,
            record_history=record_history,
        )
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

    def __init__(self, root: Any, controller: ServerController, *, lang: str | None = None) -> None:
        """Построить виджеты в ``root`` и запустить периодический опрос статуса.

        ``lang`` (issue #1135) — язык окна, если он выбран явно флагом
        ``--lang``; ``None`` означает «определить как раньше» (переменная
        окружения → системная локаль → русский).
        """
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.controller = controller
        self._tk = tk
        self._opened_url = ""
        self._last_url = ""
        self._poll_id: str | None = None
        # issue #1133: окно открывается там, где его закрыли. Раньше каждый
        # запуск начинался с нуля — и пользователь, работающий с изоляцией,
        # включал её заново каждый раз, пока однажды не забывал. Тихая потеря
        # настройки безопасности, а не просто неудобство.
        initial = initial_launch_values(remembered_launch_choice(), lang_flag=lang)
        # issue #821: подписи окна — из каталога проекта. issue #1131: язык
        # больше не определяется раз и навсегда — в окне есть переключатель,
        # и он же задаёт язык страницы дочернего сервера.
        self._lang = initial.lang
        self._messages = load_ui_messages(self._lang)

        root.title(self._t("launcher_window_title"))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.port_var = tk.StringVar(value=str(initial.port))
        self.sandbox_var = tk.BooleanVar(value=initial.sandbox)
        # issue #823: стартуем в настроенной папке задач, а не в cwd ярлыка.
        # issue #1133: запомненная папка сильнее вычисленной — её выбрал человек.
        self.workdir_var = tk.StringVar(value=str(initial.workdir))
        self.status_var = tk.StringVar(value=self._t("launcher_status_stopped"))
        self.tasks_var = tk.StringVar(value="")
        # issue #1131: язык и запись истории — выбор пользователя ДО старта.
        # Язык окна и есть язык сервера: два разных было бы странно, поэтому
        # переключатель один и меняет обоих.
        self.lang_var = tk.StringVar(value=self._lang)
        # «Не выбирал» отдельно от «выбрал то же, что дефолт» (ADR-0012):
        # None → флаг не попадёт в команду, значение резолвит сервер.
        # Значение подставляется после создания Combobox — оно из каталога.
        self.history_var = tk.StringVar(value="")
        self.command_var = tk.StringVar(value="")
        # issue #1133: имя выбранного профиля. Пустая строка — «свой набор»:
        # состояние, в котором поля не соответствуют ни одному сохранённому.
        self.profile_var = tk.StringVar(value=remembered_profile_name())
        self.workdir_var.trace_add("write", lambda *_: self._refresh_tasks_found())
        for var in (self.port_var, self.sandbox_var, self.workdir_var, self.history_var):
            var.trace_add("write", lambda *_: self._refresh_command_preview())

        # issue #1136: две вкладки вместо одного экрана. «Запуск» — то, что
        # выбирают каждый раз; «Дополнительно» — то, что до сих пор правилось
        # только в pyproject.toml, которого у поставившего через pipx нет вовсе.
        # Разделение, а не общий список: иначе редкие настройки прогона отжимают
        # вниз кнопку «Запустить», ради которой окно и открывают.
        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        frame = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(frame, text=self._t("launcher_tab_run"))
        frame.columnconfigure(1, weight=1)

        # Как запустить сервер — явный выбор варианта (issue #661: вместо галки).
        # sandbox_var False = «простой» (LocalRunner, без изоляции), True = «с
        # изоляцией» (SandboxRunner). Режим изоляции задаётся ТОЛЬКО здесь/в CLI,
        # никогда из живого веб-сервера (он ставится process-global до старта).
        # issue #1133 (шаг 2): профиль — первая строка окна намеренно. Он ЗАДАЁТ
        # остальные поля, и стоя ниже он бы стирал только что введённое: человек
        # заполняет форму, потом применяет профиль — и его ввод исчезает.
        ttk.Label(frame, text=self._t("launcher_profile")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.profile_box = ttk.Combobox(
            frame,
            textvariable=self.profile_var,
            state="readonly",
            width=24,
            values=self._profile_values(),
        )
        self.profile_box.grid(row=0, column=1, sticky="w", pady=(0, 8))
        self.profile_box.bind("<<ComboboxSelected>>", lambda _event: self._on_profile_selected())
        self.profile_save_btn = ttk.Button(
            frame, text=self._t("launcher_profile_save"), command=self._on_save_profile
        )
        self.profile_save_btn.grid(row=0, column=2, sticky="e", padx=(8, 0), pady=(0, 8))
        self.profile_delete_btn = ttk.Button(
            frame, text=self._t("launcher_profile_delete"), command=self._on_delete_profile
        )
        self.profile_delete_btn.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(0, 8))

        ttk.Label(frame, text=self._t("launcher_mode_heading")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        self.radio_simple = ttk.Radiobutton(
            frame,
            text=self._t("launcher_mode_simple"),
            variable=self.sandbox_var,
            value=False,
        )
        self.radio_simple.grid(row=3, column=0, columnspan=3, sticky="w")
        self.radio_sandbox = ttk.Radiobutton(
            frame,
            text=self._t("launcher_mode_sandbox"),
            variable=self.sandbox_var,
            value=True,
        )
        self.radio_sandbox.grid(row=4, column=0, columnspan=3, sticky="w")

        # issue #1131 (LNCH-1-02): последствие выбора названо в точке выбора.
        # «С изоляцией» отключает пошаговый трейс (core/tracer.py) — раньше
        # пользователь включал защиту и терял функцию, не понимая почему.
        self.sandbox_note = ttk.Label(
            frame, text=self._t("launcher_mode_sandbox_note"), wraplength=420, justify="left"
        )
        self.sandbox_note.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 12))

        # issue #1131 (LNCH-1-01): запись истории — тумблер, а не молчаливый
        # дефолт. Это настройка приватности: решения студента ложатся в
        # локальную базу, и отказаться от этого он должен уметь до старта.
        ttk.Label(frame, text=self._t("launcher_history")).grid(
            row=6, column=0, sticky="w", pady=(0, 8)
        )
        self.history_box = ttk.Combobox(
            frame,
            textvariable=self.history_var,
            state="readonly",
            width=28,
            values=[
                self._t("launcher_history_inherit"),
                self._t("launcher_history_on"),
                self._t("launcher_history_off"),
            ],
        )
        self.history_box.grid(row=6, column=1, columnspan=2, sticky="w", pady=(0, 8))
        # issue #1133: запомненный выбор восстанавливается; «не выбирал»
        # (``None``) — это отдельное значение «унаследовать», а не «выключено».
        self.history_box.set(self._history_label(initial.record_history))

        # issue #1131 (LNCH-2-05): язык выбирается на старте и доезжает до
        # браузера — до этого фикса страница всегда открывалась на русском.
        ttk.Label(frame, text=self._t("launcher_lang")).grid(
            row=7, column=0, sticky="w", pady=(0, 8)
        )
        self.lang_box = ttk.Combobox(
            frame,
            textvariable=self.lang_var,
            state="readonly",
            width=12,
            values=list(_SUPPORTED_LANGS),
        )
        self.lang_box.grid(row=7, column=1, columnspan=2, sticky="w", pady=(0, 8))
        self.lang_box.bind("<<ComboboxSelected>>", lambda _event: self._on_lang_changed())

        # Порт
        ttk.Label(frame, text=self._t("launcher_port")).grid(
            row=8, column=0, sticky="w", pady=(0, 8)
        )
        self.port_entry = ttk.Entry(frame, textvariable=self.port_var, width=10)
        self.port_entry.grid(row=8, column=1, columnspan=2, sticky="w", pady=(0, 8))

        # Рабочая папка
        ttk.Label(frame, text=self._t("launcher_workdir")).grid(
            row=9, column=0, sticky="w", pady=(0, 8)
        )
        self.workdir_entry = ttk.Entry(frame, textvariable=self.workdir_var)
        self.workdir_entry.grid(row=9, column=1, sticky="ew", pady=(0, 8))
        self.browse_btn = ttk.Button(
            frame, text=self._t("launcher_browse"), command=self._browse_dir
        )
        self.browse_btn.grid(row=9, column=2, sticky="e", padx=(8, 0), pady=(0, 8))

        # Сколько задач видно в выбранной папке (issue #823): промах виден здесь,
        # а не после открытия пустого веб-интерфейса.
        self.tasks_label = ttk.Label(frame, textvariable=self.tasks_var)
        self.tasks_label.grid(row=10, column=1, columnspan=2, sticky="w", pady=(0, 8))
        self._refresh_tasks_found()

        # issue #1131 (LNCH-1-07): что именно включится — видно ДО нажатия.
        # Веб-онбординг обещал галку sandbox в лаунчере, которой там не было;
        # предъявленная команда делает обещание проверяемым, а заодно учит
        # пользователя тому же запуску из терминала.
        self.command_label = ttk.Label(
            frame, textvariable=self.command_var, wraplength=420, justify="left"
        )
        self.command_label.grid(row=11, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._refresh_command_preview()

        # Действие + открыть в браузере
        self.action_btn = ttk.Button(frame, text=self._t("launcher_start"), command=self._on_action)
        self.action_btn.grid(row=12, column=0, sticky="w")
        self.open_btn = ttk.Button(
            frame,
            text=self._t("launcher_open_browser"),
            command=self._open_browser,
            state="disabled",
        )
        self.open_btn.grid(row=12, column=1, columnspan=2, sticky="e")

        # Статус
        self.status_label = ttk.Label(
            frame, textvariable=self.status_var, wraplength=420, justify="left"
        )
        self.status_label.grid(row=13, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self._build_advanced_tab()

        self._poll()

    # -- вкладка «Дополнительно» (issue #1136) --------------------------------

    def _build_advanced_tab(self) -> None:
        """Построить вкладку настроек прогона по описаниям из ядра.

        Контролы не перечислены здесь руками: их состав задаёт
        ``settings_resolver.ADVANCED_SETTINGS``, и проверить его можно тестом,
        а не глазами на машине с дисплеем.
        """
        from tkinter import ttk

        self.setting_vars: dict[str, Any] = {}
        self.setting_state_vars: dict[str, Any] = {}
        self.setting_labels: dict[str, Any] = {}
        self.setting_hints: dict[str, Any] = {}
        self.setting_buttons: dict[str, list[Any]] = {}
        self.setting_widgets: dict[str, Any] = {}

        outer = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(outer, text=self._t("launcher_tab_advanced"))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        self.advanced_intro = ttk.Label(
            outer, text=self._t("settings_intro"), wraplength=520, justify="left"
        )
        self.advanced_intro.grid(row=0, column=0, sticky="w", pady=(0, 12))

        # Семнадцать настроек в один экран не помещаются, и первым решением была
        # прокрутка через Canvas. Она вешала окно: внутренний фрейм тянется по
        # ширине canvas, canvas подгоняет размер под фрейм, и `<Configure>`
        # гоняют друг друга между двумя значениями — `update()` не возвращается.
        # Сравнение «писать только при изменении» такую осцилляцию не ловит,
        # значения-то каждый раз разные. Поймано на macOS в CI дважды; на Linux
        # не видно — там нет дисплея и тесты окна скипаются.
        #
        # Группы уже есть, поэтому вместо прокрутки — вложенные вкладки по
        # блокам: в самом большом четыре настройки, любой экран вмещает. Заодно
        # исчезает длинный список, в котором нужную строку искали глазами.
        self.advanced_groups = ttk.Notebook(outer)
        self.advanced_groups.grid(row=1, column=0, sticky="nsew")

        self._group_frames: dict[str, Any] = {}
        for group in settings_resolver.ADVANCED_GROUPS:
            items = settings_resolver.advanced_settings(group)
            if not items:
                continue
            page = ttk.Frame(self.advanced_groups, padding=(0, 8))
            page.columnconfigure(1, weight=1)
            self.advanced_groups.add(page, text=self._t(f"settings_group_{group}"))
            self._group_frames[group] = page
            row = 0
            if group == "unsafe":
                row = self._build_unsafe_gate(page, row)
            for item in items:
                row = self._build_setting_row(page, item, row)

    def _build_unsafe_gate(self, body: Any, row: int) -> int:
        """Предупреждение и галка, открывающая правку квот песочницы (issue #1136)."""
        from tkinter import ttk

        self.unsafe_warning = ttk.Label(
            body, text=self._t("settings_unsafe_warning"), wraplength=500, justify="left"
        )
        self.unsafe_warning.grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.unsafe_var = self._tk.BooleanVar(value=False)
        self.unsafe_check = ttk.Checkbutton(
            body,
            text=self._t("settings_unsafe_unlock"),
            variable=self.unsafe_var,
            command=self._on_unsafe_toggled,
        )
        self.unsafe_check.grid(row=row + 1, column=0, columnspan=4, sticky="w", pady=(0, 8))
        return row + 2

    def _build_setting_row(self, body: Any, item: Any, row: int) -> int:
        """Подпись, контрол, кнопки и строка происхождения одной настройки."""
        from tkinter import ttk

        name = item.name
        label = ttk.Label(body, text=self._t(f"setting_{name}"))
        label.grid(row=row, column=0, sticky="w", pady=(0, 2))
        self.setting_labels[name] = label

        view = settings_resolver.describe_setting(name)
        if item.kind == "bool":
            var: Any = self._tk.BooleanVar(value=bool(view.value))
            control: Any = ttk.Checkbutton(body, variable=var)
        elif item.kind == "choice":
            var = self._tk.StringVar(value=self._setting_display(view.value))
            control = ttk.Combobox(
                body, textvariable=var, state="readonly", width=16, values=list(item.choices)
            )
        else:
            var = self._tk.StringVar(value=self._setting_display(view.value))
            control = ttk.Entry(body, textvariable=var, width=24)
        control.grid(row=row, column=1, sticky="w", pady=(0, 2))
        self.setting_vars[name] = var
        self.setting_widgets[name] = control

        # partial, а не lambda с дефолтным аргументом: замыкание по переменной
        # цикла отдало бы всем кнопкам последнее имя.
        apply_btn = ttk.Button(
            body,
            text=self._t("settings_apply"),
            command=functools.partial(self._apply_setting, name),
        )
        apply_btn.grid(row=row, column=2, sticky="e", padx=(8, 0))
        reset_btn = ttk.Button(
            body,
            text=self._t("settings_reset"),
            command=functools.partial(self._reset_setting, name),
        )
        reset_btn.grid(row=row, column=3, sticky="e", padx=(8, 0))
        self.setting_buttons[name] = [apply_btn, reset_btn]

        hint_text = self._t(f"setting_{name}_hint")
        if item.nullable:
            hint_text = f"{hint_text} ({self._t('settings_empty_means_none')})"
        hint = ttk.Label(body, text=hint_text, wraplength=480, justify="left")
        hint.grid(row=row + 1, column=0, columnspan=4, sticky="w")
        self.setting_hints[name] = hint

        state_var = self._tk.StringVar(value="")
        self.setting_state_vars[name] = state_var
        state_label = ttk.Label(body, textvariable=state_var, wraplength=480, justify="left")
        state_label.grid(row=row + 2, column=0, columnspan=4, sticky="w", pady=(0, 10))
        self._refresh_setting_state(name)

        if item.unsafe:
            control.config(state="disabled")
            apply_btn.config(state="disabled")
        return row + 3

    def _setting_display(self, value: object) -> str:
        """Значение настройки в поле ввода; ``None`` — пустая строка."""
        return "" if value is None else str(value)

    def _setting_state_text(self, view: Any) -> str:
        """Строка «сейчас … (откуда). По умолчанию …» под контролом.

        Происхождение показывается всегда, а не только у изменённого:
        персистентная настройка липкая, и через месяц её автор не помнит, чей
        это выбор — его собственный, проекта или дефолт (ADR-0012).
        """
        return self._t(
            "settings_state",
            value=self._setting_display(view.value) or "—",
            origin=self._t(f"settings_origin_{view.origin}"),
            default=self._setting_display(view.default) or "—",
        )

    def _refresh_setting_state(self, name: str) -> None:
        """Перечитать настройку из файла и обновить строку под контролом."""
        view = settings_resolver.describe_setting(name)
        self.setting_state_vars[name].set(self._setting_state_text(view))
        self.setting_vars[name].set(
            bool(view.value)
            if isinstance(self.setting_vars[name].get(), bool)
            else self._setting_display(view.value)
        )

    def _apply_setting(self, name: str) -> None:
        """Сохранить значение контрола; негодное — отвергнуть словами.

        Проверяет то же ``config.validate_values``, что и ``pyproject.toml``:
        сохранить негодное значение значило бы показать в окне одно, а прогнать
        с другим.
        """
        try:
            value = settings_resolver.coerce_value(name, self.setting_vars[name].get())
            settings_resolver.set_user_run_setting(name, value)
        except (ValueError, OSError) as exc:
            self._set_status(self._t("settings_invalid", error=exc), error=True)
            # Контрол возвращается к тому, что записано: иначе в поле осталось
            # бы отвергнутое значение, и следующий взгляд принял бы его за
            # действующее.
            self._refresh_setting_state(name)
            return
        self._refresh_setting_state(name)
        self._set_status(self._t("settings_saved", name=self._t(f"setting_{name}"), value=value))

    def _reset_setting(self, name: str) -> None:
        """Убрать пользовательское значение — вернуть унаследованное."""
        try:
            settings_resolver.reset_setting(name)
        except OSError as exc:
            self._set_status(self._t("settings_invalid", error=exc), error=True)
            return
        self._refresh_setting_state(name)
        view = settings_resolver.describe_setting(name)
        self._set_status(
            self._t(
                "settings_reset_done",
                name=self._t(f"setting_{name}"),
                value=self._setting_display(view.value) or "—",
            )
        )

    def _on_unsafe_toggled(self) -> None:
        """Галка подтверждения открывает и закрывает правку квот песочницы."""
        state = "normal" if self.unsafe_var.get() else "disabled"
        for item in settings_resolver.advanced_settings("unsafe"):
            widget = self.setting_widgets[item.name]
            widget.config(state="readonly" if state == "normal" and item.choices else state)
            self.setting_buttons[item.name][0].config(state=state)

    def _retranslate_advanced(self) -> None:
        """Перевести подписи вкладки после смены языка (issue #1135)."""
        self.notebook.tab(0, text=self._t("launcher_tab_run"))
        self.notebook.tab(1, text=self._t("launcher_tab_advanced"))
        self.advanced_intro.config(text=self._t("settings_intro"))
        self.unsafe_warning.config(text=self._t("settings_unsafe_warning"))
        self.unsafe_check.config(text=self._t("settings_unsafe_unlock"))
        for index, group in enumerate(self._group_frames):
            self.advanced_groups.tab(index, text=self._t(f"settings_group_{group}"))
        for item in settings_resolver.advanced_settings():
            name = item.name
            self.setting_labels[name].config(text=self._t(f"setting_{name}"))
            hint_text = self._t(f"setting_{name}_hint")
            if item.nullable:
                hint_text = f"{hint_text} ({self._t('settings_empty_means_none')})"
            self.setting_hints[name].config(text=hint_text)
            for button, key in zip(
                self.setting_buttons[name], ("settings_apply", "settings_reset"), strict=True
            ):
                button.config(text=self._t(key))
            self._refresh_setting_state(name)

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
        # issue #1134: «Найдено задач: 0» — конец пути для первокурсника, ради
        # которого лаунчер и сделан: он не знает, что задачи сюда попадают
        # загрузчиком, и не понимает, промахнулся ли папкой. Ноль — не счёт, а
        # развилка, поэтому вместо цифры показывается следующий шаг.
        if tasks == 0:
            self.tasks_var.set(self._t("launcher_tasks_found_zero"))
            return
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
            self._set_status(self._port_busy_hint(port), error=True)
            return
        # issue #1133: выбор запоминается ДО старта — окно, закрытое сразу после
        # запуска сервера, всё равно откроется в том же состоянии.
        remember_launch_choice(
            LaunchChoice(
                sandbox=self.sandbox_var.get(),
                record_history=self.selected_record_history(),
                lang=self.lang_var.get(),
                port=port,
                workdir=workdir,
            )
        )
        self.controller.start(
            port,
            sandbox=self.sandbox_var.get(),
            workdir=workdir,
            lang=self.lang_var.get(),
            record_history=self.selected_record_history(),
        )

    def _port_busy_hint(self, port: int) -> str:
        """Что сказать про занятый порт: действие вместо тупика (issue #1134).

        Прежде было «порт занят, выберите другой» — при том что лаунчер умеет
        проверять порты сам, а чаще всего на этом порту стоит наш же сервер с
        прошлого запуска, и нужное действие вовсе не «смени порт».
        """
        if our_server_on(port, host=self.controller.host):
            self._last_url = f"http://{self.controller.host}:{port}"
            self.open_btn.config(state="normal")
            return self._t("launcher_port_busy_ours", port=port)
        free = next_free_port(port + 1, host=self.controller.host)
        if free is None:
            return self._t("launcher_port_busy", port=port)
        return self._t("launcher_port_busy_free", port=port, free=free)

    def _profile_values(self) -> list[str]:
        """Пункты списка профилей: «свой набор» первым, затем имена."""
        return [self._t("launcher_profile_custom"), *profile_names()]

    def _refresh_profiles(self, *, selected: str = "") -> None:
        """Перечитать список профилей из файла и выставить выбранный пункт."""
        self.profile_box.config(values=self._profile_values())
        self.profile_var.set(selected or self._t("launcher_profile_custom"))

    def _selected_profile_name(self) -> str:
        """Имя выбранного профиля; пустая строка — пункт «свой набор»."""
        chosen = self.profile_var.get().strip()
        return "" if chosen == self._t("launcher_profile_custom") else chosen

    def _current_choice(self) -> LaunchChoice:
        """Снимок полей окна как ``LaunchChoice`` (issue #1133)."""
        raw_port = self.port_var.get().strip()
        return LaunchChoice(
            sandbox=self.sandbox_var.get(),
            record_history=self.selected_record_history(),
            lang=self.lang_var.get(),
            port=int(raw_port) if raw_port.isdigit() else None,
            workdir=Path(self.workdir_var.get().strip() or "."),
        )

    def _on_profile_selected(self) -> None:
        """Выбран профиль — его значения заполняют поля окна.

        «Свой набор» ничего не подставляет: это состояние «поля не совпадают ни
        с одним сохранённым», и трогать введённое им нельзя.
        """
        name = self._selected_profile_name()
        if not name:
            return
        choice = launch_choice_from_profile(name)
        if choice is None:  # удалён другим окном, пока это было открыто
            self._set_status(self._t("launcher_profile_gone", name=name), error=True)
            self._refresh_profiles()
            return
        values = initial_launch_values(choice)
        self.sandbox_var.set(values.sandbox)
        self.history_box.set(self._history_label(values.record_history))
        self.lang_var.set(values.lang)
        self.port_var.set(str(values.port))
        self.workdir_var.set(str(values.workdir))
        remember_profile_name(name)
        self._set_status(self._t("launcher_profile_applied", name=name))

    def _on_save_profile(self) -> None:
        """Сохранить текущие поля под именем (спросив его)."""
        from tkinter import simpledialog

        suggested = self._selected_profile_name()
        name = simpledialog.askstring(
            self._t("launcher_profile_save"),
            self._t("launcher_profile_name_prompt"),
            initialvalue=suggested,
            parent=self.root,
        )
        if name is None:  # отмена диалога — не ошибка
            return
        try:
            save_launch_profile(launcher_settings_path(), name, self._current_choice())
        except ProfileLimitError:
            self._set_status(self._t("launcher_profile_limit"), error=True)
            return
        except ValueError:
            self._set_status(self._t("launcher_profile_bad_name"), error=True)
            return
        except OSError as exc:
            self._set_status(self._t("launcher_profile_save_failed", error=exc), error=True)
            return
        remember_profile_name(name.strip())
        self._refresh_profiles(selected=name.strip())
        self._set_status(self._t("launcher_profile_saved", name=name.strip()))

    def _on_delete_profile(self) -> None:
        """Удалить выбранный профиль (поля окна остаются как есть).

        Поля намеренно не сбрасываются: пользователь удаляет ЗАПИСЬ, а не свой
        текущий выбор — обнулять форму значило бы наказывать за уборку.
        """
        name = self._selected_profile_name()
        if not name:
            self._set_status(self._t("launcher_profile_pick_first"), error=True)
            return
        try:
            deleted = delete_launch_profile(launcher_settings_path(), name)
        except OSError as exc:
            self._set_status(self._t("launcher_profile_save_failed", error=exc), error=True)
            return
        self._refresh_profiles()
        key = "launcher_profile_deleted" if deleted else "launcher_profile_gone"
        self._set_status(self._t(key, name=name), error=not deleted)

    def _history_label(self, value: bool | None) -> str:
        """Подпись пункта «запись истории» по значению (обратное к выбору, #1133)."""
        if value is True:
            return self._t("launcher_history_on")
        if value is False:
            return self._t("launcher_history_off")
        return self._t("launcher_history_inherit")

    def selected_record_history(self) -> bool | None:
        """Выбор по записи истории: ``True``/``False``/``None`` — «унаследовать».

        ``None`` — не «выключено», а «пользователь не решал»: флаг не попадёт в
        команду, и значение резолвит сервер по обычной лестнице (ADR-0012).
        """
        chosen = self.history_var.get()
        if chosen == self._t("launcher_history_on"):
            return True
        if chosen == self._t("launcher_history_off"):
            return False
        return None

    def _refresh_command_preview(self) -> None:
        """Показать команду, которая получится при текущем выборе (issue #1131)."""
        raw_port = self.port_var.get().strip()
        port = int(raw_port) if raw_port.isdigit() else DEFAULT_PORT
        command = build_server_command(
            port,
            sandbox=self.sandbox_var.get(),
            workdir=Path(self.workdir_var.get().strip() or "."),
            lang=self.lang_var.get(),
            record_history=self.selected_record_history(),
        )
        # Первый элемент — путь к интерпретатору: в предпросмотре он длиннее
        # самой команды и ничего не объясняет.
        self.command_var.set(
            self._t("launcher_command_preview", command=" ".join(["python", *command[1:]]))
        )

    def _on_lang_changed(self) -> None:
        """Переключение языка: подписи окна и язык сервера меняются вместе.

        Перерисовываем только то, что видит пользователь прямо сейчас: полная
        пересборка окна ради смены подписей уронила бы состояние запущенного
        сервера, а язык обязан переключаться и на работающем лаунчере.
        """
        self._lang = self.lang_var.get()
        self._messages = load_ui_messages(self._lang)
        self.root.title(self._t("launcher_window_title"))
        self.radio_simple.config(text=self._t("launcher_mode_simple"))
        self.radio_sandbox.config(text=self._t("launcher_mode_sandbox"))
        self.sandbox_note.config(text=self._t("launcher_mode_sandbox_note"))
        self.browse_btn.config(text=self._t("launcher_browse"))
        self.open_btn.config(text=self._t("launcher_open_browser"))
        self.history_box.config(
            values=[
                self._t("launcher_history_inherit"),
                self._t("launcher_history_on"),
                self._t("launcher_history_off"),
            ]
        )
        self.history_box.set(self._t("launcher_history_inherit"))
        self._retranslate_advanced()
        self._refresh_tasks_found()
        self._refresh_command_preview()

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
        """Блокировать порт/папку/sandbox/настройки во время работы сервера."""
        state = "normal" if enabled else "disabled"
        self.port_entry.config(state=state)
        self.workdir_entry.config(state=state)
        self.browse_btn.config(state=state)
        self.radio_simple.config(state=state)
        self.radio_sandbox.config(state=state)
        # issue #1136: настройки прогона дочерний сервер читает ОДИН раз, на
        # старте. Оставить их живыми на работающем сервере значило бы принимать
        # правку, которая ничего не меняет до перезапуска, — та самая тихо не
        # сработавшая настройка, против которой вкладка и сделана.
        for item in settings_resolver.advanced_settings():
            unlocked = enabled and (not item.unsafe or self.unsafe_var.get())
            control_state = "normal" if unlocked else "disabled"
            self.setting_widgets[item.name].config(
                state="readonly" if unlocked and item.choices else control_state
            )
            self.setting_buttons[item.name][0].config(state=control_state)
            self.setting_buttons[item.name][1].config(state="normal" if enabled else "disabled")
        self.unsafe_check.config(state=state)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        """Обновить статус-строку (красный цвет при ошибке)."""
        self.status_var.set(text)
        self.status_label.config(foreground="#c0392b" if error else "")


def create_app(
    controller: ServerController | None = None,
    *,
    root: Any = None,
    lang: str | None = None,
) -> LauncherApp:
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
    return LauncherApp(root, controller or ServerController(), lang=lang)


def resolve_version() -> str:
    """Версия пакета для ``--version``; ``0.0.0+unknown`` вне установленного пакета.

    Читается из метаданных, как и в CLI (единый источник — ``pyproject.toml``);
    ``importlib.metadata`` — stdlib, ребра DAG не добавляет.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version("stepik-python-grader")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def build_arg_parser(messages: dict[str, str]) -> argparse.ArgumentParser:
    """Разбор аргументов ``stepik-grader-gui`` (issue #1135).

    Лаунчер — вторая установленная команда продукта, но вела себя не как
    команда: ``--help`` не существовал, а любой аргумент молча игнорировался и
    просто открывалось окно. Флагов немного и больше не нужно: окно на то и
    окно, что выбор делается в нём (issue #1131), а не в командной строке.
    """

    def _t(key: str) -> str:
        return messages.get(key, key)

    parser = argparse.ArgumentParser(
        prog="stepik-grader-gui",
        description=_t("launcher_cli_description"),
        epilog=_t("launcher_cli_epilog"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--lang",
        choices=_SUPPORTED_LANGS,
        default=None,
        help=_t("launcher_cli_lang_help"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"stepik-grader-gui {resolve_version()}",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа GUI-лаунчера (``python -m stepik_grader.launcher``, gui-script).

    Аргументы разбираются ДО создания окна: ``--help``/``--version`` обязаны
    работать там, где дисплея нет вовсе, а неизвестный флаг — отвергаться, а не
    игнорироваться (issue #1135).

    В headless-окружении/сборке без ``tkinter`` окно не создаётся, но работа
    делается: веб-интерфейс поднимается сам (``serve_without_gui``, issue
    #1134). Раньше здесь печатался совет набрать ту же команду руками — а на
    Windows gui-script идёт через ``pythonw.exe`` без консоли, и совет уходил
    в никуда. Код возврата — от дочернего сервера, а не жёсткая единица.
    """
    # issue #1108: подсказка про отсутствующий tkinter и статусы лаунчера идут
    # в консоль — в cp1251 они не должны ронять процесс.
    force_utf8_stdio()
    # Язык справки — тот же, что у окна: подсказку читают до его открытия.
    args = build_arg_parser(load_ui_messages(detect_lang())).parse_args(argv)
    messages = load_ui_messages(args.lang or detect_lang())

    def _t(key: str, **params: object) -> str:
        template = messages.get(key, key)
        return template.format(**params) if params else template

    try:
        import tkinter
    except ImportError:
        _print_err(_t("launcher_cli_no_tkinter"))
        raise SystemExit(serve_without_gui(messages, lang=args.lang)) from None

    try:
        app = create_app(lang=args.lang)
    except tkinter.TclError:
        _print_err(_t("launcher_cli_headless"))
        raise SystemExit(serve_without_gui(messages, lang=args.lang)) from None

    app.run()


if __name__ == "__main__":
    main()
