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
import types
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
    """issue #1134: без дисплея лаунчер делает работу, а не советует её сделать.

    Раньше все три ветки печатали `python -m stepik_grader --serve` и выходили
    с кодом 1 — команду, которую лаунчер знает целиком. Теперь он её выполняет,
    а код возврата приходит от сервера.
    """

    @staticmethod
    def _spy_serve(monkeypatch, *, code: int = 0) -> list[dict[str, object]]:
        """Подменить запуск сервера: тест не должен поднимать настоящий."""
        calls: list[dict[str, object]] = []

        def fake_serve(messages: dict[str, str], **kwargs: object) -> int:
            calls.append(kwargs)
            return code

        monkeypatch.setattr(launcher, "serve_without_gui", fake_serve)
        return calls

    def test_without_tkinter_starts_server_itself(self, monkeypatch, capsys) -> None:
        # None в sys.modules → `import tkinter` бросает ImportError.
        monkeypatch.setitem(sys.modules, "tkinter", None)
        calls = self._spy_serve(monkeypatch)

        with pytest.raises(SystemExit) as exc:
            # issue #1135: main() разбирает argv, поэтому список обязателен —
            # иначе argparse увидит аргументы самого pytest.
            launcher.main([])

        assert exc.value.code == 0
        assert calls == [{"lang": None}]
        # Сообщение — в stderr: gui-script на Windows идёт через pythonw.exe,
        # где stdout попросту некуда писать (issue #1134, PKG-1-05).
        assert "tkinter" in capsys.readouterr().err

    def test_headless_branch_runs_without_real_tkinter(self, monkeypatch, capsys) -> None:
        """Та же headless-ветка, но БЕЗ настоящего tkinter — значит и в облаке.

        Соседний тест начинается с `importorskip("tkinter")`, поэтому там, где
        tkinter не собран (облачная сессия), он скипается — и три раза подряд
        расхождение сигнатур ловили только раннеры CI. Здесь tkinter подменён
        заглушкой: проверяется поведение ветки, а не GUI.
        """
        import types

        monkeypatch.setitem(sys.modules, "tkinter", types.SimpleNamespace(TclError=RuntimeError))
        calls = self._spy_serve(monkeypatch, code=3)

        def _raise(**_kwargs: object) -> None:
            raise RuntimeError("no display")

        monkeypatch.setattr(launcher, "create_app", _raise)

        with pytest.raises(SystemExit) as exc:
            launcher.main([])

        assert exc.value.code == 3, "код возврата сервера подменён своим"
        assert calls == [{"lang": None}]

    def test_headless_tclerror_starts_server_with_chosen_lang(self, monkeypatch, capsys) -> None:
        tk = pytest.importorskip("tkinter")
        calls = self._spy_serve(monkeypatch)

        # issue #1135: main() зовёт create_app(lang=...), поэтому заглушка
        # обязана принимать kwargs — иначе тест падает TypeError вместо
        # проверки самой headless-ветки.
        def _raise(**_kwargs: object) -> LauncherApp:
            raise tk.TclError("no display")

        monkeypatch.setattr(launcher, "create_app", _raise)
        with pytest.raises(SystemExit) as exc:
            # issue #1135: как и в тесте выше — main() разбирает argv, поэтому
            # без явного списка argparse увидит аргументы самого pytest.
            launcher.main(["--lang", "en"])

        assert exc.value.code == 0
        assert calls == [{"lang": "en"}], "выбор языка не доехал до сервера"
        assert "display" in capsys.readouterr().err.lower()


