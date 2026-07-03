"""Тесты для новых CLI-фич Sprint E (issues #50, #51) и части roadmap (#53/#54/#58):

    - i18n: русский по умолчанию, --lang en переключает язык (D-01)
    - --verbose/--quiet: управление подробностью режимов 1/2 (D-03)
    - --output json/csv/markdown: машиночитаемый вывод для режимов 1-4
      (D-04, #53, #58)
    - --watch: перезапуск --mode 1/2 при изменении файла (#54)

В отдельном файле, а не в test_cli.py: там уже есть autouse-фикстура,
форсирующая _LANG="en" для существующих (написанных до i18n) тестов, которая
помешала бы проверить настоящий русский дефолт.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib
import sys
import types

import pytest

from stepik_grader import cli


@pytest.fixture(autouse=True)
def _restore_lang():
    """Isolate cli._LANG across tests -- main() mutates it as a module global."""
    original = cli._LANG
    yield
    cli._LANG = original


# ---------------------------------------------------------------------------
# --lang (issue #51 D-01)
# ---------------------------------------------------------------------------


def test_default_language_is_russian(capsys) -> None:
    cli.main(["--mode", "1", "--file", "/no/such/file.py"])
    assert "Файл не найден" in capsys.readouterr().out


def test_lang_en_switches_to_english(capsys) -> None:
    cli.main(["--mode", "1", "--file", "/no/such/file.py", "--lang", "en"])
    assert "File not found" in capsys.readouterr().out


def test_lang_ru_explicit(capsys) -> None:
    cli.main(["--mode", "1", "--file", "/no/such/file.py", "--lang", "ru"])
    assert "Файл не найден" in capsys.readouterr().out


def test_lang_invalid_choice_rejected() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--mode", "1", "--file", "x", "--lang", "de"])


def test_interactive_menu_is_russian_by_default(capsys, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *a: "0")
    cli.main([])
    out = capsys.readouterr().out
    assert "Проверить одно решение" in out
    assert "До свидания!" in out


# ---------------------------------------------------------------------------
# --verbose / --quiet (issue #50 D-03)
# ---------------------------------------------------------------------------


def _make_solution(tmp_path: pathlib.Path) -> pathlib.Path:
    sol = tmp_path / "task1.py"
    sol.write_text("print(int(input()) + 1)\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text("4", encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text("5", encoding="utf-8")
    return sol


def test_mode_1_verbose_by_default(tmp_path: pathlib.Path, capsys) -> None:
    """--mode 1 without flags keeps its historical always-verbose behavior."""
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--lang", "en"])
    assert "Test 1:" in capsys.readouterr().out


def test_mode_1_quiet_suppresses_per_case_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--quiet", "--lang", "en"])
    out = capsys.readouterr().out
    assert "Test 1:" not in out
    assert "task1.py" in out  # summary table still prints


def test_mode_2_quiet_by_default(tmp_path: pathlib.Path, capsys) -> None:
    """--mode 2 without flags keeps its historical no-per-case-output behavior."""
    _make_solution(tmp_path)
    cli.main(["--mode", "2", "--dir", str(tmp_path), "--lang", "en"])
    assert "Test 1:" not in capsys.readouterr().out


def test_mode_2_verbose_enables_per_case_output(tmp_path: pathlib.Path, capsys) -> None:
    _make_solution(tmp_path)
    cli.main(["--mode", "2", "--dir", str(tmp_path), "--verbose", "--lang", "en"])
    assert "Test 1:" in capsys.readouterr().out


def test_verbose_and_quiet_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--mode", "1", "--file", "x", "--verbose", "--quiet"])


# ---------------------------------------------------------------------------
# --output json (issue #50 D-04)
# ---------------------------------------------------------------------------


def test_mode_1_json_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--output", "json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["file"] == str(sol)
    assert data["passed"] == 1
    assert data["total"] == 1


def test_mode_1_json_output_no_table_printed(tmp_path: pathlib.Path, capsys) -> None:
    """--output json must print exactly one JSON line, no rich/plain table."""
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--output", "json"])
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    json.loads(out)  # doesn't raise


def test_mode_2_json_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "2", "--dir", str(tmp_path), "--output", "json"])
    data = json.loads(capsys.readouterr().out)
    assert str(sol) in data["results"]
    assert data["results"][str(sol)]["passed"] == 1


def test_mode_3_json_output(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    sol = tmp_path / "task1.py"
    sol.write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    def fake_run_benchmark(path, test_dir, *, repeats=15):
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
    cli.main(["--mode", "3", "--dir", str(tmp_path), "--output", "json"])
    data = json.loads(capsys.readouterr().out)
    assert str(sol) in data["results"]
    assert data["results"][str(sol)]["runs"] == 5


def test_mode_4_json_output(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
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

    monkeypatch.setattr(cli, "run_microbench_mode", fake_run_microbench_mode)
    cli.main(["--mode", "4", "--dir", str(tmp_path), "--output", "json"])
    data = json.loads(capsys.readouterr().out)
    assert "." in data["groups"]
    assert data["groups"]["."]["results"]


def test_output_invalid_choice_rejected() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--mode", "1", "--file", "x", "--output", "xml"])


# ---------------------------------------------------------------------------
# --output csv (issue #53) / --output markdown (issue #58)
# ---------------------------------------------------------------------------


def test_mode_1_csv_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--output", "csv"])
    out = capsys.readouterr().out
    reader = csv.DictReader(io.StringIO(out))
    rows = list(reader)
    assert reader.fieldnames == ["index", "passed", "verdict", "time", "memory", "error"]
    assert len(rows) == 1
    assert rows[0]["passed"] == "True"
    assert rows[0]["verdict"] == "AC"


def test_mode_1_markdown_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--output", "markdown"])
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0].startswith("| index |")
    assert lines[1].startswith("| ---")
    assert "AC" in lines[2]


def test_mode_2_csv_output(tmp_path: pathlib.Path, capsys) -> None:
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "2", "--dir", str(tmp_path), "--output", "csv"])
    reader = csv.DictReader(io.StringIO(capsys.readouterr().out))
    rows = list(reader)
    assert reader.fieldnames == [
        "file",
        "total",
        "passed",
        "failed",
        "errors",
        "total_time",
        "avg_time",
        "peak_memory_mb",
        "first_fail",
    ]
    assert rows[0]["file"] == str(sol)
    assert rows[0]["passed"] == "1"


def test_mode_3_csv_output(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    sol = tmp_path / "task1.py"
    sol.write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    def fake_run_benchmark(path, test_dir, *, repeats=15):
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
    cli.main(["--mode", "3", "--dir", str(tmp_path), "--output", "csv"])
    reader = csv.DictReader(io.StringIO(capsys.readouterr().out))
    rows = list(reader)
    assert rows[0]["file"] == str(sol)
    assert rows[0]["runs"] == "5"


def test_mode_4_markdown_output(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    sol = tmp_path / "task1.py"
    sol.write_text("print(1)\n", encoding="utf-8")
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

    monkeypatch.setattr(cli, "run_microbench_mode", fake_run_microbench_mode)
    cli.main(["--mode", "4", "--dir", str(tmp_path), "--output", "markdown"])
    out = capsys.readouterr().out
    assert "| group | file |" in out
    assert str(sol) in out


# ---------------------------------------------------------------------------
# --watch (issue #54)
# ---------------------------------------------------------------------------


def test_watch_missing_dependency_prints_message(
    tmp_path: pathlib.Path, capsys, monkeypatch
) -> None:
    """watchfiles isn't a dev-extra dependency -- must degrade gracefully, not crash."""
    monkeypatch.setitem(sys.modules, "watchfiles", None)
    sol = _make_solution(tmp_path)
    cli.main(["--mode", "1", "--file", str(sol), "--watch", "--lang", "en"])
    assert "watchfiles" in capsys.readouterr().out


def test_watch_reruns_on_change(tmp_path: pathlib.Path, capsys, monkeypatch) -> None:
    """A fake watchfiles.watch() yielding one change causes exactly one rerun."""
    sol = _make_solution(tmp_path)

    def fake_watch(_path):
        yield {("changed", str(sol))}

    fake_module = types.ModuleType("watchfiles")
    fake_module.watch = fake_watch
    monkeypatch.setitem(sys.modules, "watchfiles", fake_module)
    monkeypatch.setattr(cli.os, "system", lambda cmd: None)

    cli.main(["--mode", "1", "--file", str(sol), "--watch", "--lang", "en"])
    out = capsys.readouterr().out
    # Verbose per-case line printed once before the loop, once after the
    # single yielded change -- the watch-path banner also contains the
    # filename, so counting "task1.py" directly would overcount.
    assert out.count("Test 1:") == 2


def test_watch_rejected_for_mode_3() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--mode", "3", "--dir", ".", "--watch"])


def test_watch_rejected_for_mode_4() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--mode", "4", "--dir", ".", "--watch"])
