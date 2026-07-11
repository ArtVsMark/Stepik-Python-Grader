"""Tests for cli.py — интерактивное меню и argparse CLI (режимы 0-4).

Покрывает ветки _interactive_menu(), не задействованные другими тестами
(Issue #21 finding #8): happy-path и error-branches для режимов 1-4, main().
Плюс non-interactive argparse CLI (Sprint 8.1): --version, --mode 1-4,
отсутствующие --file/--dir.
"""

from __future__ import annotations

import importlib.metadata
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

    def test_mode_1_dispatches_to_run_mode_1(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n", encoding="utf-8")
        called = []
        monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **kwargs: called.append(solution))
        cli.main(["--mode", "1", "--file", str(sol)])
        assert called == [str(sol)]

    def test_mode_2_dispatches_to_run_mode_2(self, monkeypatch, tmp_path: pathlib.Path) -> None:
        called = []
        monkeypatch.setattr(
            cli, "_run_mode_2", lambda directory, **kwargs: called.append(directory)
        )
        cli.main(["--mode", "2", "--dir", str(tmp_path)])
        assert called == [str(tmp_path)]

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
        assert called == [(str(tmp_path), 20)]

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
        assert called == [(str(tmp_path), 5000)]

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
        inputs = iter(["1", "/no/such/file.py"])
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

        inputs = iter(["1", str(sol)])
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

        inputs = iter(["1", str(sol)])
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
        inputs = iter(["2", "/no/such/dir"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        inputs = iter(["2", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "No solution files found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Режим 3 — Benchmark solutions in folder
# ---------------------------------------------------------------------------


class TestMode3:
    def test_directory_not_found(self, capsys, monkeypatch) -> None:
        inputs = iter(["3", "/no/such/dir"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        inputs = iter(["3", str(tmp_path)])
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
            if "task2" in path:
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

        inputs = iter(["3", str(tmp_path)])
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
        inputs = iter(["4", "/no/such/dir"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Directory not found" in capsys.readouterr().out

    def test_no_solution_files_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        inputs = iter(["4", str(tmp_path)])
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
            cli, "_resolve_test_dir_from_input", lambda *a, **k: str(tmp_path / "does_not_exist")
        )
        inputs = iter(["4", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert "Tests not found" in capsys.readouterr().out

    def test_no_test_cases_found(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        """run_microbench_mode() returning {} hits the 'No test cases found' branch."""
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()

        monkeypatch.setattr(cli, "_ask_micro_profile", lambda: 500)
        monkeypatch.setattr(cli, "run_microbench_mode", lambda *a, **k: {})
        inputs = iter(["4", str(tmp_path)])
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
        inputs = iter(["4", str(tmp_path)])
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
        inputs = iter(["4", str(tmp_path)])
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
        """`python -m stepik_grader --version` печатает версию и завершается 0."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "stepik_grader", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert cli.__version__ in result.stdout


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
        cli._run_mode_4(str(tmp_path), 500)
        out = capsys.readouterr().out
        # Сноска о методике Py-heap появляется ровно один раз.
        assert out.count("Py-heap") >= 1
        assert "tracemalloc" in out

    def test_no_footnote_in_json_output(self, tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
        (tmp_path / "task1.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        monkeypatch.setattr(cli, "run_microbench_mode", self._bench)
        cli._run_mode_4(str(tmp_path), 500, output="json")
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
        # Последовательный ввод: сначала выбор режима "1", потом пустой путь.
        inputs = iter(["1", ""])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert picked == [str(sol)]

    def test_empty_input_dialog_wants_dir_for_mode_2(self, monkeypatch, tmp_path) -> None:
        got = []
        monkeypatch.setattr(
            cli, "_pick_path_via_dialog", lambda *, want_dir: got.append(want_dir) or str(tmp_path)
        )
        monkeypatch.setattr(cli, "_run_mode_2", lambda directory, **k: None)
        inputs = iter(["2", ""])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli._interactive_menu()
        assert got == [True]  # для папки — askdirectory

    def test_empty_input_and_dialog_cancelled_is_graceful(self, monkeypatch, capsys) -> None:
        # autouse-фикстура уже возвращает None (отмена диалога).
        inputs = iter(["1", ""])
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
    def test_clear_cache_prints_removed_count_and_exits(self, monkeypatch, capsys) -> None:
        removed_calls = []

        class _StubCache:
            def clear(self) -> int:
                removed_calls.append(True)
                return 3

        monkeypatch.setattr(cli, "GraderCache", _StubCache)
        cli.main(["--clear-cache"])
        out = capsys.readouterr().out
        assert removed_calls == [True]
        assert "3" in out

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
        assert called == [{"port": 9090, "root": None, "confine": True}]

    def test_serve_uses_default_port(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve"])
        assert called == [{"port": 8000, "root": None, "confine": True}]

    def test_serve_passes_root(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--root", "/some/dir"])
        assert called == [{"port": 8000, "root": "/some/dir", "confine": True}]

    def test_serve_no_root_confinement_disables_confine(self, monkeypatch) -> None:
        from stepik_grader import web

        called = []
        monkeypatch.setattr(web, "run_server", lambda **kwargs: called.append(kwargs))
        cli.main(["--serve", "--no-root-confinement"])
        assert called == [{"port": 8000, "root": None, "confine": False}]


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
            cli.options, "CONFIG", types.SimpleNamespace(record_stats=True, use_cache=False)
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
