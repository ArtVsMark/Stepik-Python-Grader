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

    # issue #1131 — выбор пользователя доезжает до сервера, а «не выбирал»
    # остаётся отличимым от «выбрал то же, что дефолт» (ADR-0012).

    def test_untouched_choices_add_no_flags(self) -> None:
        """Ключевой инвариант ADR-0012: дефолты НЕ запекаются в команду.

        Иначе правка `pyproject.toml` перестала бы действовать — окно
        перекрывало бы её флагом с тем же значением, — а сохранённый профиль
        заморозил бы дефолты того дня, когда его создали.
        """
        cmd = build_server_command(8000, sandbox=False, workdir=Path("/w"))

        assert "--lang" not in cmd
        assert "--history" not in cmd
        assert "--no-history" not in cmd

    def test_language_reaches_the_server(self) -> None:
        cmd = build_server_command(8000, sandbox=False, workdir=Path("/w"), lang="en")
        assert cmd[cmd.index("--lang") + 1] == "en"

    def test_history_on_and_off_are_distinct_flags(self) -> None:
        on = build_server_command(8000, sandbox=False, workdir=Path("/w"), record_history=True)
        off = build_server_command(8000, sandbox=False, workdir=Path("/w"), record_history=False)

        assert "--history" in on and "--no-history" not in on
        assert "--no-history" in off and "--history" not in off


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

    def test_start_passes_lang_and_history_to_the_command(self) -> None:
        """issue #1131: выбор из окна доходит до дочернего процесса, а не теряется."""
        port = _free_port()
        base = _spawn_running(port)
        calls: list[list[str]] = []

        def capturing_spawn(command: list[str]) -> subprocess.Popen[str]:
            calls.append(command)
            return base(command)

        controller = ServerController(spawn=capturing_spawn)
        controller.start(port, sandbox=False, workdir=Path.cwd(), lang="en", record_history=False)
        assert _wait_state(controller, ServerState.RUNNING)
        controller.stop()
        assert _wait_state(controller, ServerState.STOPPED)

        assert calls[0][calls[0].index("--lang") + 1] == "en"
        assert "--no-history" in calls[0]

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
            # issue #1135: main() разбирает argv, поэтому список обязателен —
            # иначе argparse увидит аргументы самого pytest.
            launcher.main([])
        assert exc.value.code == 1
        assert "--serve" in capsys.readouterr().out

    def test_headless_tclerror_prints_cli_hint_and_exits(self, monkeypatch, capsys) -> None:
        tk = pytest.importorskip("tkinter")

        def _raise() -> LauncherApp:
            raise tk.TclError("no display")

        monkeypatch.setattr(launcher, "create_app", _raise)
        with pytest.raises(SystemExit) as exc:
            # issue #1135: как и в тесте выше — main() разбирает argv, поэтому
            # без явного списка argparse увидит аргументы самого pytest.
            launcher.main([])
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
        # issue #1131: выбор языка и записи истории — отдельно от прежнего
        # кортежа, чтобы существующие проверки start-аргументов не переписывать.
        self.choices: list[tuple[str | None, bool | None]] = []
        self.stopped = 0

    def snapshot(self) -> ServerStatus:
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
        self.started.append((port, sandbox, workdir))
        self.choices.append((lang, record_history))

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

    def test_untouched_choices_stay_inherited(self, tk_window) -> None:
        """issue #1131: нетронутые контролы уходят как «не выбирал», а не как дефолт.

        Инвариант ADR-0012: только тогда настройка из `pyproject.toml` продолжает
        действовать, а профиль не замораживает дефолты дня своего создания.
        """
        fake = _FakeController(ServerState.STOPPED)
        app = LauncherApp(tk_window, fake)
        app.port_var.set(str(_free_port()))

        app._on_action()

        assert fake.choices[0][1] is None  # запись истории — «как в настройках»

    def test_history_choice_reaches_the_controller(self, tk_window) -> None:
        """Выбранное «выключить» доезжает до команды, а не теряется в окне."""
        fake = _FakeController(ServerState.STOPPED)
        app = LauncherApp(tk_window, fake)
        app.port_var.set(str(_free_port()))
        app.history_var.set(app._t("launcher_history_off"))

        app._on_action()

        assert fake.choices[0][1] is False

    def test_command_preview_shows_what_will_run(self, tk_window) -> None:
        """Предпросмотр показывает реальную команду, а не приблизительную."""
        app = LauncherApp(tk_window, _FakeController())
        app.sandbox_var.set(True)

        preview = app.command_var.get()

        assert "--serve" in preview and "--sandbox" in preview

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


