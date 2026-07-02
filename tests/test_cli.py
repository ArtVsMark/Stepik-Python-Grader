"""Tests for cli.py — интерактивное меню (режимы 0-4).

Покрывает ветки _interactive_menu(), не задействованные другими тестами
(Issue #21 finding #8): happy-path и error-branches для режимов 1-4, main().
"""

from __future__ import annotations

import pathlib

import cli


def test_main_delegates_to_interactive_menu(monkeypatch) -> None:
    """main() — тонкая обёртка над _interactive_menu()."""
    called = []
    monkeypatch.setattr(cli, "_interactive_menu", lambda: called.append(True))
    cli.main()
    assert called == [True]


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
