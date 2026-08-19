"""Ярлык запуска лаунчера в системе (issue #1185).

Ярлык — та поверхность, которую тестами закрыть до конца нельзя: в CI нет ни
рабочего стола, ни оболочки, которая считала бы файл ярлыком. Поэтому тесты
проверяют то, что проверяемо, — а живой прогон на своей ОС остаётся отдельным
критерием приёмки.

Все каталоги подменяются: настоящий `~/Desktop` не трогается ни разу.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from stepik_grader.core import shortcut

# У NTFS нет Unix-бита исполнения: модель доступа — ACL, и `os.chmod` там
# практически no-op (тот же факт уже записан в докстринге `storage.save_secrets`
# про 0600). Проверять бит на Windows значит мерить ОС, а не наш код.
posix_only = pytest.mark.skipif(os.name == "nt", reason="бита исполнения на Windows нет")


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    """Домашний каталог с рабочим столом — во временной папке."""
    (tmp_path / "Desktop").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _gui_command_found(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`stepik-grader-gui` есть в PATH — иначе тест мерил бы окружение."""
    fake = tmp_path / "bin" / shortcut.GUI_COMMAND
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shortcut.shutil, "which", lambda name: str(fake))


class TestCreateShortcut:
    def test_linux_entry_lands_in_the_applications_menu(self, home: pathlib.Path) -> None:
        """`.desktop` в меню, а не на рабочем столе: его в части окружений нет."""
        path = shortcut.create_shortcut(home=home, system="Linux")

        assert path == home / ".local" / "share" / "applications" / "stepik-grader.desktop"
        body = path.read_text(encoding="utf-8")
        assert body.startswith("[Desktop Entry]")
        assert "Terminal=false" in body

    def test_macos_command_has_the_right_suffix(self, home: pathlib.Path) -> None:
        """Finder запускает двойным кликом именно `.command`."""
        path = shortcut.create_shortcut(home=home, system="Darwin")

        assert path.suffix == ".command"

    @posix_only
    def test_macos_command_is_executable(self, home: pathlib.Path) -> None:
        """Без бита исполнения двойной клик в Finder ничего не делает."""
        path = shortcut.create_shortcut(home=home, system="Darwin")

        assert path.stat().st_mode & 0o111

    def test_shortcut_points_at_the_gui_script(self, home: pathlib.Path) -> None:
        """Ярлык ведёт на gui-script, а не на python внутри venv.

        Путь внутри venv не переживает переустановку пакета — а ярлык обязан.
        """
        path = shortcut.create_shortcut(home=home, system="Darwin")

        assert shortcut.GUI_COMMAND in path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("system", ["Linux", "Darwin"])
    def test_second_call_does_not_multiply_shortcuts(self, home: pathlib.Path, system: str) -> None:
        """«Нажал дважды» даёт один ярлык, а не `Stepik Grader (1)`."""
        first = shortcut.create_shortcut(home=home, system=system)
        second = shortcut.create_shortcut(home=home, system=system)

        assert first == second
        assert len(list(second.parent.glob(f"*{second.suffix}"))) == 1

    def test_desktop_missing_falls_back_to_home(self, tmp_path: pathlib.Path) -> None:
        """Локализованное имя папки не угадывается — кладём туда, где найдут."""
        path = shortcut.create_shortcut(home=tmp_path, system="Darwin")

        assert path.parent == tmp_path