# ---------------------------------------------------------------------------
# issue #823 — стартовая папка берётся из stepik_config.json, промах виден
# сразу, а остановка на переходе «готов» не оставляет окно в «Запуск…»
# ---------------------------------------------------------------------------


class TestDefaultWorkdir:
    """Дефолт рабочей папки: настроенные задачи вместо каталога ярлыка."""

    def _config(self, folder: Path, root_dir: str) -> None:
        (folder / "stepik_config.json").write_text(
            f'{{"root_dir": "{root_dir}", "secrets_path": "secrets.json"}}', encoding="utf-8"
        )

    def test_without_config_falls_back_to_cwd(self, tmp_path: Path) -> None:
        assert launcher.default_workdir(tmp_path) == tmp_path

    def test_without_config_starts_at_project_root(self, tmp_path: Path) -> None:
        """issue #1132: запуск из подпапки проекта открывает окно в проекте.

        Прежний фолбэк — голый cwd — означал, что окно стартует там, откуда его
        позвали. Лечило это только наличие `stepik_config.json`, то есть
        сценарий «задачи скачаны загрузчиком»; своя папка с задачами, собранная
        руками, оставалась ни с чем: рабочая папка задаёт `--root`, то есть
        периметр сервера, и промах даёт не пустой экран, а 403 на задачи.
        """
        (tmp_path / ".git").mkdir()  # маркер корня проекта, как у workspace_root
        nested = tmp_path / "lesson1" / "step2"
        nested.mkdir(parents=True)

        assert launcher.default_workdir(nested) == tmp_path

    def test_settings_file_marks_the_root_too(self, tmp_path: Path) -> None:
        """Корень опознаётся и по `.grader_settings.json` — у пользователя pipx `.git` нет."""
        (tmp_path / ".grader_settings.json").write_text("{}", encoding="utf-8")
        nested = tmp_path / "tasks" / "01"
        nested.mkdir(parents=True)

        assert launcher.default_workdir(nested) == tmp_path

    def test_config_still_wins_over_project_root(self, tmp_path: Path) -> None:
        """`stepik_config.json` остаётся уточнением и по-прежнему сильнее фолбэка.

        Иначе фикс #823 отменился бы: через ярлык cwd — каталог ярлыка, и
        настроенная папка задач нужна именно там.
        """
        (tmp_path / ".git").mkdir()
        tasks = tmp_path / "StepikTasks"
        tasks.mkdir()
        self._config(tmp_path, "StepikTasks")
        nested = tmp_path / "a"
        nested.mkdir()

        assert launcher.default_workdir(nested) == tmp_path

    def test_relative_root_dir_keeps_config_folder(self, tmp_path: Path) -> None:
        """Задачи внутри — берём папку конфига: и задачи видны, и загрузчик цел."""
        (tmp_path / "StepikTasks").mkdir()
        self._config(tmp_path, "StepikTasks")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert launcher.default_workdir(nested) == tmp_path

    def test_absolute_root_dir_outside_wins(self, tmp_path: Path) -> None:
        """Если задачи лежат наружу — рабочей папкой становится сам root_dir."""
        outside = tmp_path / "elsewhere" / "tasks"
        outside.mkdir(parents=True)
        home = tmp_path / "home"
        home.mkdir()
        self._config(home, str(outside).replace("\\", "\\\\"))
        assert launcher.default_workdir(home) == outside

    def test_broken_config_does_not_break_launch(self, tmp_path: Path) -> None:
        (tmp_path / "stepik_config.json").write_text("{ не json", encoding="utf-8")
        assert launcher.default_workdir(tmp_path) == tmp_path

    def test_missing_root_dir_folder_falls_back_to_config_folder(self, tmp_path: Path) -> None:
        self._config(tmp_path, "StepikTasks")  # папку не создаём
        assert launcher.default_workdir(tmp_path) == tmp_path


