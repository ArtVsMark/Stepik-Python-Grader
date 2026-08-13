"""Машинный вывод CLI: статус прогона в json/csv (issue #997, MTX-4-04)."""

from __future__ import annotations

import json
import pathlib

from stepik_grader import cli

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
