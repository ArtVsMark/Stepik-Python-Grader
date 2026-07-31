"""Тесты GUI-лаунчера веб-интерфейса (issue #661).

GUI-free ядро (``build_server_command``/``port_available``/``ServerController``/
``_last_line``) тестируется в любом окружении, включая headless. ``ServerController``
проверяется с инъекцией ``spawn`` — реальными мини-процессами, симулирующими
поднявшийся (биндит порт) и упавший (пишет в stderr, exit 1) сервер, без запуска
настоящего ``--serve``. Tk-виджеты — под guard'ом наличия графического дисплея
(в CI без дисплея тесты помечаются skip, как ``_pick_path_via_dialog``).
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from stepik_grader import launcher
from stepik_grader.launcher import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    LauncherApp,
    ServerController,
    ServerState,
    ServerStatus,
    build_server_command,
    port_available,
)

# Тестовые двойники `spawn` читают потоки так же, как продакшн-`_default_spawn`:
# явный UTF-8 вместо системной кодовой страницы. Без этого reader-поток падал на
# Windows-раннере с UnicodeDecodeError (cp1252) — тест оставался зелёным, но лог
# CI заполнялся трейсбеками из фонового потока, маскируя настоящие ошибки.
_PIPE_TEXT: dict[str, object] = {"text": True, "encoding": "utf-8", "errors": "replace"}


def _free_port() -> int:
    """Занять и сразу освободить эфемерный порт, вернув его номер."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def _wait_state(controller: ServerController, state: ServerState, timeout: float = 10.0) -> bool:
    """Дождаться перехода контроллера в ``state`` (или timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.snapshot().state == state:
            return True
        time.sleep(0.05)
    return False


def _spawn_running(port: int):
    """Фабрика ``spawn``: мини-процесс биндит ``port`` и висит — имитация сервера."""
    script = (
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('{DEFAULT_HOST}', {port}))\n"
        "s.listen()\n"
        "time.sleep(30)\n"
    )

    def spawn(_command: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_PIPE_TEXT,
        )

    return spawn


def _spawn_failing(_command: list[str]) -> subprocess.Popen[str]:
    """``spawn``: процесс пишет в stderr и падает — имитация отказа (порт занят и т.п.)."""
    return subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom: bind failed\\n'); sys.exit(1)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **_PIPE_TEXT,
    )


class TestBuildServerCommand:
    def test_basic_command_shape(self) -> None:
        cmd = build_server_command(DEFAULT_PORT, sandbox=False, workdir=Path("/work"))
        assert cmd[0] == sys.executable
        assert cmd[1:4] == ["-m", "stepik_grader", "--serve"]
        assert "--port" in cmd
        assert str(DEFAULT_PORT) in cmd
        assert cmd[cmd.index("--root") + 1] == str(Path("/work"))
        assert "--sandbox" not in cmd

    def test_sandbox_appends_flag(self) -> None:
        cmd = build_server_command(1234, sandbox=True, workdir=Path("/w"))
        assert "--sandbox" in cmd
        assert "1234" in cmd

    def test_uses_sys_executable_not_platform_string(self) -> None:
        cmd = build_server_command(8000, sandbox=False, workdir=Path.cwd())
        assert cmd[0] == sys.executable
        assert "python3" not in cmd
        assert "python" not in cmd[1:]


class TestPortAvailable:
    def test_true_for_free_port(self) -> None:
        assert port_available(_free_port()) is True

    def test_false_when_actively_listening(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((DEFAULT_HOST, 0))
            sock.listen()
            port = sock.getsockname()[1]
            assert port_available(port) is False


class TestServerControllerLifecycle:
    def test_reaches_running_then_stops(self) -> None:
        port = _free_port()
        controller = ServerController(spawn=_spawn_running(port))
        controller.start(port, sandbox=False, workdir=Path.cwd())
        assert _wait_state(controller, ServerState.RUNNING)
        assert controller.snapshot().url == f"http://{DEFAULT_HOST}:{port}"
        controller.stop()
        assert _wait_state(controller, ServerState.STOPPED)

    def test_failure_reports_error_with_stderr_tail(self) -> None:
        controller = ServerController(spawn=_spawn_failing)
        controller.start(_free_port(), sandbox=False, workdir=Path.cwd())
        assert _wait_state(controller, ServerState.ERROR)
        assert "boom: bind failed" in controller.snapshot().error

    def test_spawn_oserror_becomes_error_state(self) -> None:
        def spawn(_command: list[str]) -> subprocess.Popen[str]:
            raise OSError("exec denied")

        controller = ServerController(spawn=spawn)
        controller.start(_free_port(), sandbox=False, workdir=Path.cwd())
        status = controller.snapshot()
        assert status.state == ServerState.ERROR
        assert "exec denied" in status.error

    def test_start_is_idempotent_while_running(self) -> None:
        port = _free_port()
        base = _spawn_running(port)
        calls: list[list[str]] = []

        def counting_spawn(command: list[str]) -> subprocess.Popen[str]:
            calls.append(command)
            return base(command)

        controller = ServerController(spawn=counting_spawn)
        controller.start(port, sandbox=False, workdir=Path.cwd())
        assert _wait_state(controller, ServerState.RUNNING)
        controller.start(port, sandbox=False, workdir=Path.cwd())  # должно быть no-op
        time.sleep(0.2)
        controller.stop()
        assert _wait_state(controller, ServerState.STOPPED)
        assert len(calls) == 1

    def test_stop_without_start_is_noop(self) -> None:
        controller = ServerController(spawn=_spawn_failing)
        controller.stop()  # не должно бросать
        assert controller.snapshot().state == ServerState.STOPPED

    def test_snapshot_returns_status_dataclass(self) -> None:
        controller = ServerController(spawn=_spawn_failing)
        status = controller.snapshot()
        assert isinstance(status, ServerStatus)
        assert status.state == ServerState.STOPPED
        assert status.url == ""


class TestLastLine:
    def test_picks_last_nonempty(self) -> None:
        assert launcher._last_line("a\nb\n\n") == "b"

    def test_empty_returns_empty(self) -> None:
        assert launcher._last_line("") == ""

    def test_single_line(self) -> None:
        assert launcher._last_line("solo") == "solo"


class TestMainGraceful:
    def test_without_tkinter_prints_cli_hint_and_exits(self, monkeypatch, capsys) -> None:
        # None в sys.modules → `import tkinter` бросает ImportError.
        monkeypatch.setitem(sys.modules, "tkinter", None)
        with pytest.raises(SystemExit) as exc:
            launcher.main()
        assert exc.value.code == 1
        assert "--serve" in capsys.readouterr().out

    def test_headless_tclerror_prints_cli_hint_and_exits(self, monkeypatch, capsys) -> None:
        tk = pytest.importorskip("tkinter")

        def _raise() -> LauncherApp:
            raise tk.TclError("no display")

        monkeypatch.setattr(launcher, "create_app", _raise)
        with pytest.raises(SystemExit) as exc:
            launcher.main()
        assert exc.value.code == 1
        assert "--serve" in capsys.readouterr().out


# Один Tk-интерпретатор на весь модуль + Toplevel на тест: множественные
# create/destroy Tk() в одном процессе на Windows роняют TclError на 5-6-м
# интерполяторе (order-dependent flaky skip). Toplevel'ы этой проблемы лишены.
@pytest.fixture(scope="module")
def _tk_module():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("нет графического дисплея")
    root.withdraw()
    try:
        yield tk, root
    finally:
        with contextlib.suppress(Exception):
            root.destroy()


@pytest.fixture
def tk_window(_tk_module, monkeypatch):
    """Отдельный withdrawn Toplevel на тест поверх общего Tk-рута модуля.

    Язык окна фиксируется явно (issue #821): подписи локализованы, а язык по
    умолчанию берётся из системной локали — без фиксации проверки русских строк
    падали бы на англоязычных раннерах Windows/macOS, оставаясь зелёными на
    русской машине разработчика. Английский путь проверяется отдельным тестом.
    """
    monkeypatch.setenv(launcher.LANG_ENV_VAR, "ru")
    tk, root = _tk_module
    top = tk.Toplevel(root)
    top.withdraw()
    try:
        yield top
    finally:
        with contextlib.suppress(Exception):
            top.destroy()


class TestGuiSmoke:
    def test_builds_widgets_with_defaults(self, tk_window) -> None:
        app = LauncherApp(tk_window, ServerController())
        tk_window.update()  # прокачать событийный цикл без блокирующего mainloop
        assert app.action_btn.cget("text") == "Запустить"
        assert app.port_var.get() == str(DEFAULT_PORT)
        assert app.sandbox_var.get() is False
        assert "Остановлен" in app.status_var.get()

    def test_widgets_follow_selected_language(self, _tk_module, monkeypatch) -> None:
        """issue #821: под английским языком окно строится с английскими подписями."""
        monkeypatch.setenv(launcher.LANG_ENV_VAR, "en")
        tk, root = _tk_module
        top = tk.Toplevel(root)
        top.withdraw()
        try:
            app = LauncherApp(top, ServerController())
            top.update()
            assert app.action_btn.cget("text") == "Start"
            assert app.open_btn.cget("text") == "Open in browser"
            assert app.status_var.get() == "Stopped"
        finally:
            with contextlib.suppress(Exception):
                top.destroy()

    def test_invalid_port_sets_error_status(self, tk_window) -> None:
        app = LauncherApp(tk_window, ServerController())
        app.port_var.set("не-число")
        app._start()
        tk_window.update()
        assert "порт" in app.status_var.get().lower()

    def test_running_state_disables_inputs_and_enables_open(self, tk_window) -> None:
        # _render на снимке RUNNING: кнопка → «Остановить», ввод заблокирован,
        # «Открыть в браузере» активна (webbrowser.open не дёргаем — без url-перехода).
        app = LauncherApp(tk_window, ServerController())
        app._opened_url = f"http://{DEFAULT_HOST}:8000"  # подавить авто-открытие
        app._render(ServerStatus(state=ServerState.RUNNING, url=app._opened_url))
        tk_window.update()
        assert app.action_btn.cget("text") == "Остановить"
        assert str(app.open_btn.cget("state")) == "normal"
        assert str(app.port_entry.cget("state")) == "disabled"

    def test_create_app_builds_launcher(self, tk_window) -> None:
        app = launcher.create_app(root=tk_window)
        tk_window.update()
        assert isinstance(app, LauncherApp)


class _FakeController:
    """Контроллер-заглушка: фиксирует start/stop и отдаёт заданный snapshot."""

    def __init__(self, state: ServerState = ServerState.STOPPED, url: str = "") -> None:
        self._status = ServerStatus(state=state, url=url)
        self.host = DEFAULT_HOST
        self.started: list[tuple[int, bool, Path]] = []
        self.stopped = 0

    def snapshot(self) -> ServerStatus:
        return self._status

    def start(self, port: int, *, sandbox: bool, workdir: Path) -> None:
        self.started.append((port, sandbox, workdir))

    def stop(self) -> None:
        self.stopped += 1


class TestGuiHandlers:
    def test_render_covers_all_states(self, tk_window) -> None:
        app = LauncherApp(tk_window, _FakeController())
        app._render(ServerStatus(state=ServerState.STARTING))
        assert app.action_btn.cget("text") == "Остановить"
        assert "Запуск" in app.status_var.get()
        # ERROR берёт последнюю непустую строку stderr в статус.
        app._render(ServerStatus(state=ServerState.ERROR, error="трейс\nконкретная причина"))
        assert app.action_btn.cget("text") == "Запустить"
        assert app.status_var.get() == "конкретная причина"
        app._render(ServerStatus(state=ServerState.STOPPED))
        assert "Остановлен" in app.status_var.get()

    def test_on_action_starts_when_stopped(self, tk_window) -> None:
        fake = _FakeController(ServerState.STOPPED)
        app = LauncherApp(tk_window, fake)
        app.port_var.set(str(_free_port()))
        app._on_action()
        assert len(fake.started) == 1
        assert fake.started[0][1] is False  # sandbox по умолчанию выключен

    def test_on_action_starts_with_sandbox_flag(self, tk_window) -> None:
        fake = _FakeController(ServerState.STOPPED)
        app = LauncherApp(tk_window, fake)
        app.port_var.set(str(_free_port()))
        app.sandbox_var.set(True)
        app._on_action()
        assert fake.started and fake.started[0][1] is True

    def test_on_action_stops_when_running(self, tk_window) -> None:
        fake = _FakeController(ServerState.RUNNING)
        app = LauncherApp(tk_window, fake)
        app._on_action()
        assert fake.stopped == 1

    def test_start_rejects_missing_workdir(self, tk_window) -> None:
        fake = _FakeController(ServerState.STOPPED)
        app = LauncherApp(tk_window, fake)
        app.workdir_var.set(str(Path.cwd() / "нет-такой-папки-12345"))
        app._start()
        assert not fake.started
        assert "папка" in app.status_var.get().lower()

    def test_open_browser_opens_last_url(self, tk_window, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
        app = LauncherApp(tk_window, _FakeController())
        app._last_url = f"http://{DEFAULT_HOST}:9191"
        app._open_browser()
        assert opened == [f"http://{DEFAULT_HOST}:9191"]

    def test_browse_dir_updates_workdir(self, tk_window, monkeypatch) -> None:
        from tkinter import filedialog

        monkeypatch.setattr(filedialog, "askdirectory", lambda **kwargs: "/выбранная/папка")
        app = LauncherApp(tk_window, _FakeController())
        app._browse_dir()
        assert app.workdir_var.get() == "/выбранная/папка"

    def test_browse_dir_cancel_keeps_workdir(self, tk_window, monkeypatch) -> None:
        from tkinter import filedialog

        monkeypatch.setattr(filedialog, "askdirectory", lambda **kwargs: "")  # отмена диалога
        app = LauncherApp(tk_window, _FakeController())
        before = app.workdir_var.get()
        app._browse_dir()
        assert app.workdir_var.get() == before

    def test_on_close_stops_controller_and_destroys(self, tk_window) -> None:
        fake = _FakeController(ServerState.RUNNING)
        app = LauncherApp(tk_window, fake)
        app._on_close()
        assert fake.stopped == 1
        assert not app.root.winfo_exists()

    def test_running_auto_opens_browser_exactly_once(self, tk_window, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
        app = LauncherApp(tk_window, _FakeController())
        url = f"http://{DEFAULT_HOST}:8123"
        app._render(ServerStatus(state=ServerState.RUNNING, url=url))
        app._render(ServerStatus(state=ServerState.RUNNING, url=url))  # повторный тик
        assert opened == [url]  # авто-открытие ровно один раз на переход

    def test_run_variant_radios_default_to_simple(self, tk_window) -> None:
        # issue #661: галка изоляции заменена явным выбором варианта запуска.
        app = LauncherApp(tk_window, _FakeController())
        assert app.radio_simple.winfo_exists()
        assert app.radio_sandbox.winfo_exists()
        assert app.sandbox_var.get() is False  # дефолт — «Простой сервер»
        app.sandbox_var.set(True)  # выбор «С изоляцией» (как клик по радио)
        assert app.sandbox_var.get() is True

    def test_running_disables_both_variant_radios(self, tk_window) -> None:
        app = LauncherApp(tk_window, _FakeController())
        app._render(ServerStatus(state=ServerState.RUNNING, url=""))
        assert str(app.radio_simple.cget("state")) == "disabled"
        assert str(app.radio_sandbox.cget("state")) == "disabled"


# ---------------------------------------------------------------------------
# issue #821: язык окна лаунчера
#
# GUI — самый низкобарьерный вход («без командной строки») и до этого
# единственная поверхность вообще без переводов. Каталог читается файлом:
# модуль остаётся leaf'ом, нового ребра DAG не появляется.
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_locale_env(monkeypatch):
    """Убрать ВСЕ переменные локали перед проверкой детекта языка.

    Иначе тест проверяет окружение раннера, а не код: на macOS-раннерах задан
    `LC_ALL`, который по POSIX перекрывает `LANG`, — тест, ставивший только
    `LANG=ru_RU`, получал английский и падал (на машине разработчика, где
    `LC_ALL` не задан, он был зелёным).
    """
    for var in (launcher.LANG_ENV_VAR, "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_lang_env_var_wins(clean_locale_env) -> None:
    """Переменная окружения перекрывает системную локаль."""
    clean_locale_env.setenv(launcher.LANG_ENV_VAR, "en")
    clean_locale_env.setenv("LANG", "ru_RU.UTF-8")
    assert launcher.detect_lang() == "en"


def test_unsupported_env_value_is_ignored(clean_locale_env) -> None:
    """Неизвестное значение переменной не выбирает несуществующую локаль."""
    clean_locale_env.setenv(launcher.LANG_ENV_VAR, "fr")
    clean_locale_env.setenv("LANG", "ru_RU.UTF-8")
    assert launcher.detect_lang() == "ru"


def test_system_locale_picks_english(clean_locale_env) -> None:
    """Не-русская системная локаль даёт английское окно — цель issue #821."""
    clean_locale_env.setenv("LANG", "en_US.UTF-8")
    assert launcher.detect_lang() == "en"


def test_russian_system_locale_stays_russian(clean_locale_env) -> None:
    """Русская система — русское окно (прежнее поведение)."""
    clean_locale_env.setenv("LANG", "ru_RU.UTF-8")
    assert launcher.detect_lang() == "ru"


def test_lc_all_overrides_lang(clean_locale_env) -> None:
    """POSIX-приоритет соблюдён: `LC_ALL` сильнее `LANG` (ровно это и было на CI)."""
    clean_locale_env.setenv("LC_ALL", "en_US.UTF-8")
    clean_locale_env.setenv("LANG", "ru_RU.UTF-8")
    assert launcher.detect_lang() == "en"


def test_undetectable_locale_falls_back_to_russian(clean_locale_env) -> None:
    """Локаль не определяется → русский, а не пустое окно."""
    clean_locale_env.setattr(launcher.locale, "getlocale", lambda: (None, None))
    assert launcher.detect_lang() == "ru"


def test_ui_messages_are_localized() -> None:
    """Каталог отдаёт подписи обоих языков, и они различаются."""
    ru = launcher.load_ui_messages("ru")
    en = launcher.load_ui_messages("en")
    assert ru["launcher_start"] == "Запустить"
    assert en["launcher_start"] == "Start"
    assert en["launcher_status_running"].startswith("Running")


def test_ui_messages_unknown_lang_falls_back_to_russian() -> None:
    """Неизвестный язык — русский каталог, а не пустой словарь."""
    assert launcher.load_ui_messages("fr")["launcher_stop"] == "Остановить"


def test_ui_messages_missing_catalog_is_not_fatal(monkeypatch, tmp_path: Path) -> None:
    """Пропавший каталог не роняет GUI: пустой словарь, подписи покажут ключи."""
    monkeypatch.setattr(launcher, "_LOCALES_DIR", tmp_path / "nope")
    assert launcher.load_ui_messages("ru") == {}