class TestCountTasks:
    """«Найдено задач: N» — промах с папкой виден до открытия браузера."""

    def test_counts_folders_with_tests(self, tmp_path: Path) -> None:
        for name in ("01-a", "02-b"):
            (tmp_path / name / "tests").mkdir(parents=True)
        (tmp_path / "not-a-task").mkdir()
        assert launcher.count_tasks(tmp_path) == (2, 2)

    def test_counts_workdir_itself(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        assert launcher.count_tasks(tmp_path) == (1, 1)

    def test_zero_for_empty_folder(self, tmp_path: Path) -> None:
        assert launcher.count_tasks(tmp_path) == (0, 0)

    def test_zero_for_missing_folder(self, tmp_path: Path) -> None:
        assert launcher.count_tasks(tmp_path / "нет-такой") == (0, 0)

    def test_respects_depth_limit(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        (deep / "tests").mkdir(parents=True)
        assert launcher.count_tasks(tmp_path, max_depth=2) == (0, 0)

    def test_downloaded_task_without_tests_is_counted(self, tmp_path: Path) -> None:
        """issue #1018: скачанный шаг без публичных тестов — не «ноль задач».

        Загрузчик кладёт ``meta.json``; прежний счётчик видел только папки с
        ``tests``, поэтому сразу после успешного скачивания лаунчер писал
        «Найдено задач: 0» — как будто скачивание не сработало.
        """
        task = tmp_path / "12"
        task.mkdir()
        (task / "meta.json").write_text("{}", encoding="utf-8")
        (task / "solution.py").write_text("print(1)\n", encoding="utf-8")

        assert launcher.count_tasks(tmp_path) == (1, 0)

    def test_finds_task_at_downloader_depth(self, tmp_path: Path) -> None:
        """issue #1018: задача видна на глубине, которую создаёт сам загрузчик.

        `<курс>/<секция>/<урок>/<шаг>` — четвёртый уровень от рабочей папки.
        При прежней глубине обхода 3 счётчик до него не доходил, и полная папка
        скачанного курса показывалась как «Найдено задач: 0».
        """
        step = tmp_path / "курс" / "секция" / "урок" / "12"
        (step / "tests").mkdir(parents=True)
        (step / "meta.json").write_text("{}", encoding="utf-8")

        assert launcher.count_tasks(tmp_path) == (1, 1)

    def test_mixed_folder_reports_both_numbers(self, tmp_path: Path) -> None:
        """Задача с тестами и задача без них считаются раздельно."""
        (tmp_path / "with-tests" / "tests").mkdir(parents=True)
        bare = tmp_path / "bare"
        bare.mkdir()
        (bare / "meta.json").write_text("{}", encoding="utf-8")

        assert launcher.count_tasks(tmp_path) == (2, 1)


def test_stop_during_readiness_leaves_terminal_state(monkeypatch) -> None:
    """DESC-06: остановка в момент готовности не оставляет «Запуск…» навсегда.

    Прежде монитор выходил голым `return`, и статус навсегда застревал в
    STARTING: «Остановить» уже no-op, «Запустить» заблокировано — оставалось
    закрыть окно. Окно гонки — доли миллисекунды между TCP-пробой и захватом
    лока, поэтому воспроизводим его детерминированно: «Остановить» нажимается
    ровно внутри пробы.
    """
    port = _free_port()
    controller = ServerController(spawn=_spawn_running(port))

    def probe_then_user_presses_stop(_port: int) -> bool:
        controller._stopping = True
        return True

    monkeypatch.setattr(controller, "_probe", probe_then_user_presses_stop)
    try:
        controller.start(port, sandbox=False, workdir=Path.cwd())
        assert _wait_state(controller, ServerState.STOPPED)
    finally:
        controller.stop()


# ---------------------------------------------------------------------------
# issue #1135 — `stepik-grader-gui` ведёт себя как команда
#
# `pip install` ставит ДВЕ команды, но вторая не отвечала на `--help`, молча
# игнорировала любой аргумент и не давала выбрать язык окна. Всё это работает
# до создания окна, поэтому проверяется и там, где дисплея нет вовсе.
# ---------------------------------------------------------------------------


class TestLauncherCliSurface:
    def test_help_exits_zero_without_opening_window(self, capsys) -> None:
        """`--help` печатает назначение и выходит с 0, не трогая tkinter."""
        with pytest.raises(SystemExit) as exc:
            launcher.main(["--help"])

        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "stepik-grader-gui" in out
        assert "--lang" in out

    def test_version_prints_package_version(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            launcher.main(["--version"])

        assert exc.value.code == 0
        assert launcher.resolve_version() in capsys.readouterr().out

    def test_unknown_flag_is_rejected_not_ignored(self, capsys) -> None:
        """Прежде любой аргумент молча игнорировался и просто открывалось окно."""
        with pytest.raises(SystemExit) as exc:
            launcher.main(["--no-such-flag"])

        assert exc.value.code != 0
        assert "unrecognized" in capsys.readouterr().err

    def test_lang_flag_is_parsed(self) -> None:
        parser = launcher.build_arg_parser(launcher.load_ui_messages("ru"))

        assert parser.parse_args(["--lang", "en"]).lang == "en"
        assert parser.parse_args([]).lang is None  # «не выбирал» — не «ru»

    def test_lang_flag_reaches_the_window(self, monkeypatch) -> None:
        """`--lang en` доезжает до окна, а не теряется в разборе аргументов.

        `tkinter` подменяется заглушкой: в облачном окне его нет вовсе, а
        проверяется здесь не GUI, а передача выбора от argparse к окну.
        """
        import types

        monkeypatch.setitem(sys.modules, "tkinter", types.SimpleNamespace(TclError=RuntimeError))
        seen: list[str | None] = []

        class _App:
            def run(self) -> None:
                pass

        def fake_create_app(**kwargs: object) -> _App:
            seen.append(kwargs.get("lang"))  # type: ignore[arg-type]
            return _App()

        monkeypatch.setattr(launcher, "create_app", fake_create_app)

        launcher.main(["--lang", "en"])

        assert seen == ["en"]


class TestDetectLangFallback:
    """issue #1135 (LNCH-1-04): русский fallback — заявленное поведение, а не миф."""

    def test_unknown_locale_falls_back_to_russian(self, monkeypatch) -> None:
        """`LANG=C` — обычное дело в CI, контейнерах и по ssh."""
        monkeypatch.delenv(launcher.LANG_ENV_VAR, raising=False)
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            monkeypatch.setenv(var, "C")

        assert launcher.detect_lang() == "ru"

    def test_english_locale_still_gives_english(self, monkeypatch) -> None:
        monkeypatch.delenv(launcher.LANG_ENV_VAR, raising=False)
        monkeypatch.setenv("LC_ALL", "en_US.UTF-8")

        assert launcher.detect_lang() == "en"

    def test_env_var_wins_over_locale(self, monkeypatch) -> None:
        monkeypatch.setenv(launcher.LANG_ENV_VAR, "en")
        monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")

        assert launcher.detect_lang() == "en"
