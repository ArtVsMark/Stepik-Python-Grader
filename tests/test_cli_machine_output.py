"""Машинный вывод CLI: статус прогона в json/csv (issue #997, MTX-4-04)."""

from __future__ import annotations

import json
import pathlib

from stepik_grader import cli
from stepik_grader.cli import commands

# ---------------------------------------------------------------------------
# Машинный вывод несёт статус прогона — issue #997 (MTX-4-04)
# ---------------------------------------------------------------------------


class TestMachineOutputCarriesStatus:
    """«Ноль кейсов» не выдаётся за прогон без провалов."""

    def _solution_without_tests(self, tmp_path: pathlib.Path) -> pathlib.Path:
        task = tmp_path / "task1"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text('print("wrong")\n', encoding="utf-8")
        return task / "task1_1.py"

    def _solution_with_failing_test(self, tmp_path: pathlib.Path) -> pathlib.Path:
        task = tmp_path / "task2"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text('print("wrong")\n', encoding="utf-8")
        (task / "tests" / "1").write_text("5", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("10", encoding="utf-8")
        return task / "task1_1.py"

    def test_json_says_no_tests_instead_of_silent_success(self, tmp_path, monkeypatch, capsys):
        """MTX-4-04: total=0, failed=0 читалось потребителем как успех."""
        solution = self._solution_without_tests(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(solution), "--output", "json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "no_tests"

    def test_json_status_matches_failure(self, tmp_path, monkeypatch, capsys):
        """Провал называется провалом — статус не расходится с кодом возврата."""
        solution = self._solution_with_failing_test(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(solution), "--output", "json"])

        assert json.loads(capsys.readouterr().out)["status"] == "fail"

    def test_batch_json_has_status_per_solution_and_overall(self, tmp_path, monkeypatch, capsys):
        """Режим 2: статус и у пачки, и у каждого решения."""
        self._solution_without_tests(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "2", "--dir", ".", "--output", "json"])

        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "no_tests"
        assert all(row["status"] == "no_tests" for row in payload["results"].values())

    def test_csv_is_not_an_empty_table(self, tmp_path, monkeypatch, capsys):
        """Пустая таблица читалась как «всё хорошо» — теперь есть строка статуса."""
        solution = self._solution_without_tests(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(solution), "--output", "csv"])

        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert len(lines) >= 2, "в CSV только заголовок — потребитель увидит успех"
        assert "NO_TESTS" in lines[-1]


# ---------------------------------------------------------------------------
# Пометки машинного вывода — issue #997 (SBX-5-04, JRN-2-03)
# ---------------------------------------------------------------------------


