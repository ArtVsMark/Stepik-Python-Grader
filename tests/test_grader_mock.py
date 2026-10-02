"""test_grader_mock.py — mock-тесты для grader.py.

Покрывает:
  - run_single_test: AC / WA / TLE / RE (returncode != 0) через мок subprocess.Popen
  - run_tests: агрегация статистики (passed/failed/errors/first_fail)
  - _detect_run_mode: приоритет meta.json → .type → AST
  - is_solution_file / _SOLUTION_FILE_RE: все форматы имён + баг task_1.py
  - is_function_only_solution: function-only vs script
  - _is_python_code_block: stdin-данные vs python-код
  - _parse_testblock_file: маркеры # TEST_N:
  - load_test_cases: формат 1 (legacy .clue), формат 2 (input_N.txt),
    формат 3 (input.txt+output.txt)
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# grader.py лежит в корне проекта — убедимся что он в sys.path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from stepik_grader import grader

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_popen_mock(
    stdout: bytes = b"42\n",
    stderr: bytes = b"",
    returncode: int = 0,
    timeout: bool = False,
) -> MagicMock:
    """Фабрика: mock ``subprocess.Popen`` под drain-путь ``_run_with_polling``.

    issue #629: ``run_single_test`` всегда задаёт ``max_output_bytes`` (из CONFIG),
    поэтому грейд идёт poll/drain-путём — stdout/stderr читаются порциями до EOF
    в фоновых потоках, а завершение ждётся ``proc.wait(timeout=...)`` (а не
    единым ``communicate()``). Мок отдаёт вывод одной порцией + EOF (``b""``);
    один инстанс дренируется один раз (свежий mock на кейс — ``fake_popen`` ниже).

    ``timeout=True``: первый ``wait()`` бросает ``TimeoutExpired`` (реальный TLE),
    второй (reap после ``proc.kill()``) возвращает код.
    """
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.returncode = returncode
    mock_proc.kill = MagicMock()
    mock_proc.poll.return_value = returncode  # not None → finally-guard не убивает
    # Дренаж читает порциями до сигнального b"" (одна порция вывода + EOF).
    #
    # issue #952: дренаж зовёт `read1`, а не `read`. Настоящий pipe — это
    # `BufferedReader`, у него есть оба; `read` ждал бы полный буфер или EOF и
    # терял вывод решения, оставившего живого внука. Мок обязан отражать ту же
    # пару, иначе он проверяет метод, которого продуктовый код не вызывает.
    mock_proc.stdout.read1.side_effect = [stdout, b""]
    mock_proc.stderr.read1.side_effect = [stderr, b""]
    mock_proc.stdout.read.side_effect = [stdout, b""]
    mock_proc.stderr.read.side_effect = [stderr, b""]

    if timeout:
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="python", timeout=10.0),
            returncode,  # reap-wait после proc.kill()
        ]
    else:
        mock_proc.wait.return_value = returncode

    # issue #1248: отдельного fast-пути через `communicate()` больше нет —
    # сбор вывода идёт дренажом при любых значениях лимита и отмены. Стаб
    # оставлен на случай прямых вызовов из чужих тестов.
    mock_proc.communicate.return_value = (stdout, stderr)
    return mock_proc


def _make_testcase(
    index: int = 1,
    input_lines: list[str] | None = None,
    expected_lines: list[str] | None = None,
    test_type: str = "stdin",
) -> grader.TestCase:
    return grader.TestCase(
        index=index,
        input_lines=input_lines or ["5"],
        expected_lines=expected_lines or ["42"],
        test_type=test_type,
    )


# ---------------------------------------------------------------------------
# set_runner — Runner injection point (issue #266)
# ---------------------------------------------------------------------------


class TestSetRunner:
    def test_replaces_active_runner(self, monkeypatch) -> None:
        from stepik_grader.core import runner as runner_mod

        original = runner_mod._RUNNER
        try:
            fake = object()
            runner_mod.set_runner(fake)  # type: ignore[arg-type]
            assert runner_mod._RUNNER is fake
        finally:
            runner_mod.set_runner(original)

    def test_default_is_local_runner(self) -> None:
        from stepik_grader.core import runner as runner_mod
        from stepik_grader.core.runner import LocalRunner

        assert isinstance(runner_mod._RUNNER, LocalRunner)


# ---------------------------------------------------------------------------
# run_single_test — stdin mode
# ---------------------------------------------------------------------------


class TestRunSingleTestStdin:
    """Тесты run_single_test в stdin-режиме через mock subprocess.Popen."""

    def test_ac_correct_output(self, tmp_path: pathlib.Path) -> None:
        """Решение даёт правильный вывод → passed=True, verdict=AC."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(42)\n")
        case = _make_testcase(expected_lines=["42"])

        mock_proc = _make_popen_mock(stdout=b"42\n")
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, measure_memory=False)

        assert result["passed"] is True
        assert result["verdict"] == "AC"
        assert result["error"] == ""
        assert result["timed_out"] is False
        assert result["output"] == ["42"]

    def test_wa_wrong_output(self, tmp_path: pathlib.Path) -> None:
        """Решение даёт неверный вывод → passed=False, verdict=WA, diff не пустой."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(0)\n")
        case = _make_testcase(expected_lines=["42"])

        mock_proc = _make_popen_mock(stdout=b"0\n")
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, measure_memory=False)

        assert result["passed"] is False
        assert result["verdict"] == "WA"
        assert result["diff"] != ""

    def test_re_nonzero_returncode(self, tmp_path: pathlib.Path) -> None:
        """Процесс завершился с ненулевым кодом → verdict=RE, error содержит stderr."""
        sol = tmp_path / "task1.py"
        sol.write_text("raise ValueError('boom')\n")
        case = _make_testcase()

        mock_proc = _make_popen_mock(
            stdout=b"",
            stderr=b"ValueError: boom",
            returncode=1,
        )
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, measure_memory=False)

        assert result["passed"] is False
        assert result["verdict"] == "RE"
        assert "ValueError" in result["error"]
        assert result["timed_out"] is False

    def test_tle_timeout(self, tmp_path: pathlib.Path) -> None:
        """proc.wait(timeout) → TimeoutExpired → verdict=TLE, timed_out=True.

        issue #629: грейд с лимитом идёт drain-путём (``_run_with_polling``):
        вывод сливают фоновые reader'ы, а завершение ждёт ``proc.wait(timeout)``.
        Мок: 1-й ``wait()`` бросает ``TimeoutExpired`` (TLE), 2-й (reap после
        ``proc.kill()``) возвращает код — точная имитация реального TLE-пути.
        """
        sol = tmp_path / "task1.py"
        sol.write_text("import time; time.sleep(100)\n")
        case = _make_testcase()

        mock_proc = _make_popen_mock(timeout=True)
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, timeout=0.01, measure_memory=False)

        assert result["passed"] is False
        assert result["verdict"] == "TLE"
        assert result["timed_out"] is True

    def test_multiline_output_ac(self, tmp_path: pathlib.Path) -> None:
        """Несколько строк вывода — все совпадают → AC."""
        sol = tmp_path / "task1.py"
        sol.write_text("print('a'); print('b')\n")
        case = _make_testcase(input_lines=[], expected_lines=["a", "b"])

        mock_proc = _make_popen_mock(stdout=b"a\nb\n")
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, measure_memory=False)

        assert result["passed"] is True
        assert result["output"] == ["a", "b"]

    def test_cancelled_verdict(self, tmp_path: pathlib.Path) -> None:
        """cancel_event set mid-run (issue #262) -- real subprocess, not
        mocked (poll-loop timing matters). Must be verdict=CANCELLED, not
        TLE -- a cancelled run is not "your solution is too slow"."""
        sol = tmp_path / "task1.py"
        sol.write_text("import time; time.sleep(30)\n")
        case = _make_testcase()

        cancel_event = threading.Event()

        def _cancel_soon() -> None:
            cancel_event.wait(0.3)
            cancel_event.set()

        threading.Thread(target=_cancel_soon, daemon=True).start()
        result = grader.run_single_test(
            str(sol), case, timeout=60.0, measure_memory=False, cancel_event=cancel_event
        )

        assert result["passed"] is False
        assert result["verdict"] == "CANCELLED"
        assert result["timed_out"] is False
        assert result["error"] != ""

    def test_sandbox_violation_verdict(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """SandboxRunner (issue #266) sets RunOutcome.sandbox_violation --
        must map to a distinct SANDBOX_VIOLATION verdict, not RE/TLE. Uses a
        stub Runner (no real sandbox backend needed at this layer -- this
        tests grader_core's own interpretation of the field)."""
        from stepik_grader.core import runner as runner_mod
        from stepik_grader.core.runner import RunOutcome

        class _StubSandboxRunner:
            def run(self, spec):
                return RunOutcome(sandbox_violation="memory", elapsed=1.5, peak_memory_mb=512.0)

        monkeypatch.setattr(runner_mod, "_RUNNER", _StubSandboxRunner())
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n")
        case = _make_testcase()

        result = grader.run_single_test(str(sol), case, measure_memory=False)

        assert result["passed"] is False
        assert result["verdict"] == "SANDBOX_VIOLATION"
        assert result["timed_out"] is False
        assert "memory" in result["error"]
        assert result["memory"] == 512.0

    def test_result_keys_present(self, tmp_path: pathlib.Path) -> None:
        """Словарь результата содержит все ожидаемые ключи."""
        sol = tmp_path / "task1.py"
        sol.write_text("print(1)\n")
        case = _make_testcase(expected_lines=["1"])

        mock_proc = _make_popen_mock(stdout=b"1\n")
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_single_test(str(sol), case, measure_memory=False)

        expected_keys = (
            "passed",
            "output",
            "expected",
            "diff",
            "time",
            "memory",
            "error",
            "timed_out",
            "verdict",
        )
        for key in expected_keys:
            assert key in result, f"Ключ '{key}' отсутствует в результате"


# ---------------------------------------------------------------------------
# run_tests — агрегация
# ---------------------------------------------------------------------------


class TestRunTests:
    """Тесты агрегатора run_tests: passed/failed/errors/first_fail."""

    def _run_with_mocked_subprocess(
        self,
        tmp_path: pathlib.Path,
        responses: list[tuple[bytes, bytes, int]],
    ) -> dict[str, Any]:
        """Запускает run_tests на решении с N тест-кейсами через мок."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        for i, (stdout, _, _) in enumerate(responses, 1):
            expected = stdout.decode().strip()
            (test_dir / f"input_{i}.txt").write_text(f"{i}\n")
            (test_dir / f"expected_{i}.txt").write_text(expected + "\n")

        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        call_count = 0

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            out, err, rc = responses[call_count % len(responses)]
            call_count += 1
            return _make_popen_mock(stdout=out, stderr=err, returncode=rc)

        with patch("subprocess.Popen", side_effect=fake_popen):
            return grader.run_tests(sol, test_dir)

    def test_all_passed(self, tmp_path: pathlib.Path) -> None:
        responses = [(b"1\n", b"", 0), (b"2\n", b"", 0), (b"3\n", b"", 0)]
        result = self._run_with_mocked_subprocess(tmp_path, responses)
        assert result["passed"] == 3
        assert result["failed"] == 0
        assert result["errors"] == 0
        assert result["first_fail"] is None

    def test_one_failed(self, tmp_path: pathlib.Path) -> None:
        """Второй тест даёт неверный вывод."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("1\n")
        (test_dir / "expected_1.txt").write_text("1\n")
        (test_dir / "input_2.txt").write_text("2\n")
        (test_dir / "expected_2.txt").write_text("2\n")

        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        responses = [b"1\n", b"WRONG\n"]
        call_count = 0

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            out = responses[call_count % len(responses)]
            call_count += 1
            return _make_popen_mock(stdout=out)

        with patch("subprocess.Popen", side_effect=fake_popen):
            result = grader.run_tests(sol, test_dir)

        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["first_fail"] == 2

    def test_runtime_error_counted(self, tmp_path: pathlib.Path) -> None:
        """RE (returncode=1) → errors += 1, first_fail устанавливается."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("1\n")
        (test_dir / "expected_1.txt").write_text("1\n")

        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        mock_proc = _make_popen_mock(stdout=b"", stderr=b"NameError", returncode=1)
        with patch("subprocess.Popen", return_value=mock_proc):
            result = grader.run_tests(sol, test_dir)

        assert result["errors"] == 1
        assert result["passed"] == 0
        assert result["first_fail"] == 1

    def test_total_time_is_sum(self, tmp_path: pathlib.Path) -> None:
        """total_time >= 0 и avg_time = total_time / total."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("1\n")
        (test_dir / "expected_1.txt").write_text("1\n")
        (test_dir / "input_2.txt").write_text("2\n")
        (test_dir / "expected_2.txt").write_text("2\n")

        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        responses = [b"1\n", b"2\n"]
        call_count = 0

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            out = responses[call_count % 2]
            call_count += 1
            return _make_popen_mock(stdout=out)

        with patch("subprocess.Popen", side_effect=fake_popen):
            result = grader.run_tests(sol, test_dir)

        assert result["total"] == 2
        assert result["total_time"] >= 0
        assert abs(result["avg_time"] - result["total_time"] / 2) < 1e-9

    def test_cases_include_stdin(self, tmp_path: pathlib.Path) -> None:
        """issue #397: каждый кейс в result["cases"] несёт свой stdin, чтобы
        web-путь (grade_path → ErrorCard) не перечитывал тест-кейсы вторым
        проходом. stdin == "\\n".join(input_lines) (без хвостового перевода)."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "input_1.txt").write_text("3\n7\n")
        (test_dir / "expected_1.txt").write_text("10\n")
        (test_dir / "input_2.txt").write_text("5\n")
        (test_dir / "expected_2.txt").write_text("5\n")

        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        responses = [b"10\n", b"5\n"]
        call_count = 0

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            out = responses[call_count % 2]
            call_count += 1
            return _make_popen_mock(stdout=out)

        with patch("subprocess.Popen", side_effect=fake_popen):
            result = grader.run_tests(sol, test_dir)

        assert [c["stdin"] for c in result["cases"]] == ["3\n7", "5"]


# ---------------------------------------------------------------------------
# run_tests / run_benchmark — progress_callback + cancel_event (issue #262)
# ---------------------------------------------------------------------------


class TestRunTestsProgressAndCancel:
    """progress_callback/cancel_event на run_tests — оба default None, не
    трогает CLI/синхронный /api/grade (они их не передают)."""

    def _make_test_dir(self, tmp_path: pathlib.Path, n: int) -> pathlib.Path:
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        for i in range(1, n + 1):
            (test_dir / f"input_{i}.txt").write_text(f"{i}\n")
            (test_dir / f"expected_{i}.txt").write_text(f"{i}\n")
        return test_dir

    def test_progress_callback_invoked_once_per_case(self, tmp_path: pathlib.Path) -> None:
        test_dir = self._make_test_dir(tmp_path, 3)
        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            return _make_popen_mock(stdout=b"1\n")

        ticks: list[int] = []
        with patch("subprocess.Popen", side_effect=fake_popen):
            grader.run_tests(sol, test_dir, progress_callback=ticks.append)

        assert ticks == [1, 1, 1]

    def test_default_kwargs_unchanged(self, tmp_path: pathlib.Path) -> None:
        """No progress_callback/cancel_event at all — output identical to
        pre-#262 shape (regression guard for the "CLI unaffected" promise)."""
        test_dir = self._make_test_dir(tmp_path, 2)
        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout=b"1\n")):
            result = grader.run_tests(sol, test_dir)

        assert result["total"] == 2
        assert set(result.keys()) == {
            "total",
            "passed",
            "failed",
            "errors",
            "total_time",
            "avg_time",
            "peak_memory_mb",
            "first_fail",
            # issue #935: предупреждения загрузки набора — часть результата.
            # Ключ присутствует всегда, на чистом наборе это пустой список:
            # потребителю не нужно гадать, «нет предупреждений» это или «поле
            # забыли положить».
            "warnings",
            "cases",
        }
        assert result["warnings"] == []

    def test_cancel_event_stops_before_all_cases_run(self, tmp_path: pathlib.Path) -> None:
        """Real subprocess (not mocked) -- pre-set cancel_event must stop the
        per-case loop before the last case ever launches a subprocess."""
        test_dir = self._make_test_dir(tmp_path, 5)
        sol = tmp_path / "task1.py"
        sol.write_text("print(input())\n")

        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before run_tests() even starts
        result = grader.run_tests(sol, test_dir, cancel_event=cancel_event)

        assert result["total"] == 0
        assert result["cases"] == []


class TestRunBenchmarkProgressAndCancel:
    def _make_test_dir(self, tmp_path: pathlib.Path, n: int) -> pathlib.Path:
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        for i in range(1, n + 1):
            (test_dir / f"input_{i}.txt").write_text(f"{i}\n")
            (test_dir / f"expected_{i}.txt").write_text(f"{i}\n")
        return test_dir

    def test_progress_callback_invoked_per_case_times_repeats(self, tmp_path: pathlib.Path) -> None:
        test_dir = self._make_test_dir(tmp_path, 2)
        sol = tmp_path / "task1.py"
        sol.write_text("# mock\n")

        def fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            return _make_popen_mock(stdout=b"1\n")

        ticks: list[int] = []
        with patch("subprocess.Popen", side_effect=fake_popen):
            grader.run_benchmark(sol, test_dir, repeats=3, progress_callback=ticks.append)

        assert ticks == [1] * 6  # 2 cases * 3 repeats

    def test_cancel_event_pre_set_returns_cancelled_flag(self, tmp_path: pathlib.Path) -> None:
        """Real subprocess -- pre-set cancel_event before the first repeat."""
        test_dir = self._make_test_dir(tmp_path, 1)
        sol = tmp_path / "task1.py"
        sol.write_text("print(input())\n")

        cancel_event = threading.Event()
        cancel_event.set()
        result = grader.run_benchmark(sol, test_dir, repeats=5, cancel_event=cancel_event)

        assert result["cancelled"] is True
        assert result["runs"] == 0


# ---------------------------------------------------------------------------
# is_solution_file — регулярное выражение _SOLUTION_FILE_RE
# ---------------------------------------------------------------------------


class TestIsSolutionFile:
    """Проверяет все допустимые и недопустимые имена файлов решений.

    Особое внимание: task_1.py (стиль downloader.py) — потенциальный баг.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "task.py",
            "task1.py",
            "task1_2.py",
            "task4_1.py",
            "task7_3.py",
            "task_1.py",  # стиль downloader.py
            "task_2.py",
        ],
    )
    def test_valid_names(self, name: str) -> None:
        assert grader.is_solution_file(name), (
            f"'{name}' должен распознаваться как файл решения. "
            f"Проверьте _SOLUTION_FILE_RE = {grader._SOLUTION_FILE_RE.pattern!r}"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "solution.py",
            "main.py",
            "task1.txt",
            "task1_.py",
            "task_1_2.py",
            "Task1.py",
            "task 1.py",
            "",
            "task1.py.bak",
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        assert not grader.is_solution_file(name), (
            f"'{name}' НЕ должен распознаваться как файл решения."
        )


# ---------------------------------------------------------------------------
# is_function_only_solution
# ---------------------------------------------------------------------------


class TestIsFunctionOnlySolution:
    def test_simple_function(self) -> None:
        code = textwrap.dedent("""\
            def add(a, b):
                return a + b
        """)
        assert grader.is_function_only_solution(code) is True

    def test_function_with_import(self) -> None:
        code = textwrap.dedent("""\
            from datetime import date

            def days_diff(d1, d2):
                return (d2 - d1).days
        """)
        assert grader.is_function_only_solution(code) is True

    def test_function_with_constant_assignment(self) -> None:
        code = textwrap.dedent("""\
            MOD = 10**9 + 7

            def solve(n):
                return n % MOD
        """)
        assert grader.is_function_only_solution(code) is True

    def test_script_with_print(self) -> None:
        code = textwrap.dedent("""\
            def solve(n):
                return n * 2

            print(solve(5))
        """)
        assert grader.is_function_only_solution(code) is False

    def test_script_with_for_loop(self) -> None:
        code = textwrap.dedent("""\
            for i in range(10):
                print(i)
        """)
        assert grader.is_function_only_solution(code) is False

    def test_no_functions(self) -> None:
        code = "x = 42\n"
        assert grader.is_function_only_solution(code) is False

    def test_syntax_error(self) -> None:
        code = "def broken(:\n    pass\n"
        assert grader.is_function_only_solution(code) is False


# ---------------------------------------------------------------------------
# _is_python_code_block
# ---------------------------------------------------------------------------


class TestIsPythonCodeBlock:
    def test_function_call(self) -> None:
        assert grader._is_python_code_block("print(func(x))") is True

    def test_assignment_with_call(self) -> None:
        assert grader._is_python_code_block("r = wins([1, 2, 3])") is True

    def test_plain_numbers(self) -> None:
        assert grader._is_python_code_block("10\n20\n30") is False

    def test_date_string(self) -> None:
        assert grader._is_python_code_block("04.11.2021") is False

    def test_empty_string(self) -> None:
        assert grader._is_python_code_block("") is False

    def test_whitespace_only(self) -> None:
        assert grader._is_python_code_block("   \n  ") is False

    def test_syntax_error_returns_false(self) -> None:
        assert grader._is_python_code_block("def (\n") is False


# ---------------------------------------------------------------------------
# _parse_testblock_file
# ---------------------------------------------------------------------------


class TestParseTestblockFile:
    def test_basic_blocks(self) -> None:
        text = textwrap.dedent("""\
            # TEST_1:
            5
            # TEST_2:
            10 20
        """)
        blocks = grader._parse_testblock_file(text)
        assert len(blocks) == 2
        assert blocks[0] == "5"
        assert blocks[1] == "10 20"

    def test_empty_block_preserved(self) -> None:
        text = "# TEST_1:\nhello\n# TEST_2:\n# TEST_3:\nworld\n"
        blocks = grader._parse_testblock_file(text)
        assert len(blocks) == 3
        assert blocks[1] == ""

    def test_input_data_header_ignored(self) -> None:
        """Заголовок # INPUT DATA: ДО первого # TEST_N: пропускается (issue #410, B6)."""
        text = textwrap.dedent("""\
            # INPUT DATA:
            # TEST_1:
            42
        """)
        blocks = grader._parse_testblock_file(text)
        assert len(blocks) == 1
        assert blocks[0] == "42"

    def test_input_data_inside_block_preserved(self) -> None:
        """# INPUT DATA: ВНУТРИ блока — данные кейса, сохраняется (issue #410, B6)."""
        text = textwrap.dedent("""\
            # TEST_1:
            # INPUT DATA:
            42
        """)
        blocks = grader._parse_testblock_file(text)
        assert blocks == ["# INPUT DATA:\n42"]

    def test_no_blocks(self) -> None:
        assert grader._parse_testblock_file("just some text\n") == []


# ---------------------------------------------------------------------------
# load_test_cases — форматы 1, 2, 3
# ---------------------------------------------------------------------------


class TestLoadTestCases:
    def test_format2_input_expected(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "input_1.txt").write_text("5\n")
        (tmp_path / "expected_1.txt").write_text("25\n")
        (tmp_path / "input_2.txt").write_text("3\n")
        (tmp_path / "expected_2.txt").write_text("9\n")

        cases = grader.load_test_cases(tmp_path)
        assert len(cases) == 2
        assert cases[0].index == 1
        assert cases[0].input_lines == ["5"]
        assert cases[0].expected_lines == ["25"]

    def test_format1_legacy_clue(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "1").write_text("10\n")
        (tmp_path / "1.clue").write_text("100\n")

        cases = grader.load_test_cases(tmp_path)
        assert len(cases) == 1
        assert cases[0].index == 1
        assert cases[0].test_type == "stdin"

    def test_format1_with_type_file(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "1").write_text("x = 5\n")
        (tmp_path / "1.clue").write_text("25\n")
        (tmp_path / "1.type").write_text("function\n")

        cases = grader.load_test_cases(tmp_path)
        assert cases[0].test_type == "function"

    def test_format3_testblock(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "input.txt").write_text("# TEST_1:\n5\n# TEST_2:\n3\n")
        (tmp_path / "output.txt").write_text("# TEST_1:\n25\n# TEST_2:\n9\n")

        cases = grader.load_test_cases(tmp_path)
        assert len(cases) == 2
        assert cases[0].expected_lines == ["25"]
        assert cases[1].expected_lines == ["9"]

    def test_empty_dir_returns_empty(self, tmp_path: pathlib.Path) -> None:
        cases = grader.load_test_cases(tmp_path)
        assert cases == []


# ---------------------------------------------------------------------------
# _detect_run_mode
# ---------------------------------------------------------------------------


class TestDetectRunMode:
    def test_meta_json_takes_priority(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("def solve(n): return n\n")
        (tmp_path / "meta.json").write_text(json.dumps({"function_name": "solve"}))
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        mode = grader._detect_run_mode(sol, test_dir)
        assert mode == "function"

    def test_type_file_overrides_ast(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("print(input())\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "1.type").write_text("function\n")

        mode = grader._detect_run_mode(sol, test_dir)
        assert mode == "function"

    def test_ast_detects_function_only(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("def solve(n):\n    return n * 2\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        mode = grader._detect_run_mode(sol, test_dir)
        assert mode == "function"

    def test_stdin_for_script(self, tmp_path: pathlib.Path) -> None:
        sol = tmp_path / "task1.py"
        sol.write_text("n = int(input())\nprint(n * 2)\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()

        mode = grader._detect_run_mode(sol, test_dir)
        assert mode == "stdin"


# ---------------------------------------------------------------------------
# find_all_solution_files
# ---------------------------------------------------------------------------


class TestFindAllSolutionFiles:
    def test_finds_valid_files(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "task1.py").write_text("")
        (tmp_path / "task1_2.py").write_text("")
        (tmp_path / "task_1.py").write_text("")
        (tmp_path / "README.md").write_text("")
        (tmp_path / "main.py").write_text("")

        found = grader.find_all_solution_files(str(tmp_path))
        names = [pathlib.Path(p).name for p in found]
        assert "task1.py" in names
        assert "task1_2.py" in names
        assert "task_1.py" in names
        assert "README.md" not in names
        assert "main.py" not in names

    def test_nested_dirs(self, tmp_path: pathlib.Path) -> None:
        sub = tmp_path / "module3"
        sub.mkdir()
        (sub / "task4_1.py").write_text("")
        (sub / "task4_2.py").write_text("")

        found = grader.find_all_solution_files(str(tmp_path))
        assert len(found) == 2