class TestServeWithoutGui:
    """issue #1134: сама headless-ветка — GUI-free, проверяется везде."""

    @staticmethod
    def _messages() -> dict[str, str]:
        return launcher.load_ui_messages("ru")

    def test_starts_server_and_returns_its_code(self, monkeypatch, tmp_path) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr(launcher, "our_server_on", lambda *a, **k: False)
        monkeypatch.setattr(launcher, "port_available", lambda *a, **k: True)
        monkeypatch.setattr(launcher.subprocess, "call", lambda cmd: seen.append(cmd) or 7)

        code = launcher.serve_without_gui(self._messages(), port=8123, workdir=tmp_path)

        assert code == 7, "код возврата сервера потерян"
        (command,) = seen
        assert "--serve" in command
        assert command[command.index("--port") + 1] == "8123"
        assert command[command.index("--root") + 1] == str(tmp_path)
        assert "--sandbox" not in command  # изоляция — явный выбор, не дефолт

    def test_running_server_is_not_duplicated(self, monkeypatch, capsys) -> None:
        """На порту уже наш сервер — второй не поднимаем, печатаем адрес."""
        monkeypatch.setattr(launcher, "our_server_on", lambda *a, **k: True)
        monkeypatch.setattr(
            launcher.subprocess, "call", lambda cmd: pytest.fail("сервер запущен повторно")
        )

        assert launcher.serve_without_gui(self._messages(), port=8321) == 0
        assert "8321" in capsys.readouterr().err

    def test_busy_port_falls_back_to_the_next_free_one(self, monkeypatch, tmp_path) -> None:
        """Порт занят чужим — берём следующий свободный, как и окно (#1146)."""
        seen: list[list[str]] = []
        monkeypatch.setattr(launcher, "our_server_on", lambda *a, **k: False)
        monkeypatch.setattr(launcher, "port_available", lambda port, **k: port != 8000)
        monkeypatch.setattr(launcher.subprocess, "call", lambda cmd: seen.append(cmd) or 0)

        launcher.serve_without_gui(self._messages(), workdir=tmp_path)

        (command,) = seen
        assert command[command.index("--port") + 1] == "8001"

    def test_no_free_port_reports_and_exits_nonzero(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(launcher, "our_server_on", lambda *a, **k: False)
        monkeypatch.setattr(launcher, "port_available", lambda *a, **k: False)
        monkeypatch.setattr(
            launcher.subprocess, "call", lambda cmd: pytest.fail("сервер запущен без порта")
        )

        assert launcher.serve_without_gui(self._messages(), port=8000) == 1
        assert "8000" in capsys.readouterr().err

    def test_lang_choice_reaches_the_server(self, monkeypatch, tmp_path) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr(launcher, "our_server_on", lambda *a, **k: False)
        monkeypatch.setattr(launcher, "port_available", lambda *a, **k: True)
        monkeypatch.setattr(launcher.subprocess, "call", lambda cmd: seen.append(cmd) or 0)

        launcher.serve_without_gui(self._messages(), lang="en", workdir=tmp_path)
        launcher.serve_without_gui(self._messages(), lang=None, workdir=tmp_path)

        with_lang, without_lang = seen
        assert with_lang[with_lang.index("--lang") + 1] == "en"
        # «не выбирал» — флага нет вовсе: дефолт резолвит сервер (ADR-0012).
        assert "--lang" not in without_lang


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
def tk_window(_tk_module, monkeypatch, tmp_path):
    """Отдельный withdrawn Toplevel на тест поверх общего Tk-рута модуля.

    Язык окна фиксируется явно (issue #821): подписи локализованы, а язык по
    умолчанию берётся из системной локали — без фиксации проверки русских строк
    падали бы на англоязычных раннерах Windows/macOS, оставаясь зелёными на
    русской машине разработчика. Английский путь проверяется отдельным тестом.

    Корень настроек уводится в ``tmp_path`` (issue #1133): окно теперь ЧИТАЕТ
    запомненный выбор при открытии, и без изоляции тест на машине разработчика
    подхватил бы его настоящий `.grader_settings.json` — а заодно перезаписал бы
    его при проверке старта.
    """
    monkeypatch.setenv(launcher.LANG_ENV_VAR, "ru")
    monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)
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

    def test_window_opens_with_remembered_choice(self, tk_window, tmp_path) -> None:
        """issue #1133: связка окна с памятью — то, что GUI-free тесты не видят.

        Логика восстановления проверяется отдельно и без дисплея
        (`TestRemembersChoice`), но что конструктор её ВЫЗЫВАЕТ — только здесь.
        Тест скипается там, где нет дисплея, то есть во всём CI: это поверхность
        локального окна, и в PR она отмечена как проверка руками.
        """
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        launcher.remember_launch_choice(
            launcher.LaunchChoice(sandbox=True, record_history=False, port=8321, workdir=tasks)
        )

        app = LauncherApp(tk_window, ServerController())
        tk_window.update()

        assert app.port_var.get() == "8321"
        assert app.sandbox_var.get() is True
        assert app.workdir_var.get() == str(tasks)

    def test_start_remembers_the_choice(self, tk_window, tmp_path, monkeypatch) -> None:
        """Выбор сохраняется при запуске, а не при закрытии окна.

        Окно, закрытое сразу после старта сервера (обычное дело — оно больше не
        нужно), иначе не оставило бы следа.
        """
        app = LauncherApp(tk_window, ServerController())
        tk_window.update()
        app.port_var.set("8322")
        app.sandbox_var.set(True)
        app.workdir_var.set(str(tmp_path))
        monkeypatch.setattr(launcher, "port_available", lambda *a, **k: True)
        monkeypatch.setattr(app.controller, "start", lambda *a, **k: None)

        app._start()

        remembered = launcher.remembered_launch_choice()
        assert remembered.port == 8322
        assert remembered.sandbox is True
        assert remembered.workdir == tmp_path

    def test_widgets_follow_selected_language(self, _tk_module, monkeypatch, tmp_path) -> None:
        """issue #821: под английским языком окно строится с английскими подписями."""
        monkeypatch.setenv(launcher.LANG_ENV_VAR, "en")
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)
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


# ---------------------------------------------------------------------------
# issue #1134 — занятый порт перестал быть тупиком
#
# «Порт занят, выберите другой» — при том что лаунчер умеет проверять порты, а
# чаще всего на порту стоит наш же сервер с прошлого запуска, и нужное действие
# не «смени порт», а «открой его».
# ---------------------------------------------------------------------------