class TestMachineLabels:
    """schema/mode/isolation рядом с результатом во ВСЕХ режимах.

    Дополняет `status` (MTX-4-04, #1082): тот покрыл режимы 1/2, здесь —
    режимы замера, ранние выходы и сами пометки.
    """

    def _task(self, tmp_path: pathlib.Path) -> pathlib.Path:
        task = tmp_path / "task1"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text("print(int(input()) * 2)\n", encoding="utf-8")
        (task / "tests" / "1").write_text("5", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("10", encoding="utf-8")
        return task

    def _json(self, capsys) -> dict:
        return json.loads(capsys.readouterr().out)

    def test_every_mode_names_itself(self, tmp_path, monkeypatch, capsys):
        """JRN-2-03: четыре формы результата без признака, по которому их различить."""
        task = self._task(tmp_path)
        monkeypatch.chdir(tmp_path)
        expected = {1: "grade", 2: "batch", 3: "benchmark", 4: "microbench"}

        for mode, name in expected.items():
            target = ["--file", str(task / "task1_1.py")] if mode == 1 else ["--dir", str(task)]
            cli.main(["--mode", str(mode), *target, "--output", "json"])
            payload = self._json(capsys)
            assert payload["mode"] == name, f"режим {mode} не назвал себя"
            assert payload["schema"] == commands.JSON_SCHEMA_VERSION
            assert payload["isolation"] == "none"

    def test_early_exit_is_labelled_without_mode(self, tmp_path, monkeypatch, capsys):
        """Ранний выход помечен, но без mode: режим до отказа не начинался."""
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "2", "--dir", str(tmp_path / "nope"), "--output", "json"])

        payload = self._json(capsys)
        assert payload["status"] == "no_tests"
        assert payload["isolation"] == "none"
        assert "mode" not in payload

    def test_benchmark_without_solutions_is_not_ok(self, tmp_path, monkeypatch, capsys):
        """Пустой замер — не успех: сравнивать было нечего."""
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "3", "--dir", str(empty), "--output", "json"])

        assert self._json(capsys)["status"] == "no_tests"

    def test_stats_line_records_isolation(self, tmp_path, monkeypatch, capsys):
        """SBX-5-04: по строке статистики не отличить прогон под --sandbox."""
        task = self._task(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--stats"])
        capsys.readouterr()

        line = json.loads(
            (tmp_path / ".grader_stats.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        )
        assert line["isolation"] == "none"

    def test_isolation_label_follows_sandbox_backend(self, monkeypatch):
        """Под песочницей метка — имя backend'а, а не «none».

        Подменяется активный runner, а не место чтения паспорта: значение
        считает общий хелпер (``history_recording.current_isolation``, issue
        #1220), и тест не должен знать, из какого модуля его позвали.
        """
        from stepik_grader.core import runner

        class _FakeSandbox:
            backend_name = "bubblewrap"

        monkeypatch.setattr(runner, "_RUNNER", _FakeSandbox())

        assert commands._isolation_label() == "bubblewrap"


# ---------------------------------------------------------------------------
# Табличный вывод не исполняет управляющие последовательности — issue #981
# ---------------------------------------------------------------------------


class TestTabularOutputEscaping:
    """csv/markdown печатаются в терминал, значит подчиняются тому же правилу.

    Подробный отчёт экранировали ещё в первом заходе по #981, а табличные
    форматы — нет: `error` там это stderr решения, и ANSI из упавшего решения
    перерисовывал таблицу поверх напечатанного. JSON в это множество не входит —
    `json.dumps` экранирует управляющие символы сам.
    """

    def _task_with_forging_solution(self, tmp_path: pathlib.Path) -> pathlib.Path:
        task = tmp_path / "task1"
        (task / "tests").mkdir(parents=True)
        (task / "task1_1.py").write_text(
            "import sys\n"
            'sys.stderr.write("\\x1b[2J\\x1b[H\\x1b[32m  task1.py  1/1  OK\\x1b[0m")\n'
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        (task / "tests" / "1").write_text("5", encoding="utf-8")
        (task / "tests" / "1.clue").write_text("10", encoding="utf-8")
        return task

    def test_csv_does_not_carry_raw_escape(self, tmp_path, monkeypatch, capsys):
        """Решение падает с ANSI в stderr — в CSV не должно быть сырых ESC."""
        task = self._task_with_forging_solution(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--output", "csv"])

        out = capsys.readouterr().out
        assert "\x1b" not in out, "сырая ESC из stderr решения дошла до терминала"
        assert "\\x1b[2J" in out, "последовательность должна быть видна как текст"

    def test_markdown_does_not_carry_raw_escape(self, tmp_path, monkeypatch, capsys):
        """То же для markdown — формат другой, канал тот же."""
        task = self._task_with_forging_solution(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--output", "markdown"])

        assert "\x1b" not in capsys.readouterr().out

    def test_json_keeps_original_text(self, tmp_path, monkeypatch, capsys):
        """JSON остаётся машинным: экранирование делает сам json.dumps."""
        task = self._task_with_forging_solution(tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.main(["--mode", "1", "--file", str(task / "task1_1.py"), "--output", "json"])

        raw = capsys.readouterr().out
        assert "\x1b" not in raw, "в тексте JSON сырых ESC быть не должно"
        assert "\\u001b" in raw, "json.dumps экранирует управляющие символы сам"
