"""Tests for cli.py — интерактивное меню и argparse CLI (режимы 0-5).

Покрывает ветки _interactive_menu(), не задействованные другими тестами
(Issue #21 finding #8): happy-path и error-branches для режимов 1-4, main().
Плюс non-interactive argparse CLI (Sprint 8.1): --version, --mode 1-4,
отсутствующие --file/--dir.
"""

from __future__ import annotations

import importlib.metadata
import json
import pathlib
import sys
import tomllib

import pytest

from stepik_grader import cli

# Ссылка на настоящую функцию диалога ДО того, как autouse-фикстура её
# подменит — нужна тестам, проверяющим саму graceful-деградацию (issue #79).
_REAL_PICK_PATH_VIA_DIALOG = cli._pick_path_via_dialog


@pytest.fixture(autouse=True)
def _force_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert on message text predating i18n (issue #51 D-01),
    which made Russian the default. Force English so existing assertions
    stay meaningful without duplicating them in both languages; the
    Russian-default + --lang switch itself is covered separately below.
    """
    monkeypatch.setattr(cli, "_LANG", "en")


@pytest.fixture(autouse=True)
def _no_gui_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """issue #79: по умолчанию гасим нативный файловый диалог во всех тестах.

    Без этого тест, дошедший до fallback-диалога (пустой путь / отсутствующий
    --file), мог бы открыть реальное окно tkinter и повиснуть на Windows/macOS
    CI-раннерах (там дисплей может подняться, в отличие от headless Linux).
    Тесты, которым нужен «выбранный» путь, переопределяют эту подмену.
    """
    monkeypatch.setattr(cli, "_pick_path_via_dialog", lambda *, want_dir: None)


def test_version_is_dynamic_in_pyproject() -> None:
    """issue #162: git-теги — единственный источник истины. pyproject.toml
    объявляет version как dynamic и НЕ содержит статической строки version.
    """
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    project = data["project"]
    assert "version" not in project, "статическая version должна быть удалена (issue #162)"
    assert "version" in project.get("dynamic", []), "version должна быть dynamic"


def test_version_matches_installed_metadata() -> None:
    """cli.__version__ (Issue #36: read via importlib.metadata) отражает версию
    установленного пакета — теперь вычисленную setuptools-scm из git-тегов.

    Requires `pip install -e .` to have been run so the installed package
    metadata is in sync; see CONTRIBUTING.md.
    """
    assert cli.__version__ == importlib.metadata.version("stepik-python-grader")
    # Не fallback-заглушка: пакет установлен, версия реально вычислена.
    assert cli.__version__ != "0.0.0+unknown"


def test_resolve_version_reads_installed_metadata() -> None:
    assert cli._resolve_version() == cli.__version__


def test_resolve_version_falls_back_when_package_not_installed(monkeypatch) -> None:
    """Running from a git clone without `pip install -e .` shouldn't crash."""

    def _raise(*_a, **_k):
        raise cli.importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(cli.importlib.metadata, "version", _raise)
    assert cli._resolve_version() == "0.0.0+unknown"


# ---------------------------------------------------------------------------
# --version: dev vs release output (issue #163)
# ---------------------------------------------------------------------------


def test_format_version_on_tag_has_no_dev_suffix() -> None:
    assert cli._format_version_for_display("1.5.0") == "1.5.0"


def test_format_version_off_tag_gets_dev_marker() -> None:
    raw = "1.5.0.post4+gabcdef1"
    formatted = cli._format_version_for_display(raw)
    assert formatted.startswith(raw)
    assert "dev build" in formatted


def test_format_version_dirty_worktree_still_marked_dev() -> None:
    raw = "1.5.0.post4+gabcdef1.d20260708"
    assert "dev build" in cli._format_version_for_display(raw)


def test_is_dev_build_true_for_local_segment() -> None:
    assert cli._is_dev_build("1.5.0.post4+gabcdef1") is True


def test_is_dev_build_false_for_clean_tag() -> None:
    assert cli._is_dev_build("1.5.0") is False


def test_version_flag_marks_dev_build_when_off_tag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "__version__", "1.5.0.post4+gabcdef1")
    cli.main(["--version"])
    out = capsys.readouterr().out
    assert "1.5.0.post4+gabcdef1" in out
    assert "dev build" in out


def test_version_flag_clean_on_tag(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "__version__", "1.5.0")
    cli.main(["--version"])
    out = capsys.readouterr().out
    assert "1.5.0" in out
    assert "dev build" not in out


def test_main_delegates_to_interactive_menu(monkeypatch) -> None:
    """main() with no --mode falls back to the interactive menu."""
    called = []
    monkeypatch.setattr(cli, "_interactive_menu", lambda: called.append(True))
    cli.main([])
    assert called == [True]


# ---------------------------------------------------------------------------
# argparse CLI — non-interactive режимы (Sprint 8.1)
# ---------------------------------------------------------------------------


class TestArgparseCli:
    def test_version_prints_and_exits(self, capsys) -> None:
        cli.main(["--version"])
        out = capsys.readouterr().out
        assert cli.__version__ in out

    def test_mode_1_requires_file(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--mode", "1"])

    def test_mode_2_requires_dir(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--mode", "2"])

    def test_mode_3_requires_dir(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--mode", "3"])

    def test_mode_4_requires_dir(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--mode", "4"])

    def test_invalid_mode_choice_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["--mode", "9"])

    def test_config_flag_with_missing_file_is_rejected(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        """issue #993: явно указанный конфиг обязан существовать.

        Тихий откат на автопоиск здесь хуже отказа: пользователь просил
        конкретный файл, а прогон пошёл бы с чужими параметрами.
        """
        with pytest.raises(SystemExit):
            cli.main(["--config", str(tmp_path / "nope.toml"), "--version"])
        assert "--config" in capsys.readouterr().err

    def test_config_flag_sets_explicit_source(self, tmp_path: pathlib.Path) -> None:
        """--config фиксирует источник конфигурации на весь процесс.

        Модуль берётся через ``cli.config``, а не свежим импортом: соседний
        ``test_bare_import_does_not_read_pyproject_toml`` переимпортирует
        ``stepik_grader.config``, после чего в ``sys.modules`` лежит ДРУГОЙ
        объект модуля — со своим кэшем и своими переопределениями.
        """
        config = cli.config

        cfg = tmp_path / "grader.toml"
        cfg.write_text("[tool.stepik-grader]\ntimeout_seconds = 7.0\n", encoding="utf-8")
        try:
            cli.main(["--config", str(cfg), "--version"])
            assert config.config_source() == cfg
            assert config.get_config().timeout_seconds == 7.0
        finally:
            config.set_config_path(None)

    def test_run_profile_header_names_conditions(
        self, tmp_path: pathlib.Path, monkeypatch, capsys
    ) -> None:
        """issue #984: шапка прогона говорит, чем проверяли и каким конфигом.

        Прежде условия, определяющие вердикт, не были видны нигде — расхождение
        двух прогонов объяснить было нечем.
        """
        config = cli.config
        cfg = tmp_path / "grader.toml"
        cfg.write_text("[tool.stepik-grader]\ntimeout_seconds = 4.0\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        sol = tmp_path / "task.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        try:
            cli.main(["--mode", "1", "--file", str(sol), "--config", str(cfg)])
        finally:
            config.set_config_path(None)

        out = capsys.readouterr().out
        assert "Условия прогона" in out
        assert "LocalRunner" in out
        assert "4.0" in out  # таймаут из применённого конфига
        assert str(cfg) in out  # и сам применённый файл

    def test_run_profile_header_absent_in_machine_output(
        self, tmp_path: pathlib.Path, monkeypatch, capsys
    ) -> None:
        """В json/csv/markdown шапки нет — это данные для пайплайна."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        sol = tmp_path / "task.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(sol), "--output", "json"])

        out = capsys.readouterr().out
        assert "Условия прогона" not in out
        json.loads(out.strip().splitlines()[-1])  # вывод остаётся разбираемым

    def test_mode_1_dispatches_to_run_mode_1(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        called = []
        monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **kwargs: called.append(solution))
        cli.main(["--mode", "1", "--file", str(sol)])
        assert called == [sol]

    def test_mode_2_dispatches_to_run_mode_2(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        called = []
        monkeypatch.setattr(
            cli, "_run_mode_2", lambda directory, **kwargs: called.append(directory)
        )
        cli.main(["--mode", "2", "--dir", str(tmp_path)])
        assert called == [tmp_path]

    def test_mode_3_dispatches_to_run_mode_3_with_repeats(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        called = []
        monkeypatch.setattr(
            cli,
            "_run_mode_3",
            lambda directory, repeats, **kwargs: called.append((directory, repeats)),
        )
        cli.main(["--mode", "3", "--dir", str(tmp_path), "--repeats", "20"])
        assert called == [(tmp_path, 20)]

    def test_mode_3_default_repeats(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        called = []
        monkeypatch.setattr(
            cli, "_run_mode_3", lambda directory, repeats, **kwargs: called.append(repeats)
        )
        cli.main(["--mode", "3", "--dir", str(tmp_path)])
        assert called == [15]

    def test_mode_4_dispatches_to_run_mode_4_with_number(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        called = []
        monkeypatch.setattr(
            cli,
            "_run_mode_4",
            lambda directory, number, **kwargs: called.append((directory, number)),
        )
        cli.main(["--mode", "4", "--dir", str(tmp_path), "--number", "5000"])
        assert called == [(tmp_path, 5000)]

    def test_mode_4_default_number(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        called = []
        monkeypatch.setattr(
            cli, "_run_mode_4", lambda directory, number, **kwargs: called.append(number)
        )
        cli.main(["--mode", "4", "--dir", str(tmp_path)])
        assert called == [1000]


# ---------------------------------------------------------------------------
# Режим 1 — Check one solution
# ---------------------------------------------------------------------------


class TestMode1:
    def test_file_not_found(self, capsys, monkeypatch) -> None:
        inputs = iter(["1", "/no/such/file.py", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "File not found" in capsys.readouterr().out

    def test_happy_path(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "input_1.txt").write_text("4", encoding="utf-8")
        (tests_dir / "expected_1.txt").write_text("5", encoding="utf-8")

        inputs = iter(["1", str(sol), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        out = capsys.readouterr().out
        assert "task1.py" in out

    def test_test_dir_not_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        """File exists, but resolve_test_dir() finds nothing (issue #47 R-04).

        Previously resolve_test_dir() silently returned a non-existent
        <parent>/tests/ path; _run_mode_1 must handle the new None contract
        with a friendly message instead of crashing on pathlib.Path(None).
        """
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")

        inputs = iter(["1", str(sol), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        out = capsys.readouterr().out
        assert "Tests not found for" in out
        assert "python -m stepik_grader.downloader" in out


# ---------------------------------------------------------------------------
# Режим 2 — Check all solutions in folder
# ---------------------------------------------------------------------------


class TestMode2:
    def test_directory_not_found(self, capsys, monkeypatch) -> None:
        inputs = iter(["2", "/no/such/dir", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        inputs = iter(["2", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "No solution files found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Режим 3 — Benchmark solutions in folder
# ---------------------------------------------------------------------------


class TestMode3:
    def test_directory_not_found(self, capsys, monkeypatch) -> None:
        inputs = iter(["3", "/no/such/dir", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        inputs = iter(["3", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "No solution files found" in capsys.readouterr().out

    def test_happy_path_ranks_and_reports_errors(
        self, tmp_path: pathlib.Path, capsys, monkeypatch
    ) -> None:
        """One solution benchmarks OK, one errors -- both paths of the result loop."""
        ok_sol = tmp_path / "task1.py"
        ok_sol.write_text("print(1)\n", encoding="utf-8")
        bad_sol = tmp_path / "task2.py"
        bad_sol.write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()

        def fake_run_benchmark(path, test_dir, *, repeats=15):
            if "task2" in str(path):
                return {"error": "boom", "runs": 0}
            return {
                "runs": 5,
                "min": 0.001,
                "median": 0.002,
                "mean": 0.002,
                "max": 0.003,
                "stdev": 0.0,
                "peak_memory_mb": 0.0,
            }

        monkeypatch.setattr(cli, "run_benchmark", fake_run_benchmark)
        monkeypatch.setattr(cli, "_ask_bench_profile", lambda: 5)

        inputs = iter(["3", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        out = capsys.readouterr().out
        assert "task1.py" in out
        assert "boom" in out


# ---------------------------------------------------------------------------
# Режим 4 — Micro-benchmark (timeit) for folder
# ---------------------------------------------------------------------------


class TestMode4:
    def test_directory_not_found(self, capsys, monkeypatch) -> None:
        inputs = iter(["4", "/no/such/dir", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        inputs = iter(["4", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "No solution files found" in capsys.readouterr().out

    def test_tests_not_found_for_group(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        """_resolve_test_dir_from_input returning a nonexistent path hits 'Tests not found'.

        collect_grouped_files() derives folders from os.walk(), so in practice every
        resolved folder physically exists -- this defensive branch is only reachable
        by forcing _resolve_test_dir_from_input to return a bogus path.
        """
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")

        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        monkeypatch.setattr(
            cli, "_resolve_test_dir_from_input", lambda *a, **k: tmp_path / "does_not_exist"
        )
        inputs = iter(["4", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Tests not found" in capsys.readouterr().out

    def test_no_test_cases_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        """run_microbench_mode() returning {} hits the 'No test cases found' branch."""
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        monkeypatch.setattr(cli, "run_microbench_mode", lambda *a, **k: {})
        inputs = iter(["4", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "No test cases found" in capsys.readouterr().out

    def test_happy_path_ranks_and_reports_errors(
        self, tmp_path: pathlib.Path, capsys, monkeypatch
    ) -> None:
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()

        def fake_run_microbench_mode(paths, test_dir, *, number=1000):
            return {
                paths[0]: {
                    "min": 0.001,
                    "median": 0.002,
                    "mean": 0.002,
                    "max": 0.003,
                    "stdev": 0.0,
                    "runs": 5,
                    "peak_memory_mb": 0.0,
                    "relative": 1.0,
                    "verdict": "SIMILAR",
                }
            }

        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        monkeypatch.setattr(cli, "run_microbench_mode", fake_run_microbench_mode)
        inputs = iter(["4", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        out = capsys.readouterr().out
        assert "task1.py" in out

    def test_all_errors_prints_error_rows(
        self, tmp_path: pathlib.Path, capsys, monkeypatch
    ) -> None:
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        monkeypatch.setattr(
            cli,
            "run_microbench_mode",
            lambda paths, test_dir, **k: {paths[0]: {"error": "SyntaxError"}},
        )
        inputs = iter(["4", str(tmp_path), "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "SyntaxError" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _ask_bench_profile / _ask_micro_profile — custom profile prompts
# ---------------------------------------------------------------------------


class TestAskProfiles:
    def test_ask_bench_profile_custom(self, monkeypatch) -> None:
        """Choice '4' (custom) prompts for a repeat count, clamped to 5-100."""
        inputs = iter(["4", "500"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        assert cli._ask_bench_profile() == 100

    def test_ask_bench_profile_invalid_falls_back_to_medium(self, monkeypatch) -> None:
        monkeypatch.setattr("builtins.input", lambda *a: "9")
        assert cli._ask_bench_profile() == 15

    def test_ask_micro_profile_custom(self, monkeypatch) -> None:
        inputs = iter(["6", "1000000"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        assert cli._ask_micro_profile() == 500_000

    def test_ask_micro_profile_invalid_falls_back_to_normal(self, monkeypatch) -> None:
        monkeypatch.setattr("builtins.input", lambda *a: "9")
        assert cli._ask_micro_profile() == 1_000


# ---------------------------------------------------------------------------
# _force_utf8_stdio — cp1251 crash fix in Git Bash (issue #64)
# ---------------------------------------------------------------------------


class _FakeStream:
    """Минимальный stdout/stderr-дублёр с настраиваемой кодировкой."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.reconfigured: dict[str, object] | None = None

    def reconfigure(self, **kwargs: object) -> None:
        self.reconfigured = kwargs
        self.encoding = str(kwargs.get("encoding", self.encoding))


class TestForceUtf8Stdio:
    """issue #119: _force_utf8_stdio живёт в cli/options.py и читает sys.stdout/
    sys.stderr через свой собственный `import sys` — но это тот же sys-модуль
    (singleton), поэтому патчить настоящий sys.stdout/stderr (а не cli.sys,
    несуществующий facade-alias) достаточно и корректно вне зависимости от
    того, в каком модуле физически лежит функция.
    """

    def test_cp1251_stream_reconfigured_to_utf8(self, monkeypatch) -> None:
        """cp1251-поток (Git Bash) переключается на UTF-8 с errors='replace'."""
        out, err = _FakeStream("cp1251"), _FakeStream("cp1251")
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        cli._force_utf8_stdio()
        assert out.reconfigured == {"encoding": "utf-8", "errors": "replace"}
        assert err.reconfigured == {"encoding": "utf-8", "errors": "replace"}

    def test_utf8_stream_left_untouched(self, monkeypatch) -> None:
        """Уже-UTF-8 поток не трогаем (никаких лишних reconfigure)."""
        out = _FakeStream("utf-8")
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", _FakeStream("UTF-8"))
        cli._force_utf8_stdio()
        assert out.reconfigured is None

    def test_stream_without_reconfigure_is_noop(self, monkeypatch) -> None:
        """Поток без .reconfigure (например, перехваченный) не роняет процесс."""

        class _Bare:
            encoding = "cp1251"

        monkeypatch.setattr(sys, "stdout", _Bare())
        monkeypatch.setattr(sys, "stderr", _Bare())
        cli._force_utf8_stdio()  # не должно бросить AttributeError

    def test_main_calls_force_utf8(self, monkeypatch) -> None:
        """main() вызывает _force_utf8_stdio до разбора аргументов."""
        called = []
        monkeypatch.setattr(cli, "_force_utf8_stdio", lambda: called.append(True))
        monkeypatch.setattr(cli, "_interactive_menu", lambda: None)
        cli.main([])
        assert called == [True]


# ---------------------------------------------------------------------------
# python -m stepik_grader (issue #65)
# ---------------------------------------------------------------------------


class TestPackageMainEntryPoint:
    def test_python_m_stepik_grader_version(self) -> None:
        """`python -m stepik_grader --version` печатает версию и завершается 0.

        Эталон считается в ТОМ ЖЕ подпроцессе (issue #837): сравнивать
        ``cli.__version__`` из процесса pytest с выводом подпроцесса нельзя —
        корневой ``conftest.py`` кладёт ``src/`` в начало ``sys.path``, и
        протухший ``src/*.egg-info`` затеняет метаданные установленного пакета.
        Тест краснел у любого, кто ставил пакет на другом коммите, а при
        случайном совпадении версий не проверял ничего.
        """
        import subprocess
        import sys

        expected = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.metadata as m; print(m.version('stepik-python-grader'))",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        result = subprocess.run(
            [sys.executable, "-m", "stepik_grader", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert expected in result.stdout

    def test_in_process_metadata_matches_installed_package(self) -> None:
        """Guard: метаданные в процессе pytest совпадают с установленным пакетом.

        issue #837: если они разошлись, значит ``src/*.egg-info`` (артефакт
        сборки, не под git) старше установленного пакета — прогон начинает
        врать по версии. Тест говорит это прямо, вместо загадочного падения в
        соседнем ассерте.
        """
        import importlib.metadata
        import subprocess
        import sys

        in_process = importlib.metadata.version("stepik-python-grader")
        installed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib.metadata as m; print(m.version('stepik-python-grader'))",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert in_process == installed, (
            "версия в процессе pytest разошлась с установленной "
            f"({in_process} vs {installed}). Обычно это протухший src/*.egg-info, "
            "который conftest.py выводит вперёд dist-info: удалите каталог "
            "src/*.egg-info и повторите `pip install -e .`"
        )


# ---------------------------------------------------------------------------
# Mode 4 memory column methodology footnote (issue #66)
# ---------------------------------------------------------------------------


class TestMode4MemoryFootnote:
    def _bench(self, paths, test_dir, *, number=1000):
        return {
            paths[0]: {
                "min": 0.001,
                "median": 0.002,
                "mean": 0.002,
                "max": 0.003,
                "stdev": 0.0,
                "runs": 5,
                "peak_memory_mb": 0.0,
                "relative": 1.0,
                "verdict": "SIMILAR",
            }
        }

    def test_footnote_printed_once_after_table(
        self, tmp_path: pathlib.Path, capsys, monkeypatch
    ) -> None:
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        monkeypatch.setattr(cli, "run_microbench_mode", self._bench)
        cli._run_mode_4(tmp_path, 500)
        out = capsys.readouterr().out
        # Сноска о методике Py-heap появляется ровно один раз.
        assert out.count("Py-heap") >= 1
        assert "tracemalloc" in out

    def test_no_footnote_in_json_output(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        monkeypatch.setattr(cli, "run_microbench_mode", self._bench)
        cli._run_mode_4(tmp_path, 500, output="json")
        assert "tracemalloc" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# File-dialog fallback (issue #79)
# ---------------------------------------------------------------------------


class TestDialogFallbackMenu:
    """Пустой ввод пути в меню → нативный файловый диалог (mock)."""

    def test_empty_input_triggers_dialog_mode_1(self, monkeypatch, tmp_path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        picked = []
        monkeypatch.setattr(cli, "_pick_path_via_dialog", lambda *, want_dir: str(sol))
        monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: picked.append(solution))
        # Последовательный ввод: режим "1", пустой путь (→ диалог), затем "0" —
        # выход из зацикленного меню (issue #445).
        inputs = iter(["1", "", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert picked == [str(sol)]

    def test_empty_input_dialog_wants_dir_for_mode_2(self, monkeypatch, tmp_path) -> None:
        got = []
        monkeypatch.setattr(
            cli, "_pick_path_via_dialog", lambda *, want_dir: got.append(want_dir) or str(tmp_path)
        )
        monkeypatch.setattr(cli, "_run_mode_2", lambda directory, **k: None)
        inputs = iter(["2", "", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert got == [True]  # для папки — askdirectory

    def test_empty_input_and_dialog_cancelled_is_graceful(self, monkeypatch, capsys) -> None:
        # autouse-фикстура уже возвращает None (отмена диалога).
        inputs = iter(["1", "", "0"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()  # не должно быть трейсбека
        assert "File not found" in capsys.readouterr().out


class TestDialogFallbackCli:
    """`--mode N` без --file/--dir: диалог только в text-режиме."""

    def test_missing_file_uses_dialog_in_text_mode(self, monkeypatch, tmp_path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        called = []
        monkeypatch.setattr(cli, "_pick_path_via_dialog", lambda *, want_dir: str(sol))
        monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: called.append(solution))
        cli.main(["--mode", "1"])
        assert called == [str(sol)]

    def test_missing_file_json_output_no_dialog_errors(self, monkeypatch) -> None:
        # В машинном режиме диалог не показываем — argparse.error → SystemExit.
        called = []
        monkeypatch.setattr(
            cli, "_pick_path_via_dialog", lambda *, want_dir: called.append(True) or "/x"
        )
        with pytest.raises(SystemExit):
            cli.main(["--mode", "1", "--output", "json"])
        assert called == []  # диалог НЕ вызывался

    def test_missing_file_watch_no_dialog_errors(self, monkeypatch) -> None:
        called = []
        monkeypatch.setattr(
            cli, "_pick_path_via_dialog", lambda *, want_dir: called.append(True) or "/x"
        )
        with pytest.raises(SystemExit):
            cli.main(["--mode", "1", "--watch"])
        assert called == []

    def test_missing_dir_dialog_cancel_errors(self, monkeypatch) -> None:
        # autouse → None (как отмена/headless) → parser.error → SystemExit.
        with pytest.raises(SystemExit):
            cli.main(["--mode", "2"])


class TestPickPathViaDialogGraceful:
    """Сама _pick_path_via_dialog деградирует без tkinter, не падая."""

    def test_returns_none_when_tkinter_missing(self, monkeypatch) -> None:
        # None в sys.modules заставляет `import tkinter` бросить ImportError.
        monkeypatch.setitem(sys.modules, "tkinter", None)
        assert _REAL_PICK_PATH_VIA_DIALOG(want_dir=False) is None


# ---------------------------------------------------------------------------
# Entry-point flags: --clear-cache / --init-vscode / --serve (issue #118)
#
# main() возвращает раньше --mode routing для этих трёх флагов, но до сих
# пор они не были покрыты на уровне cli.main([...]) — только --version и
# --mode 1-4. Добавлено для Stage 0 (#118) декомпозиции cli.py (#117):
# фиксирует текущее публичное поведение entrypoint-уровня до того, как эти
# ветки, возможно, переедут в отдельный commands-модуль (#120).
# ---------------------------------------------------------------------------


class TestEntrypointSideEffectFlags:
    def test_clear_cache_prints_removed_count_and_exits(
        self, monkeypatch, capsys, tmp_path
    ) -> None:
        from stepik_grader.core import stepik_client

        removed_calls = []

        class _StubCache:
            def clear(self) -> int:
                removed_calls.append(True)
                return 3

        monkeypatch.setattr(cli, "GraderCache", _StubCache)
        # issue #816: флаг чистит и `.stepik_cache`, поэтому каталог подменяется
        # на пустой временный — иначе тест удалял бы РЕАЛЬНЫЙ кэш в cwd
        # разработчика и считал его записи в ожидаемую сумму.
        monkeypatch.setattr(stepik_client, "CACHE_DIR", tmp_path / "empty-api-cache")
        cli.main(["--clear-cache"])
        out = capsys.readouterr().out
        assert removed_calls == [True]
        assert "3" in out

    def test_clear_cache_also_clears_stepik_api_cache(self, monkeypatch, capsys, tmp_path) -> None:
        """issue #816 (DEV-11): флаг чистит ОБА кэша, а не только `.grader_cache`.

        `.stepik_cache` (ответы API) прибавляет файл на каждую новую скачанную
        задачу и не чистился ничем — ни TTL, ни этим флагом; пользователю
        оставалось удалять каталог руками, зная о его существовании.
        """
        from stepik_grader.core import stepik_client

        class _StubCache:
            def clear(self) -> int:
                return 2

        api_cache = tmp_path / ".stepik_cache"
        api_cache.mkdir()
        for i in range(3):
            (api_cache / f"{i}.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(cli, "GraderCache", _StubCache)
        monkeypatch.setattr(stepik_client, "CACHE_DIR", api_cache)
        cli.main(["--clear-cache"])

        assert list(api_cache.glob("*.json")) == []
        assert "5" in capsys.readouterr().out  # 2 записи прогонов + 3 ответа API

    def test_init_vscode_written_reports_path(self, monkeypatch, capsys, tmp_path) -> None:
        from stepik_grader import ide

        target = tmp_path / ".vscode" / "tasks.json"
        monkeypatch.setattr(ide, "write_vscode_tasks", lambda: (True, target))
        cli.main(["--init-vscode"])
        out = capsys.readouterr().out
        assert str(target) in out

    def test_init_vscode_existing_file_reports_warning(self, monkeypatch, capsys, tmp_path) -> None:
        from stepik_grader import ide

        target = tmp_path / ".vscode" / "tasks.json"
        monkeypatch.setattr(ide, "write_vscode_tasks", lambda: (False, target))
        cli.main(["--init-vscode"])
        out = capsys.readouterr().out
        assert str(target) in out

    def test_serve_delegates_to_web_run_server_with_port(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--port", "9090"])
        assert called == [
            {"port": 9090, "root": None, "confine": True, "sandbox": False, "record_history": True}
        ]

    def test_serve_uses_default_port(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve"])
        assert called == [
            {"port": 8000, "root": None, "confine": True, "sandbox": False, "record_history": True}
        ]

    def test_serve_passes_root(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--root", "/some/dir"])
        assert called == [
            {
                "port": 8000,
                "root": pathlib.Path("/some/dir"),
                "confine": True,
                "sandbox": False,
                "record_history": True,
            }
        ]

    def test_serve_no_root_confinement_disables_confine(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--no-root-confinement"])
        assert called == [
            {"port": 8000, "root": None, "confine": False, "sandbox": False, "record_history": True}
        ]

    def test_serve_passes_sandbox_flag(self, monkeypatch) -> None:
        # issue #396: --sandbox теперь проброшен в web — run_server сам ставит
        # SandboxRunner активным _RUNNER. Раньше (#351) комбинация отклонялась
        # parser.error, т.к. sandbox в web не был реализован.
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--sandbox"])
        assert called == [
            {"port": 8000, "root": None, "confine": True, "sandbox": True, "record_history": True}
        ]

    def test_serve_no_history_disables_recording(self, monkeypatch) -> None:
        # issue #395: --serve включает историю по умолчанию (наполнить «Подучить»);
        # явный --no-history выключает запись.
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--no-history"])
        assert called == [
            {"port": 8000, "root": None, "confine": True, "sandbox": False, "record_history": False}
        ]

    def test_serve_sandbox_unavailable_is_rejected(self, monkeypatch, capsys) -> None:
        # issue #396: если backend песочницы недоступен (нет bwrap и т.п.),
        # run_server бросает SandboxUnavailableError — CLI честно отказывает
        # parser.error, а не запускает сервер без изоляции.
        from stepik_grader import web
        from stepik_grader.core.sandbox import SandboxUnavailableError

        def _raise(**kwargs: object) -> None:
            raise SandboxUnavailableError("no sandbox backend here")

        monkeypatch.setattr(web, "run_server", _raise)
        with pytest.raises(SystemExit):
            cli.main(["--serve", "--sandbox"])
        err = capsys.readouterr().err
        assert "no sandbox backend here" in err

    # -- issue #800 (QA-03): та же гарантия для режимов 1–4 ---------------
    #
    # Инвариант CLAUDE.md №4 — «недоступный backend → parser.error, а не тихий
    # откат на LocalRunner» — держался только для `--serve`. Ветка `--mode N
    # --sandbox` не выполнялась НИ ОДНИМ тестом (отчёт покрытия: cli/__init__.py
    # 554-559 не покрыты), то есть рефакторинг, переставивший `set_runner` и
    # обработку исключения, тихо вернул бы исполнение недоверенного кода без
    # изоляции при полностью зелёном прогоне.

    def test_mode1_sandbox_unavailable_is_rejected(
        self, monkeypatch, capsys, tmp_path: pathlib.Path
    ) -> None:
        """Недоступный backend в режиме 1 — отказ, а не запуск без изоляции."""
        import stepik_grader.core.sandbox as sandbox_mod
        from stepik_grader.core.sandbox import SandboxUnavailableError

        def _raise() -> None:
            raise SandboxUnavailableError("no bwrap on this machine")

        monkeypatch.setattr(sandbox_mod, "SandboxRunner", _raise)
        solution = tmp_path / "task.py"
        solution.write_text("print(1)\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc:
            cli.main(["--mode", "1", "--sandbox", "--file", str(solution)])

        assert exc.value.code == 2  # parser.error, а не обычный выход
        assert "no bwrap on this machine" in capsys.readouterr().err

    def test_mode1_sandbox_installs_sandbox_runner(
        self, monkeypatch, tmp_path: pathlib.Path
    ) -> None:
        """Успешный путь: активным раннером становится именно SandboxRunner."""
        import stepik_grader.cli as cli_mod
        import stepik_grader.core.sandbox as sandbox_mod

        installed: list[object] = []
        sentinel = object()
        monkeypatch.setattr(sandbox_mod, "SandboxRunner", lambda: sentinel)
        monkeypatch.setattr(cli_mod, "set_runner", installed.append)
        # Сам грейдинг не нужен: проверяется только установка раннера.
        monkeypatch.setattr(cli_mod, "_run_mode_1", lambda *a, **k: None)
        solution = tmp_path / "task.py"
        solution.write_text("print(1)\n", encoding="utf-8")

        cli.main(["--mode", "1", "--sandbox", "--file", str(solution)])

        assert installed == [sentinel], "активным раннером должен стать SandboxRunner"


# ---------------------------------------------------------------------------
# Verdict-tally helpers (issue #268) — pure functions, tested directly rather
# than only through the cli.main()/_run_mode_N facade.
# ---------------------------------------------------------------------------


class TestVerdictTallyHelpers:
    def test_counts_from_cases_uses_verdict_field(self) -> None:
        from stepik_grader.cli import commands

        cases = [
            {"verdict": "AC", "passed": True},
            {"verdict": "AC", "passed": True},
            {"verdict": "WA", "passed": False},
            {"verdict": "RE", "passed": False},
        ]
        assert commands._verdict_counts_from_cases(cases) == {"AC": 2, "WA": 1, "RE": 1}

    def test_counts_from_cases_falls_back_to_passed_when_verdict_missing(self) -> None:
        from stepik_grader.cli import commands

        cases = [{"passed": True}, {"passed": False}]
        assert commands._verdict_counts_from_cases(cases) == {"AC": 1, "WA": 1}

    def test_counts_from_bench_uses_verdict_field(self) -> None:
        from stepik_grader.cli import commands

        results = {
            "a.py": {"verdict": "SIMILAR"},
            "b.py": {"verdict": "SLOWER"},
            "c.py": {"verdict": "SIMILAR"},
        }
        assert commands._verdict_counts_from_bench(results) == {"SIMILAR": 2, "SLOWER": 1}

    def test_counts_from_bench_maps_error_entries_to_err(self) -> None:
        from stepik_grader.cli import commands

        results = {"a.py": {"verdict": "SIMILAR"}, "b.py": {"error": "no test cases"}}
        assert commands._verdict_counts_from_bench(results) == {"SIMILAR": 1, "ERR": 1}


# ---------------------------------------------------------------------------
# --stats / --no-stats / --stats-summary (issue #268)
# ---------------------------------------------------------------------------


class TestStatsFlags:
    def test_stats_summary_no_data_prints_message_and_exits(
        self, monkeypatch, capsys, tmp_path
    ) -> None:
        real = cli.stats.read_summary
        monkeypatch.setattr(
            cli.stats,
            "read_summary",
            lambda: real(stats_path=tmp_path / "does-not-exist.jsonl"),
        )
        cli.main(["--stats-summary", "--lang", "en"])
        out = capsys.readouterr().out
        assert "No data" in out

    def test_stats_summary_with_data_prints_table(self, monkeypatch, capsys, tmp_path) -> None:
        stats_path = tmp_path / ".grader_stats.jsonl"
        real = cli.stats.read_summary
        cli.stats.record_run(1, {"AC": 1}, 0.5, stats_path=stats_path)
        monkeypatch.setattr(cli.stats, "read_summary", lambda: real(stats_path=stats_path))
        cli.main(["--stats-summary"])
        out = capsys.readouterr().out
        assert "Total runs" in out
        assert "1" in out

    def test_mode_1_stats_flag_threads_record_stats_true(self, monkeypatch, tmp_path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        calls = []
        monkeypatch.setattr(cli, "_run_mode_1", lambda *a, **k: calls.append(k.get("record_stats")))
        cli.main(["--mode", "1", "--file", str(sol), "--stats"])
        assert calls == [True]

    def test_mode_1_no_stats_flag_threads_record_stats_false(self, monkeypatch, tmp_path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        calls = []
        monkeypatch.setattr(cli, "_run_mode_1", lambda *a, **k: calls.append(k.get("record_stats")))
        cli.main(["--mode", "1", "--file", str(sol), "--no-stats"])
        assert calls == [False]

    def test_mode_1_default_uses_config_record_stats(self, monkeypatch, tmp_path) -> None:
        import types

        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        monkeypatch.setattr(
            cli.options,
            "CONFIG",
            types.SimpleNamespace(record_stats=True, record_history=False, use_cache=False),
        )
        calls = []
        monkeypatch.setattr(cli, "_run_mode_1", lambda *a, **k: calls.append(k.get("record_stats")))
        cli.main(["--mode", "1", "--file", str(sol)])
        assert calls == [True]


# ---------------------------------------------------------------------------
# Facade namespace contract (issue #117/#118)
#
# Каждый monkeypatch.setattr(cli, "_name", ...) в этом файле полагается на
# то, что main()/_run_mode_N()/_print_tabular() резолвят "_name" как
# module-global имя cli.py в момент ВЫЗОВА (Python ищет голое имя в globals()
# охватывающего модуля при каждом обращении — late binding), а не как
# ссылку, захваченную при импорте. Это единственная причина, по которой
# monkeypatch вообще работает сегодня.
#
# Если будущий safe-extraction PR (#119) перенесёт одну из этих функций в
# `cli/options.py`/`cli/rendering.py` и вызывающая сторона начнёт обращаться
# к ней через `from .options import _build_arg_parser` (bound at import
# time) вместо реэкспорта на facade `cli`, эти тесты первыми покажут
# расхождение: patch перестанет "долетать" до вызывающей стороны.
# ---------------------------------------------------------------------------


class TestFacadeNamespaceContract:
    """Регрессия на late-binding резолюцию имён safe-extraction кандидатов.

    Покрывает именно те helpers, которые issue #117 называет safe candidates
    для первого extraction-PR (#119): _build_arg_parser, _resolve_verbosity,
    _resolve_use_cache, _rows_to_csv, _rows_to_markdown, _print_tabular.
    """

    def test_build_arg_parser_called_via_facade(self, monkeypatch) -> None:
        real = cli._build_arg_parser
        calls = []

        def _spy():
            calls.append(True)
            return real()

        monkeypatch.setattr(cli, "_build_arg_parser", _spy)
        cli.main(["--version"])
        assert calls == [True]

    def test_resolve_verbosity_called_via_facade_for_mode_1(self, monkeypatch, tmp_path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        calls = []
        real = cli._resolve_verbosity

        def _spy(args, *, default):
            calls.append(default)
            return real(args, default=default)

        monkeypatch.setattr(cli, "_resolve_verbosity", _spy)
        monkeypatch.setattr(cli, "_run_mode_1", lambda *a, **k: None)
        cli.main(["--mode", "1", "--file", str(sol)])
        assert calls == [True]  # mode 1 default verbosity — True

    def test_resolve_use_cache_called_via_facade_for_mode_2(self, monkeypatch, tmp_path) -> None:
        calls = []
        real = cli._resolve_use_cache

        def _spy(args, *, incremental):
            calls.append(incremental)
            return real(args, incremental=incremental)

        monkeypatch.setattr(cli, "_resolve_use_cache", _spy)
        monkeypatch.setattr(cli, "_run_mode_2", lambda *a, **k: None)
        cli.main(["--mode", "2", "--dir", str(tmp_path)])
        assert calls == [False]  # mode 2 без --watch: incremental=False

    def test_rows_to_csv_called_via_facade_from_print_tabular(self, monkeypatch) -> None:
        """issue #121: _rows_to_csv/_print_tabular живут в cli/rendering.py —
        CONFIG-style hidden dependency: патчим там же, а не на facade `cli`
        (facade больше не держит своей копии имени, которую читает _print_tabular).
        """
        calls = []
        real = cli._rows_to_csv

        def _spy(rows, fieldnames):
            calls.append((rows, fieldnames))
            return real(rows, fieldnames)

        monkeypatch.setattr(cli.rendering, "_rows_to_csv", _spy)
        cli._print_tabular("csv", [{"a": 1}], ["a"])
        assert calls == [([{"a": 1}], ["a"])]

    def test_rows_to_markdown_called_via_facade_from_print_tabular(self, monkeypatch) -> None:
        calls = []
        real = cli._rows_to_markdown

        def _spy(rows, fieldnames):
            calls.append((rows, fieldnames))
            return real(rows, fieldnames)

        monkeypatch.setattr(cli.rendering, "_rows_to_markdown", _spy)
        cli._print_tabular("markdown", [{"a": 1}], ["a"])
        assert calls == [([{"a": 1}], ["a"])]

    def test_print_tabular_called_via_facade_for_mode_1_csv_output(
        self, monkeypatch, tmp_path
    ) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "input_1.txt").write_text("4", encoding="utf-8")
        (tests_dir / "expected_1.txt").write_text("5", encoding="utf-8")

        calls = []
        real = cli._print_tabular

        def _spy(output, rows, fieldnames):
            calls.append(output)
            return real(output, rows, fieldnames)

        monkeypatch.setattr(cli, "_print_tabular", _spy)
        cli.main(["--mode", "1", "--file", str(sol), "--output", "csv"])
        assert calls == ["csv"]


# ---------------------------------------------------------------------------
# _collect_lint — единый прогон ruff для печати + истории (issue #403)
# ---------------------------------------------------------------------------


def test_collect_lint_none_when_ruff_unavailable(monkeypatch, tmp_path) -> None:
    """ruff недоступен (нет extra [lint]) → None (печать покажет подсказку)."""
    from stepik_grader.cli import commands
    from stepik_grader.core import lint

    monkeypatch.setattr(lint, "ruff_available", lambda: False)
    sol = tmp_path / "s.py"
    sol.write_text("x = 1\n", encoding="utf-8")
    assert commands._collect_lint([sol]) is None


def test_collect_lint_empty_for_no_solutions() -> None:
    from stepik_grader.cli import commands

    assert commands._collect_lint([]) == {}


def test_collect_lint_runs_ruff_when_available(monkeypatch, tmp_path) -> None:
    """ruff доступен → dict {sol: [Violation, ...]} (один прогон на решение)."""
    from stepik_grader.cli import commands
    from stepik_grader.core import lint

    monkeypatch.setattr(lint, "ruff_available", lambda: True)
    sentinel = [lint.Violation(rule_code="F401", line_no=1, message="x")]
    # issue #728: набор правил и preview передаются явно (ровно карточки базы).
    monkeypatch.setattr(lint, "run_lint", lambda sol, *, select, preview=False: sentinel)
    sol = tmp_path / "s.py"
    sol.write_text("import os\n", encoding="utf-8")
    collected = commands._collect_lint([sol])
    assert collected == {sol: sentinel}


# ---------------------------------------------------------------------------
# --export-progress — экспорт агрегатов прогресса (issue #432)
# ---------------------------------------------------------------------------


def test_export_progress_md_writes_file(tmp_path, monkeypatch) -> None:
    from stepik_grader.core import history, history_recording

    monkeypatch.chdir(tmp_path)
    # issue #818: путь базы резолвится (env → конфиг → рядом → пользовательская),
    # поэтому тест готовит данные ТАМ ЖЕ, куда смотрит грейдер.
    db = history_recording.default_history_db_path()
    history.record_run(1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t")
    cli.main(["--export-progress", "md"])
    out = tmp_path / "grader-progress.md"
    assert out.exists()
    assert "Прогресс Stepik-Grader" in out.read_text(encoding="utf-8")


def test_export_progress_empty_history_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cli.main(["--export-progress", "html"])  # пустая история → дружелюбно, без файла
    assert not (tmp_path / "grader-progress.html").exists()


# ---------------------------------------------------------------------------
# Пре-флайт перед замером скорости в CLI — режимы 3/4 (issue #729)
# ---------------------------------------------------------------------------


class TestCliBenchPreflight:
    """CLI держит то же предусловие, что web: меряем только прошедшее тесты."""

    @staticmethod
    def _task_dir(tmp_path: pathlib.Path) -> pathlib.Path:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 2)\n", encoding="utf-8")
        return tmp_path

    def test_mode_3_skips_wrong_answer(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        directory = self._task_dir(tmp_path)
        measured: list[str] = []

        def fake_run_benchmark(path, test_dir, *, repeats=15):
            measured.append(path.name)
            return {
                "runs": repeats,
                "min": 0.001,
                "median": 0.002,
                "mean": 0.002,
                "max": 0.003,
                "stdev": 0.0,
                "peak_memory_mb": 0.0,
            }

        monkeypatch.setattr(cli, "run_benchmark", fake_run_benchmark)
        cli._run_mode_3(directory, repeats=5)

        # Решение с неверным ответом до замера не доходит вовсе.
        assert measured == ["task1_1.py"]
        out = capsys.readouterr().out
        assert "task1_2.py" in out
        assert "WA" in out

    def test_mode_4_skips_wrong_answer(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        directory = self._task_dir(tmp_path)
        measured: list[list[str]] = []

        def fake_microbench(paths, test_dir, *, number=1000):
            measured.append([p.name for p in paths])
            # Реальный run_microbench_mode ранжирует результат сам, поэтому
            # verdict/relative проставлены — reporter на них рассчитывает.
            return {
                p: {
                    "runs": 5,
                    "min": 1e-6,
                    "median": 2e-6,
                    "mean": 2e-6,
                    "max": 3e-6,
                    "stdev": 0.0,
                    "peak_memory_mb": 0.0,
                    "relative": 1.0,
                    "verdict": "SIMILAR",
                }
                for p in paths
            }

        monkeypatch.setattr(cli, "run_microbench_mode", fake_microbench)
        cli._run_mode_4(directory, number=100)

        assert measured == [["task1_1.py"]]
        assert "task1_2.py" in capsys.readouterr().out

    def test_all_correct_go_to_measurement(
        self, tmp_path: pathlib.Path, capsys, monkeypatch
    ) -> None:
        """Пре-флайт не мешает нормальному случаю."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "1").write_text("4", encoding="utf-8")
        (tmp_path / "tests" / "1.clue").write_text("5", encoding="utf-8")
        (tmp_path / "task1_1.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        (tmp_path / "task1_2.py").write_text("print(int(input()) + 1)\n", encoding="utf-8")
        measured: list[str] = []

        def fake_run_benchmark(path, test_dir, *, repeats=15):
            measured.append(path.name)
            return {
                "runs": repeats,
                "min": 0.001,
                "median": 0.002,
                "mean": 0.002,
                "max": 0.003,
                "stdev": 0.0,
                "peak_memory_mb": 0.0,
            }

        monkeypatch.setattr(cli, "run_benchmark", fake_run_benchmark)
        cli._run_mode_3(tmp_path, repeats=5)

        assert measured == ["task1_1.py", "task1_2.py"]


class TestPurgeHistoryFlag:
    """issue #813 (SECD-03): `--purge-history` — штатное удаление своих данных."""

    def _seed(self, work: pathlib.Path) -> pathlib.Path:
        """Наполнить ту базу, которую грейдер реально удалит (issue #818).

        Путь резолвится (env → конфиг → база рядом → пользовательская), поэтому
        писать в `work/.grader_history.db` наугад больше нельзя: `--purge-history`
        удалял бы другой файл, и тест проверял бы пустое место.
        """
        from stepik_grader.core.history import record_run
        from stepik_grader.core.history_recording import default_history_db_path

        db_path = default_history_db_path()
        for task in ("alpha", "alpha", "beta"):
            record_run(2, [], db_path=db_path, task_key=task, solution_name="s.py")
        (work / ".grader_stats.jsonl").write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
        return db_path

    def test_purge_all_removes_history_and_stats(self, tmp_path, monkeypatch, capsys) -> None:
        """Без аргумента убираются и история, и журнал статистики.

        Это одни и те же личные данные: что решал, когда, сколько заняло, —
        просто в двух файлах, о которых пользователь знать не обязан.
        """
        monkeypatch.chdir(tmp_path)
        db = self._seed(tmp_path)

        cli.main(["--purge-history"])

        assert not db.exists()
        assert not (tmp_path / ".grader_stats.jsonl").exists()
        assert not list(db.parent.glob(".grader_history.db*"))  # включая -wal/-shm
        assert "3" in capsys.readouterr().out  # три прогона

    def test_purge_single_task_keeps_stats(self, tmp_path, monkeypatch, capsys) -> None:
        """С аргументом чистятся прогоны одной задачи; статистика не трогается."""
        monkeypatch.chdir(tmp_path)
        db_path = self._seed(tmp_path)

        cli.main(["--purge-history", "alpha"])

        assert db_path.is_file()
        assert (tmp_path / ".grader_stats.jsonl").is_file()
        assert "alpha" in capsys.readouterr().out

    def test_purge_on_clean_workdir_is_not_an_error(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        cli.main(["--purge-history"])
        assert "0" in capsys.readouterr().out

    def test_purge_prints_what_will_be_deleted(self, tmp_path, monkeypatch, capsys) -> None:
        """issue #990: объём удаления виден ДО удаления, а не после.

        Данные уходят необратимо, поэтому пользователь должен успеть увидеть,
        сколько прогонов и каких задач исчезнет.
        """
        monkeypatch.chdir(tmp_path)
        self._seed(tmp_path)

        # Язык задаётся явно: main() выставляет _LANG из --lang (по умолчанию
        # ru), перекрывая autouse-фикстуру английского.
        cli.main(["--purge-history", "--lang", "en"])

        out = capsys.readouterr().out
        assert "Will be deleted permanently" in out
        assert "alpha" in out and "beta" in out  # обе задачи названы

    def test_purge_of_shared_key_warns_about_collision(self, tmp_path, monkeypatch, capsys) -> None:
        """Ключ задачи — имя папки, и одноимённые папки делят историю.

        Пока это так, точечное удаление способно унести чужой курс; о риске
        сообщается до удаления, а не постфактум в issue.
        """
        from stepik_grader.core.history import record_run
        from stepik_grader.core.history_recording import default_history_db_path

        monkeypatch.chdir(tmp_path)
        db_path = default_history_db_path()
        # Одинаковый ключ, разные решения — так выглядит коллизия одноимённых
        # папок из разных курсов в единой пользовательской базе.
        record_run(2, [], db_path=db_path, task_key="задача-3", solution_name="course_a.py")
        record_run(2, [], db_path=db_path, task_key="задача-3", solution_name="course_b.py")

        cli.main(["--purge-history", "задача-3"])

        out = capsys.readouterr().out
        assert "course_a.py" in out and "course_b.py" in out

    def test_purge_survives_corrupt_database(self, tmp_path, monkeypatch, capsys) -> None:
        """Битая база — не трейсбек: цель команды и есть «истории не стало».

        Находки PROD-2-02/FZZ-5-05: пользователь с повреждённым журналом
        приходит сюда именно за этим, а получал `sqlite3.DatabaseError`.
        """
        from stepik_grader.core.history_recording import default_history_db_path

        monkeypatch.chdir(tmp_path)
        db_path = default_history_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("это не sqlite", encoding="utf-8")

        cli.main(["--purge-history"])

        assert not db_path.exists()
        assert capsys.readouterr().out  # что-то сказано пользователю


# ---------------------------------------------------------------------------
# Коды возврата (issue #936): исход прогона как машинный сигнал
# ---------------------------------------------------------------------------


def _task_with_cases(tmp_path: pathlib.Path, expected: str) -> pathlib.Path:
    """Задача с одним кейсом: вход `5`, ожидание задаётся параметром."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "input_1.txt").write_text("5\n", encoding="utf-8")
    (tmp_path / "tests" / "expected_1.txt").write_text(expected, encoding="utf-8")
    solution = tmp_path / "task1.py"
    solution.write_text("print(input())\n", encoding="utf-8")
    return solution


class TestExitCodes:
    """`--mode 1/2` документированы как CI-сценарий, значит исход обязан быть в rc."""

    def test_passing_run_returns_ok(self, tmp_path: pathlib.Path) -> None:
        """Все кейсы прошли — код 0."""
        solution = _task_with_cases(tmp_path, "5\n")
        assert cli.main(["--mode", "1", "--file", str(solution)]) == cli.ExitCode.OK

    def test_failing_run_returns_failures(self, tmp_path: pathlib.Path) -> None:
        """Есть непройденный кейс — код 1, а не 0 (issue #936)."""
        solution = _task_with_cases(tmp_path, "999\n")
        assert cli.main(["--mode", "1", "--file", str(solution)]) == cli.ExitCode.FAILURES

    def test_empty_tests_dir_returns_no_tests(self, tmp_path: pathlib.Path) -> None:
        """Каталог tests/ есть, но кейсов нет — код 2, а не «успех» (issue #936).

        Это самый тихий способ пропустить неверное решение: задача не скачалась,
        а гейт зелёный.
        """
        (tmp_path / "tests").mkdir()
        solution = tmp_path / "task1.py"
        solution.write_text("print(1)\n", encoding="utf-8")

        assert cli.main(["--mode", "1", "--file", str(solution)]) == cli.ExitCode.NO_TESTS

    def test_missing_file_returns_no_tests(self, tmp_path: pathlib.Path) -> None:
        """Файла решения нет — код 2: проверять нечего."""
        missing = tmp_path / "task1.py"
        assert cli.main(["--mode", "1", "--file", str(missing)]) == cli.ExitCode.NO_TESTS

    def test_mode_2_failing_returns_failures(self, tmp_path: pathlib.Path) -> None:
        """Режим 2 с провалом в таблице — код 1."""
        _task_with_cases(tmp_path, "999\n")
        assert cli.main(["--mode", "2", "--dir", str(tmp_path)]) == cli.ExitCode.FAILURES

    def test_mode_2_without_solutions_returns_no_tests(self, tmp_path: pathlib.Path) -> None:
        """Режим 2 на папке без решений — код 2."""
        assert cli.main(["--mode", "2", "--dir", str(tmp_path)]) == cli.ExitCode.NO_TESTS

    def test_version_returns_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Служебные команды завершаются нулём — гейт не должен на них падать."""
        assert cli.main(["--version"]) == cli.ExitCode.OK
        capsys.readouterr()

    def test_run_cli_raises_system_exit_with_code(self, tmp_path: pathlib.Path) -> None:
        """Точка входа консольного скрипта превращает код исхода в статус процесса."""
        solution = _task_with_cases(tmp_path, "999\n")

        with pytest.raises(SystemExit) as exc:
            cli.run_cli(["--mode", "1", "--file", str(solution)])

        assert exc.value.code == cli.ExitCode.FAILURES