class TestNextFreePort:
    def test_skips_busy_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((DEFAULT_HOST, 0))
            sock.listen()
            busy = sock.getsockname()[1]

            assert launcher.next_free_port(busy) != busy

    def test_returns_start_when_free(self) -> None:
        free = _free_port()
        assert launcher.next_free_port(free) == free

    def test_none_when_nothing_free_in_window(self, monkeypatch) -> None:
        """Все кандидаты заняты — гадать дальше бессмысленно, там системное."""
        monkeypatch.setattr(launcher, "port_available", lambda *a, **kw: False)

        assert launcher.next_free_port(9000, attempts=3) is None


class TestOurServerOn:
    def test_false_for_closed_port(self) -> None:
        """Сетевая ошибка — «не наш»: проба не должна ронять лаунчер."""
        assert launcher.our_server_on(_free_port(), timeout=0.2) is False

    def test_false_for_foreign_listener(self) -> None:
        """Чужой слушатель на порту нашим сервером не считается."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((DEFAULT_HOST, 0))
            sock.listen()
            port = sock.getsockname()[1]

            assert launcher.our_server_on(port, timeout=0.3) is False

    def test_true_when_page_carries_our_marker(self) -> None:
        """Наш сервер узнаётся по маркеру, который страница несёт всегда."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # имя задано базовым классом BaseHTTPRequestHandler
                body = b'<body data-sandbox="false" data-record-history="true">'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        httpd = HTTPServer((DEFAULT_HOST, 0), _Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            assert launcher.our_server_on(httpd.server_address[1], timeout=2.0) is True
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestZeroTasksLine:
    """issue #1134: «Найдено задач: 0» — развилка, а не счёт.

    Первокурсник, ради которого лаунчер и сделан, из нуля не узнаёт ни что
    задачи скачиваются загрузчиком, ни что он мог промахнуться папкой.

    Тесты GUI-free: метод `_refresh_tasks_found` трогает только `workdir_var`,
    `tasks_var` и каталог сообщений, поэтому вызывается на заглушке — иначе
    проверка жила бы только там, где собран tkinter, а это ровно те окружения,
    где её никто не увидит (см. соседний headless-класс).
    """

    class _Var:
        def __init__(self, value: str = "") -> None:
            self._value = value

        def get(self) -> str:
            return self._value

        def set(self, value: str) -> None:
            self._value = value

    def _line_for(self, workdir: Path) -> str:
        stub = types.SimpleNamespace(
            workdir_var=self._Var(str(workdir)),
            tasks_var=self._Var(),
            _messages=launcher.load_ui_messages("ru"),
        )
        stub._t = launcher.LauncherApp._t.__get__(stub)
        launcher.LauncherApp._refresh_tasks_found(stub)
        return str(stub.tasks_var.get())

    def test_empty_folder_offers_a_next_step(self, tmp_path: Path) -> None:
        line = self._line_for(tmp_path)

        assert "0" not in line, "ноль показан как счёт, а не как развилка"
        assert "загрузчик" in line.lower()  # откуда задачи берутся
        assert "папк" in line.lower()  # и что можно было промахнуться папкой

    def test_folder_with_tasks_keeps_the_count(self, tmp_path: Path) -> None:
        """Непустая папка — прежняя строка со счётчиком, поведение не тронуто."""
        task = tmp_path / "01-task"
        (task / "tests").mkdir(parents=True)
        (task / "main.py").write_text("print(1)", encoding="utf-8")
        (task / "tests" / "1").write_text("in", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("out", encoding="utf-8")

        assert "1" in self._line_for(tmp_path)

    def test_missing_folder_still_says_so(self, tmp_path: Path) -> None:
        """Несуществующая папка — своё сообщение, не «задач не найдено»."""
        line = self._line_for(tmp_path / "нет-такой")

        assert "не найдена" in line.lower()


class TestRemembersChoice:
    """issue #1133: окно открывается там, где его закрыли.

    Раньше каждый запуск начинался с нуля — порт 8000, изоляция выключена,
    папка вычислена заново. Для инструмента, который открывают ежедневно, это
    значит, что работающий с изоляцией включает её каждый раз, пока однажды не
    забудет: тихая потеря настройки безопасности, а не просто неудобство.

    Всё GUI-free: логика восстановления вынесена из конструктора окна ровно
    затем, чтобы её проверял не только тот, у кого есть дисплей.
    """

    def test_saved_choice_comes_back(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        choice = launcher.LaunchChoice(
            sandbox=True, record_history=False, lang="en", port=8321, workdir=tasks
        )

        launcher.remember_launch_choice(choice)

        assert launcher.remembered_launch_choice() == choice

    def test_first_run_has_no_memory(self, tmp_path: Path, monkeypatch) -> None:
        """Файла ещё нет — пустой выбор, а не падение и не мусор."""
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)

        assert launcher.remembered_launch_choice() == launcher.LaunchChoice()

    def test_unwritable_settings_do_not_block_launch(self, tmp_path: Path, monkeypatch) -> None:
        """Память — не обязательство: сервер запускается, даже если запись упала."""
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(launcher, "save_fields", refuse)

        launcher.remember_launch_choice(launcher.LaunchChoice(sandbox=True))  # без исключения

    def test_remembered_values_fill_the_window(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        remembered = launcher.LaunchChoice(
            sandbox=True, record_history=False, lang="en", port=8321, workdir=tasks
        )

        initial = launcher.initial_launch_values(remembered, fallback_dir=tmp_path)

        assert initial.port == 8321
        assert initial.sandbox is True
        assert initial.workdir == tasks
        assert initial.lang == "en"
        assert initial.record_history is False

    def test_empty_memory_falls_back_to_defaults(self, tmp_path: Path) -> None:
        initial = launcher.initial_launch_values(launcher.LaunchChoice(), fallback_dir=tmp_path)

        assert initial.port == launcher.DEFAULT_PORT
        assert initial.sandbox is False
        assert initial.workdir == tmp_path
        # «Не выбирал» остаётся None — это «унаследовать», а не «выключено».
        assert initial.record_history is None

    def test_vanished_workdir_falls_back(self, tmp_path: Path) -> None:
        """Папка с прошлого раза исчезла (внешний диск, переезд проекта).

        Окно, открытое на несуществующем пути, показало бы ноль задач — и повод
        думать, что пропали они, а не папка.
        """
        remembered = launcher.LaunchChoice(workdir=tmp_path / "унесённая-флешка")

        initial = launcher.initial_launch_values(remembered, fallback_dir=tmp_path)

        assert initial.workdir == tmp_path

    def test_lang_flag_beats_memory(self, tmp_path: Path) -> None:
        """`--lang` — решение «на сейчас», память только предлагает прошлое."""
        remembered = launcher.LaunchChoice(lang="ru")

        initial = launcher.initial_launch_values(remembered, lang_flag="en", fallback_dir=tmp_path)

        assert initial.lang == "en"


class TestStartRemembersWithoutDisplay:
    """issue #1133: сохранение при старте — без tkinter, значит и в CI.

    `_start` трогает только переменные окна и контроллер, поэтому вызывается на
    заглушке. Через настоящее окно эта связка проверялась бы лишь там, где есть
    дисплей, — то есть ни в одном job'е матрицы.
    """

    class _Var:
        def __init__(self, value: object = "") -> None:
            self._value = value

        def get(self) -> object:
            return self._value

        def set(self, value: object) -> None:
            self._value = value

    def test_start_saves_the_choice(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)
        monkeypatch.setattr(launcher, "port_available", lambda *a, **k: True)
        started: list[dict[str, object]] = []

        stub = types.SimpleNamespace(
            port_var=self._Var("8324"),
            workdir_var=self._Var(str(tmp_path)),
            sandbox_var=self._Var(True),
            lang_var=self._Var("en"),
            controller=types.SimpleNamespace(
                host=launcher.DEFAULT_HOST,
                start=lambda *a, **k: started.append(k),
            ),
            _messages=launcher.load_ui_messages("ru"),
        )
        stub._t = launcher.LauncherApp._t.__get__(stub)
        stub.selected_record_history = lambda: False
        stub._set_status = lambda *a, **k: None

        launcher.LauncherApp._start(stub)

        assert started, "сервер не запущен — тест проверяет не тот путь"
        remembered = launcher.remembered_launch_choice()
        assert remembered.port == 8324
        assert remembered.sandbox is True
        assert remembered.lang == "en"
        assert remembered.record_history is False
        assert remembered.workdir == tmp_path

    def test_invalid_port_saves_nothing(self, tmp_path: Path, monkeypatch) -> None:
        """Невалидный ввод не попадает в память: запомнить стоит только запуск."""
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)

        stub = types.SimpleNamespace(
            port_var=self._Var("не число"),
            workdir_var=self._Var(str(tmp_path)),
            sandbox_var=self._Var(True),
            lang_var=self._Var("ru"),
            controller=types.SimpleNamespace(host=launcher.DEFAULT_HOST),
            _messages=launcher.load_ui_messages("ru"),
        )
        stub._t = launcher.LauncherApp._t.__get__(stub)
        stub.selected_record_history = lambda: None
        stub._set_status = lambda *a, **k: None

        launcher.LauncherApp._start(stub)

        assert launcher.remembered_launch_choice() == launcher.LaunchChoice()


class TestProfilesInWindow:
    """issue #1133 (шаг 2): профиль выбирается в окне, а не только флагом.

    Ядро GUI-free: список имён, восстановление выбранного, применение профиля
    к полям. Обработчики окна вызываются на заглушке — через настоящий tkinter
    они проверялись бы лишь там, где есть дисплей, то есть ни в одном job'е.
    """

    class _Var:
        def __init__(self, value: object = "") -> None:
            self._value = value

        def get(self) -> object:
            return self._value

        def set(self, value: object) -> None:
            self._value = value

    class _Box:
        def __init__(self) -> None:
            self.values: list[str] = []

        def config(self, **kwargs: object) -> None:
            self.values = list(kwargs.get("values", ()))  # type: ignore[arg-type]

        def set(self, value: object) -> None:
            self.value = value

    @pytest.fixture
    def workspace(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(launcher, "default_workdir", lambda: tmp_path)
        return tmp_path

    def _stub(self, workspace: Path):
        stub = types.SimpleNamespace(
            profile_var=self._Var(""),
            port_var=self._Var("8000"),
            workdir_var=self._Var(str(workspace)),
            sandbox_var=self._Var(False),
            lang_var=self._Var("ru"),
            history_box=self._Box(),
            profile_box=self._Box(),
            _messages=launcher.load_ui_messages("ru"),
            root=None,
        )
        stub._t = launcher.LauncherApp._t.__get__(stub)
        stub._history_label = launcher.LauncherApp._history_label.__get__(stub)
        stub._profile_values = launcher.LauncherApp._profile_values.__get__(stub)
        stub._selected_profile_name = launcher.LauncherApp._selected_profile_name.__get__(stub)
        stub._current_choice = launcher.LauncherApp._current_choice.__get__(stub)
        stub._refresh_profiles = launcher.LauncherApp._refresh_profiles.__get__(stub)
        stub.selected_record_history = lambda: None
        stub.status: list[str] = []
        stub._set_status = lambda text, error=False: stub.status.append(text)
        return stub

    def test_names_are_alphabetical(self, workspace: Path) -> None:
        """Список читают глазами — «где-то в середине» худший способ искать своё."""
        path = launcher.launcher_settings_path()
        for name in ("яблоко", "берёза", "абрикос"):
            launcher.save_launch_profile(path, name, launcher.LaunchChoice(port=8000))

        assert launcher.profile_names() == ["абрикос", "берёза", "яблоко"]

    def test_remembered_name_survives(self, workspace: Path) -> None:
        launcher.save_launch_profile(
            launcher.launcher_settings_path(), "как обычно", launcher.LaunchChoice(port=8001)
        )
        launcher.remember_profile_name("как обычно")

        assert launcher.remembered_profile_name() == "как обычно"

    def test_deleted_profile_is_not_shown_as_selected(self, workspace: Path) -> None:
        """Имя, за которым уже ничего нет, показывать нельзя.

        Профиль мог удалить второе окно или правка файла руками — тогда список
        предлагал бы выбор, который ничего не восстанавливает.
        """
        path = launcher.launcher_settings_path()
        launcher.save_launch_profile(path, "временный", launcher.LaunchChoice(port=8002))
        launcher.remember_profile_name("временный")
        launcher.delete_launch_profile(path, "временный")

        assert launcher.remembered_profile_name() == ""

    def test_selecting_profile_fills_the_fields(self, workspace: Path) -> None:
        tasks = workspace / "tasks"
        tasks.mkdir()
        launcher.save_launch_profile(
            launcher.launcher_settings_path(),
            "с изоляцией",
            launcher.LaunchChoice(sandbox=True, port=8123, lang="en", workdir=tasks),
        )
        stub = self._stub(workspace)
        stub.profile_var.set("с изоляцией")

        launcher.LauncherApp._on_profile_selected(stub)

        assert stub.sandbox_var.get() is True
        assert stub.port_var.get() == "8123"
        assert stub.lang_var.get() == "en"
        assert stub.workdir_var.get() == str(tasks)
        assert launcher.remembered_profile_name() == "с изоляцией"

    def test_custom_set_touches_nothing(self, workspace: Path) -> None:
        """«Свой набор» — состояние, а не профиль: введённое им не стирается."""
        stub = self._stub(workspace)
        stub.port_var.set("9999")
        stub.profile_var.set(stub._t("launcher_profile_custom"))

        launcher.LauncherApp._on_profile_selected(stub)

        assert stub.port_var.get() == "9999"

    def test_current_choice_snapshots_the_form(self, workspace: Path) -> None:
        stub = self._stub(workspace)
        stub.port_var.set("8321")
        stub.sandbox_var.set(True)

        choice = stub._current_choice()

        assert choice.port == 8321
        assert choice.sandbox is True
        assert choice.workdir == workspace

    def test_invalid_port_does_not_break_the_snapshot(self, workspace: Path) -> None:
        """Полуформа сохраняется без порта, а не падает: имя уже введено."""
        stub = self._stub(workspace)
        stub.port_var.set("не число")

        assert stub._current_choice().port is None

    def test_deleting_profile_keeps_form_intact(self, workspace: Path) -> None:
        """Удаляют ЗАПИСЬ, а не текущий выбор — обнулять форму значит наказывать за уборку."""
        path = launcher.launcher_settings_path()
        launcher.save_launch_profile(path, "лишний", launcher.LaunchChoice(port=8004))
        stub = self._stub(workspace)
        stub.profile_var.set("лишний")
        stub.port_var.set("8888")

        launcher.LauncherApp._on_delete_profile(stub)

        assert stub.port_var.get() == "8888"
        assert launcher.profile_names() == []

    def test_delete_without_selection_says_so(self, workspace: Path) -> None:
        stub = self._stub(workspace)
        stub.profile_var.set(stub._t("launcher_profile_custom"))

        launcher.LauncherApp._on_delete_profile(stub)

        assert any("выберите профиль" in text.lower() for text in stub.status), stub.status

    def test_profile_list_starts_with_custom_entry(self, workspace: Path) -> None:
        launcher.save_launch_profile(
            launcher.launcher_settings_path(), "первый", launcher.LaunchChoice(port=8005)
        )
        stub = self._stub(workspace)

        values = stub._profile_values()

        assert values[0] == stub._t("launcher_profile_custom")
        assert values[1:] == ["первый"]


class TestAdvancedTabHandlers:
    """issue #1136: вкладка «Дополнительно» — обработчики без tkinter.

    Как и профили (#1133), проверяются на заглушке: настоящее окно живёт только
    там, где есть дисплей, то есть ни в одном job'е CI и ни в одной облачной
    сессии. Заглушка подделывает переменные и виджеты, но методы берутся
    настоящие — расхождение сигнатур ловится здесь, а не глазами.
    """

    class _Var:
        def __init__(self, value: object = "") -> None:
            self._value = value

        def get(self) -> object:
            return self._value

        def set(self, value: object) -> None:
            self._value = value

    class _Widget:
        """Виджет, помнящий последний ``state`` — им и проверяется блокировка."""

        def __init__(self) -> None:
            self.state = "normal"

        def config(self, **kwargs: object) -> None:
            if "state" in kwargs:
                self.state = str(kwargs["state"])

    @pytest.fixture
    def workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from stepik_grader import config

        monkeypatch.chdir(tmp_path)
        config.set_workspace_root(tmp_path)
        config.reset_config_cache()
        try:
            yield tmp_path
        finally:
            config.set_workspace_root(None)
            config.reset_config_cache()

    def _stub(self):
        from stepik_grader.core import settings_resolver

        names = [item.name for item in settings_resolver.advanced_settings()]
        stub = types.SimpleNamespace(
            setting_vars={
                item.name: self._Var(
                    False
                    if item.kind == "bool"
                    else str(settings_resolver.describe_setting(item.name).value or "")
                )
                for item in settings_resolver.advanced_settings()
            },
            setting_state_vars={name: self._Var("") for name in names},
            setting_widgets={name: self._Widget() for name in names},
            setting_buttons={name: [self._Widget(), self._Widget()] for name in names},
            unsafe_var=self._Var(False),
            unsafe_check=self._Widget(),
            port_entry=self._Widget(),
            workdir_entry=self._Widget(),
            browse_btn=self._Widget(),
            radio_simple=self._Widget(),
            radio_sandbox=self._Widget(),
            _messages=launcher.load_ui_messages("ru"),
        )
        stub._t = LauncherApp._t.__get__(stub)
        stub.status: list[tuple[str, bool]] = []
        stub._set_status = lambda text, error=False: stub.status.append((text, error))
        for method in (
            "_setting_display",
            "_setting_state_text",
            "_refresh_setting_state",
            "_apply_setting",
            "_reset_setting",
            "_on_unsafe_toggled",
            "_set_inputs_enabled",
        ):
            setattr(stub, method, getattr(LauncherApp, method).__get__(stub))
        return stub

    def test_applied_value_reaches_the_settings_file(self, workspace: Path) -> None:
        from stepik_grader.core.user_settings import SETTINGS_FILE_NAME, load_settings

        stub = self._stub()
        stub.setting_vars["timeout_seconds"].set("30")

        stub._apply_setting("timeout_seconds")

        stored = load_settings(workspace / SETTINGS_FILE_NAME).run_settings
        assert stored == {"timeout_seconds": 30.0}

    def test_applied_value_is_reported_by_its_human_name(self, workspace: Path) -> None:
        stub = self._stub()
        stub.setting_vars["job_workers"].set("4")

        stub._apply_setting("job_workers")

        text, error = stub.status[-1]
        assert error is False
        assert launcher.load_ui_messages("ru")["setting_job_workers"] in text

    def test_rejected_value_is_not_saved(self, workspace: Path) -> None:
        from stepik_grader.core.user_settings import SETTINGS_FILE_NAME, load_settings

        stub = self._stub()
        stub.setting_vars["job_workers"].set("0")  # конфиг требует >= 1

        stub._apply_setting("job_workers")

        assert load_settings(workspace / SETTINGS_FILE_NAME).run_settings == {}
        assert stub.status[-1][1] is True

    def test_rejected_value_leaves_the_control_showing_what_is_stored(
        self, workspace: Path
    ) -> None:
        """Поле не хранит отвергнутое: иначе следующий взгляд примет его за действующее.

        Именно так липкая настройка и обманывает: в окне «0», в прогоне —
        двойка из конфига, и ничто на экране не говорит, какое из двух работает.
        """
        stub = self._stub()
        stub._apply_setting("job_workers")  # исходное значение из конфига
        stub.setting_vars["job_workers"].set("0")

        stub._apply_setting("job_workers")

        assert stub.setting_vars["job_workers"].get() == "2"

    def test_empty_memory_limit_is_saved_as_no_limit(self, workspace: Path) -> None:
        """Пусто у nullable-поля — осмысленный выбор «без лимита», а не ошибка ввода."""
        from stepik_grader.core.user_settings import SETTINGS_FILE_NAME, load_settings

        stub = self._stub()
        stub.setting_vars["max_memory_mb"].set("")

        stub._apply_setting("max_memory_mb")

        assert load_settings(workspace / SETTINGS_FILE_NAME).run_settings == {"max_memory_mb": None}

    def test_reset_removes_the_key(self, workspace: Path) -> None:
        from stepik_grader.core.user_settings import SETTINGS_FILE_NAME, load_settings

        stub = self._stub()
        stub.setting_vars["timeout_seconds"].set("30")
        stub._apply_setting("timeout_seconds")

        stub._reset_setting("timeout_seconds")

        assert load_settings(workspace / SETTINGS_FILE_NAME).run_settings == {}
        assert stub.setting_vars["timeout_seconds"].get() == "10.0"

    def test_state_line_names_the_origin(self, workspace: Path) -> None:
        """Откуда значение — видно всегда: свой выбор месячной давности не помнят."""
        messages = launcher.load_ui_messages("ru")
        stub = self._stub()

        stub._refresh_setting_state("timeout_seconds")
        untouched = str(stub.setting_state_vars["timeout_seconds"].get())

        stub.setting_vars["timeout_seconds"].set("30")
        stub._apply_setting("timeout_seconds")
        changed = str(stub.setting_state_vars["timeout_seconds"].get())

        assert messages["settings_origin_default"] in untouched
        assert messages["settings_origin_user"] in changed

    def test_project_value_is_named_as_such(self, workspace: Path) -> None:
        """Значение из pyproject.toml не выдаётся за пользовательский выбор."""
        from stepik_grader import config

        (workspace / "pyproject.toml").write_text(
            "[tool.stepik-grader]\ntimeout_seconds = 7.5\n", encoding="utf-8"
        )
        config.reset_config_cache()
        stub = self._stub()

        stub._refresh_setting_state("timeout_seconds")

        text = str(stub.setting_state_vars["timeout_seconds"].get())
        assert launcher.load_ui_messages("ru")["settings_origin_pyproject"] in text
        assert "7.5" in text

    def test_sandbox_quotas_start_locked(self, workspace: Path) -> None:
        from stepik_grader.core import settings_resolver

        stub = self._stub()
        stub._on_unsafe_toggled()

        for item in settings_resolver.advanced_settings("unsafe"):
            assert stub.setting_widgets[item.name].state == "disabled"
            assert stub.setting_buttons[item.name][0].state == "disabled"

    def test_confirmation_unlocks_only_the_unsafe_block(self, workspace: Path) -> None:
        from stepik_grader.core import settings_resolver

        stub = self._stub()
        stub.unsafe_var.set(True)

        stub._on_unsafe_toggled()

        for item in settings_resolver.advanced_settings("unsafe"):
            assert stub.setting_widgets[item.name].state == "normal"
        # Соседние блоки галка не трогает — она про изоляцию, а не про вкладку.
        assert stub.setting_widgets["timeout_seconds"].state == "normal"

    def test_running_server_freezes_the_settings(self, workspace: Path) -> None:
        """Дочерний сервер читает настройки один раз, на старте.

        Живой контрол на работающем сервере принимал бы правку, которая ничего
        не меняет до перезапуска, — ровно та молча не сработавшая настройка,
        против которой вкладка и сделана.
        """
        stub = self._stub()

        stub._set_inputs_enabled(False)

        assert stub.setting_widgets["timeout_seconds"].state == "disabled"
        assert stub.setting_buttons["timeout_seconds"][0].state == "disabled"
        assert stub.unsafe_check.state == "disabled"

    def test_stopping_server_returns_the_settings_but_keeps_the_gate(self, workspace: Path) -> None:
        """Разблокировка не открывает квоты песочницы: подтверждение снято."""
        stub = self._stub()
        stub._set_inputs_enabled(False)

        stub._set_inputs_enabled(True)

        assert stub.setting_widgets["timeout_seconds"].state == "normal"
        assert stub.setting_widgets["sandbox_max_processes"].state == "disabled"

    def test_choice_control_is_readonly_not_free_text(self, workspace: Path) -> None:
        """Комбобокс остаётся readonly — иначе в него впечатают несуществующий режим."""
        stub = self._stub()

        stub._set_inputs_enabled(True)

        assert stub.setting_widgets["compare_mode"].state == "readonly"


class TestAdvancedTabIsBuilt:
    """issue #1136: вкладка собирается целиком — на подменённом tkinter.

    В облачной сессии и во всех job'ах CI ``tkinter`` не собран, поэтому
    настоящее окно не строится нигде: опечатка в имени виджета или забытый
    ключ каталога дошли бы до пользователя первыми. Заглушка отвечает на
    единственный вопрос, ради которого сюда стоит лезть, — собирается ли
    вкладка и по описаниям ли из ядра.
    """

    class _Var:
        def __init__(self, value: object = None, **_kwargs: object) -> None:
            self._value = value

        def get(self) -> object:
            return self._value

        def set(self, value: object) -> None:
            self._value = value

        def trace_add(self, *_args: object, **_kwargs: object) -> None:
            pass

    class _Widget:
        """Виджет-заглушка: помнит, с какими параметрами создан и настроен."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.parent = args[0] if args else None
            self.kwargs = dict(kwargs)
            self.grid_kwargs: dict[str, object] = {}

        def config(self, **kwargs: object) -> None:
            self.kwargs.update(kwargs)

        configure = config

        def grid(self, **kwargs: object) -> None:
            self.grid_kwargs = dict(kwargs)

        def __getattr__(self, _name: str):
            return lambda *args, **kwargs: None

    class _Canvas(_Widget):
        def create_window(self, *_args: object, **_kwargs: object) -> int:
            return 1

        def bbox(self, _what: str) -> tuple[int, int, int, int]:
            return (0, 0, 10, 10)

    class _Notebook(_Widget):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.tabs: list[tuple[object, str]] = []

        def add(self, child: object, text: str = "") -> None:
            self.tabs.append((child, text))

    def _fake_tk(self, monkeypatch: pytest.MonkeyPatch):
        ttk = types.SimpleNamespace(
            Frame=self._Widget,
            Label=self._Widget,
            Button=self._Widget,
            Entry=self._Widget,
            Combobox=self._Widget,
            Checkbutton=self._Widget,
            Scrollbar=self._Widget,
            Notebook=self._Notebook,
        )
        tk = types.SimpleNamespace(
            TclError=RuntimeError,
            Canvas=self._Canvas,
            BooleanVar=self._Var,
            StringVar=self._Var,
            ttk=ttk,
        )
        monkeypatch.setitem(sys.modules, "tkinter", tk)
        monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
        return tk

    @pytest.fixture
    def built(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from stepik_grader import config

        monkeypatch.chdir(tmp_path)
        config.set_workspace_root(tmp_path)
        config.reset_config_cache()
        tk = self._fake_tk(monkeypatch)
        stub = types.SimpleNamespace(
            _tk=tk,
            notebook=self._Notebook(),
            _messages=launcher.load_ui_messages("ru"),
        )
        stub._t = LauncherApp._t.__get__(stub)
        for method in (
            "_build_advanced_tab",
            "_build_unsafe_gate",
            "_build_setting_row",
            "_setting_display",
            "_setting_state_text",
            "_refresh_setting_state",
            "_apply_setting",
            "_reset_setting",
            "_on_unsafe_toggled",
        ):
            setattr(stub, method, getattr(LauncherApp, method).__get__(stub))
        try:
            stub._build_advanced_tab()
            yield stub
        finally:
            config.set_workspace_root(None)
            config.reset_config_cache()

    def test_every_declared_setting_gets_a_control(self, built) -> None:
        from stepik_grader.core import settings_resolver

        expected = {item.name for item in settings_resolver.advanced_settings()}

        assert set(built.setting_widgets) == expected
        assert set(built.setting_vars) == expected
        assert set(built.setting_buttons) == expected

    def test_tab_is_added_to_the_notebook(self, built) -> None:
        assert (
            built.notebook.tabs[-1][1] == launcher.load_ui_messages("ru")["launcher_tab_advanced"]
        )

    def test_choice_control_lists_the_allowed_values(self, built) -> None:
        assert built.setting_widgets["compare_mode"].kwargs["values"] == ["stepik", "strict"]

    def test_every_group_gets_a_heading(self, built) -> None:
        from stepik_grader.core import settings_resolver

        assert set(built._group_labels) == set(settings_resolver.ADVANCED_GROUPS)

    def test_labels_come_from_the_catalogue_not_raw_keys(self, built) -> None:
        """Подпись — текст из локали, а не служебный идентификатор.

        Пропущенный ключ каталога виден именно так: вместо «Таймаут решения, с»
        в окне стоит «setting_timeout_seconds».
        """
        for name, label in built.setting_labels.items():
            assert label.kwargs["text"] != f"setting_{name}"

    def test_sandbox_quotas_are_born_disabled(self, built) -> None:
        """Квоты закрыты ДО того, как окно показано, а не после первого клика."""
        from stepik_grader.core import settings_resolver

        for item in settings_resolver.advanced_settings("unsafe"):
            assert built.setting_widgets[item.name].kwargs["state"] == "disabled"
            assert built.setting_buttons[item.name][0].kwargs["state"] == "disabled"

    def test_nullable_hint_says_what_empty_means(self, built) -> None:
        messages = launcher.load_ui_messages("ru")

        text = built.setting_hints["max_memory_mb"].kwargs["text"]

        assert messages["settings_empty_means_none"] in text
        assert (
            messages["settings_empty_means_none"]
            not in built.setting_hints["job_workers"].kwargs["text"]
        )