class TestFailuresAreExplained:
    def test_unknown_platform_names_the_alternative(self, home: pathlib.Path) -> None:
        with pytest.raises(shortcut.ShortcutError, match=shortcut.GUI_COMMAND):
            shortcut.create_shortcut(home=home, system="PlanNine")

    def test_missing_gui_script_tells_how_to_fix(
        self, home: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Не найдено» без подсказки оставляет пользователя там же, где он был."""
        monkeypatch.setattr(shortcut.shutil, "which", lambda name: None)

        with pytest.raises(shortcut.ShortcutError, match="pipx install"):
            shortcut.create_shortcut(home=home, system="Linux")

    def test_windows_reports_powershell_failure(
        self, home: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отказ PowerShell — не «ярлык создан»: причина доходит до человека."""

        def _fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], returncode=1, stdout="", stderr="access denied")

        monkeypatch.setattr(shortcut.subprocess, "run", _fail)

        with pytest.raises(shortcut.ShortcutError, match="access denied"):
            shortcut.create_shortcut(home=home, system="Windows")

    def test_unwritable_target_is_reported(
        self, home: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("read-only fs")

        monkeypatch.setattr(pathlib.Path, "write_text", _boom)

        with pytest.raises(shortcut.ShortcutError, match="read-only"):
            shortcut.create_shortcut(home=home, system="Linux")


class TestNeverCreatedImplicitly:
    """Без явного действия пользователя ярлык не появляется никогда."""

    def test_importing_the_package_creates_nothing(self, home: pathlib.Path) -> None:
        import importlib

        importlib.reload(shortcut)

        assert not list((home / "Desktop").iterdir())
        assert not (home / ".local").exists()

    def test_cli_without_the_flag_creates_nothing(
        self, home: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stepik_grader import cli

        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
        cli.main(["--version"])

        assert not (home / ".local").exists()
        assert not list((home / "Desktop").iterdir())


class TestRepoLaunchFiles:
    """`launcher.sh` / `launcher.cmd` в корне клона (issue #1185, часть C).

    Помогают только тем, у кого есть клон: после `pipx install` то же окно
    открывает `stepik-grader-gui` из любого каталога.
    """

    _ROOT = pathlib.Path(__file__).parent.parent

    def test_both_files_exist(self) -> None:
        assert (self._ROOT / "launcher.sh").is_file()
        assert (self._ROOT / "launcher.cmd").is_file()

    def test_shell_launcher_is_executable(self) -> None:
        """Бит исполнения спрашиваем у git, а не у файловой системы.

        Значение имеет режим, записанный в индексе (`100755`): именно он
        приезжает контрибьютору при клоне. Локальный `stat()` на Windows всегда
        показал бы `100666` — там Unix-бита нет вовсе, и проверка мерила бы ОС
        проверяющего вместо содержимого репозитория.
        """
        try:
            result = subprocess.run(
                ["git", "ls-files", "-s", "launcher.sh"],
                cwd=self._ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            pytest.skip(f"git недоступен: {exc}")

        if result.returncode != 0 or not (result.stdout or "").strip():
            pytest.skip("не git-клон (например, распакованный sdist)")

        assert (result.stdout or "").startswith("100755"), (
            f"launcher.sh закоммичен без бита исполнения: {result.stdout.split()[0]}"
        )

    def test_windows_launcher_uses_pythonw(self) -> None:
        """`python.exe` повесил бы консоль рядом с окном — ровно то, от чего уходим.

        Смотрим только исполняемые строки: в комментарии `python.exe` упомянут
        законно — там объясняется, почему он и НЕ используется. Первая редакция
        теста этого не различала и краснела на собственном объяснении.
        """
        lines = [
            line
            for line in (self._ROOT / "launcher.cmd").read_text(encoding="utf-8").splitlines()
            if not line.strip().lower().startswith("rem")
        ]
        code = "\n".join(lines)

        assert "pythonw.exe" in code
        assert "python.exe" not in code.replace("pythonw.exe", "")

    @posix_only
    def test_missing_venv_is_explained_not_traced(self, tmp_path: pathlib.Path) -> None:
        """Без `.venv` — понятная подсказка, а не трассировка Python.

        Только POSIX: `launcher.sh` и есть POSIX-вход, у Windows свой
        `launcher.cmd` (его разбирает `test_windows_launcher_uses_pythonw`).
        Запуск `sh launcher.sh` на Windows-раннере проверял бы Git Bash, а не
        поверхность продукта.
        """
        script = tmp_path / "launcher.sh"
        script.write_text(
            (self._ROOT / "launcher.sh").read_text(encoding="utf-8"), encoding="utf-8"
        )
        script.chmod(0o755)

        result = subprocess.run(
            ["sh", str(script)], capture_output=True, text=True, timeout=30, check=False
        )

        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert "pip install -e ." in result.stderr

    def test_windows_launcher_explains_missing_venv_too(self) -> None:
        """У `.cmd` та же подсказка, что у `.sh`, — проверяется чтением файла.

        Прогнать `launcher.cmd` можно только на Windows, а подсказка обязана
        существовать независимо от того, на какой ОС идёт проверка: иначе
        Windows-ветка теряется вместе с пропуском POSIX-теста выше.
        """
        body = (self._ROOT / "launcher.cmd").read_text(encoding="utf-8")

        assert "pip install -e ." in body
        assert "1>&2" in body, "подсказка обязана уходить в stderr, как у launcher.sh"
        assert "exit /b 1" in body, "без .venv код возврата обязан быть ненулевым"

    def test_latin_names_only(self) -> None:
        """Кириллица в именах по-разному нормализуется в git на macOS."""
        for name in ("launcher.sh", "launcher.cmd"):
            assert name.isascii()
