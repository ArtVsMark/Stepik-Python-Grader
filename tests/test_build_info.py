"""issue #1262: логическая версия приезжает в пакет файлом, а не считается на лету.

Три вопроса, на которые отвечают тесты ниже.

1. **Что показывать человеку.** Полная PEP 440-версия (`1.10.0.post494+g76bb98c85`)
   нужна машине и остаётся в заголовке окна; строка под ним показывает то же
   число, что бейдж README, а у релиза — без нулевого PATCH.
2. **Чего делать нельзя.** Чтение обязано обходиться без git и подпроцессов: в
   пакете, поставленном через pipx, истории нет вовсе, а git-подпроцесс при
   старте окна подвисает на macOS + 3.14 на десятки секунд (#1166/#1149).
3. **Что не должно ломаться.** Файла нет (запуск из клона без сборки) или он
   битый — строки просто не будет, а окно откроется как раньше.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest

from stepik_grader import build_info, launcher

_ROOT = pathlib.Path(__file__).parent.parent
_GENERATOR = _ROOT / "scripts" / "generate_build_info.py"


def _load_generator():
    """Импортировать `scripts/generate_build_info.py` как модуль."""
    spec = importlib.util.spec_from_file_location("_generate_build_info", _GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDisplayVersion:
    """Какое число видит человек."""

    def test_dev_build_keeps_patch(self) -> None:
        """У промежуточной сборки PATCH и есть содержательная часть."""
        assert build_info.display_version({"version": "1.10.234", "released": False}) == "1.10.234"

    def test_release_drops_the_zero_patch(self) -> None:
        """PATCH в теге всегда ноль — константа, которая ничего не сообщает."""
        assert build_info.display_version({"version": "1.11.0", "released": True}) == "1.11"

    def test_no_data_shows_nothing(self) -> None:
        """Нет сведений — нет и строки: показывать «неизвестно» хуже, чем молчать."""
        assert build_info.display_version({}) is None

    def test_empty_version_shows_nothing(self) -> None:
        assert build_info.display_version({"version": "   ", "released": False}) is None

    def test_non_string_version_shows_nothing(self) -> None:
        """Битое значение не должно превращаться в строку «None» на экране."""
        assert build_info.display_version({"version": 111, "released": False}) is None


class TestReadingIsSafe:
    """Чтение файла сборки не роняет запуск и не ходит в git."""

    def test_missing_file_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_args: object, **_kwargs: object) -> object:
            raise FileNotFoundError("нет файла")

        monkeypatch.setattr(build_info.importlib.resources, "files", _boom)
        assert build_info.read_build_info() is None

    def test_broken_json_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        broken = tmp_path / "pkg"
        broken.mkdir()
        (broken / build_info.BUILD_INFO_NAME).write_text("{не json", encoding="utf-8")
        monkeypatch.setattr(build_info.importlib.resources, "files", lambda _pkg: broken)
        assert build_info.read_build_info() is None

    def test_json_list_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Валидный JSON, но не словарь — тот же отказ, а не падение на `.get`."""
        wrong = tmp_path / "pkg"
        wrong.mkdir()
        (wrong / build_info.BUILD_INFO_NAME).write_text("[1, 2]", encoding="utf-8")
        monkeypatch.setattr(build_info.importlib.resources, "files", lambda _pkg: wrong)
        assert build_info.read_build_info() is None

    def test_reading_spawns_no_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Ни git, ни любого другого подпроцесса — окно открывается сразу.

        Это не придирка: на macOS + Python 3.14 git-подпроцесс подвисает на
        десятки секунд (#1166/#1149), и версия «на лету» стоила бы пользователю
        неоткрывающегося окна.
        """
        source = tmp_path / "pkg"
        source.mkdir()
        (source / build_info.BUILD_INFO_NAME).write_text(
            json.dumps({"version": "1.10.234", "released": False}), encoding="utf-8"
        )
        monkeypatch.setattr(build_info.importlib.resources, "files", lambda _pkg: source)

        def _forbidden(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("чтение сведений о сборке запустило подпроцесс")

        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(subprocess, "run", _forbidden)
        monkeypatch.setattr(subprocess, "check_output", _forbidden)

        assert build_info.display_version() == "1.10.234"


class TestVersionLineInTheWindow:
    """Строка под заголовком — формат и локали (без tkinter, как `window_title`)."""

    def test_line_is_built_from_the_catalogue(self) -> None:
        ru = launcher.version_line(launcher.load_ui_messages("ru"), "1.10.234")
        en = launcher.version_line(launcher.load_ui_messages("en"), "1.10.234")
        assert ru is not None and "1.10.234" in ru
        assert en is not None and "1.10.234" in en
        assert ru != en, "строка собрана хардкодом, а не из локали"

    def test_key_exists_in_both_locales(self) -> None:
        for lang in ("ru", "en"):
            template = launcher.load_ui_messages(lang).get("launcher_version_line")
            assert template, f"нет ключа launcher_version_line в локали {lang}"
            assert "{version}" in template

    def test_no_build_info_no_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Клон без сборки: строки нет, исключения тоже."""
        monkeypatch.setattr(launcher, "display_version", lambda: None)
        assert launcher.version_line(launcher.load_ui_messages("ru")) is None

    def test_missing_catalogue_key_is_survivable(self) -> None:
        """Каталог без ключа — окно всё равно открывается, просто без строки."""
        assert launcher.version_line({}, "1.10.234") is None

    def test_title_still_carries_the_full_version(self) -> None:
        """Заголовок не тронут: сопоставление сборки с коммитом никуда не делось."""
        raw = "1.10.0.post494+g76bb98c85"
        title = launcher.window_title(launcher.load_ui_messages("ru"), raw)
        assert raw in title
        assert "dev" in title


class TestGenerator:
    """`scripts/generate_build_info.py` — то, что кладётся в колесо."""

    def test_writes_all_four_fields(self, tmp_path: pathlib.Path) -> None:
        module = _load_generator()
        target = tmp_path / "_build_info.json"
        written = module.write_build_info(
            target, {"version": "1.10.234", "pep440": "x", "commit": "abc", "released": False}
        )
        assert set(written) == {"version", "pep440", "commit", "released"}
        assert json.loads(target.read_text(encoding="utf-8")) == written

    def test_version_matches_the_badge_source(self) -> None:
        """Число в файле — то же, что показывает бейдж README на этой ревизии."""
        module = _load_generator()
        import sys

        sys.path.insert(0, str(_ROOT / "scripts"))
        import version as version_script

        assert module.build_info()["version"] == version_script.project_version()

    def test_scm_options_come_from_pyproject(self) -> None:
        """Схема версии читается из проекта, а не повторяется в скрипте.

        Иначе файл сборки объявлял бы одну схему, а колесо через минуту
        собиралось бы по другой — и два числа расходились бы молча.
        """
        module = _load_generator()
        assert module._scm_options().get("version_scheme") == "post-release"

    def test_empty_version_is_red(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Пустой вход обязан быть красным: молча записанная пустота уедет в колесо."""
        module = _load_generator()
        target = tmp_path / "_build_info.json"
        monkeypatch.setattr(
            module,
            "build_info",
            lambda: {"version": "", "pep440": "", "commit": "", "released": False},
        )

        assert module.main(["--out", str(target)]) == 1
        assert not target.exists()
        assert "не удалось вычислить" in capsys.readouterr().err


class TestWheelGate:
    """Файл обязан доехать до пользователя, иначе строки версии не будет."""

    def test_wheel_check_requires_build_info(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "_check_wheel_contents", _ROOT / "scripts" / "check_wheel_contents.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert "stepik_grader/_build_info.json" in module.REQUIRED_PATTERNS
        # Колесо без файла — список проблем непустой (прецедент #1127: пакетные
        # данные не были защищены ничем, и сломанный glob уезжал к пользователю).
        assert module.missing_patterns(["stepik_grader/__init__.py"]) != []

    def test_package_data_lists_build_info(self) -> None:
        """`package-data` и гейт обязаны говорить об одном файле."""
        text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert '"_build_info.json"' in text
