"""В режиме 1 результат подписан выбранным файлом, а не временным (issue #1211).

Режим 1 намеренно НЕ перезаписывает целевой файл перед проверкой (#297): код из
редактора уходит в теле запроса и исполняется из временного файла рядом с
задачей — так убрана гонка `save → grade` между двумя окнами. Но подпись в
результатах бралась от исполнявшегося файла, и пользователь видел `tmpXXXXXX.py`
вместо выбранного решения.

Это не косметика. Подпись — единственное, чем человек убеждается, что проверено
именно то, что он выбрал; в сравнении решений строки различают по ней же, а
скриншот с `tmpXXXXXX.py`, отправленный ментору, выглядит как сбой инструмента.
Хуже того, временное имя уезжало в историю (`solution_name`), то есть портило
не только экран, но и данные.

Реальный путь исполнения при этом не подменяется — он нужен диагностике.
"""

from __future__ import annotations

import json
import pathlib

from stepik_grader.web import runs, viewmodels
from tests._wait import wait_until


def _make_task(folder: pathlib.Path, code: str, *, name: str = "task.py") -> pathlib.Path:
    """Задача на диске: решение плюс один тест-кейс рядом."""
    tests = folder / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "input_1.txt").write_text("4\n", encoding="utf-8")
    (tests / "expected_1.txt").write_text("5\n", encoding="utf-8")
    solution = folder / name
    solution.write_text(code, encoding="utf-8")
    return solution


def _poll_until_terminal(job_id: str, *, timeout: float = 15.0) -> dict:
    """Дождаться терминального статуса джобы (зеркалит tests/test_runs.py)."""

    def _terminal() -> dict | None:
        job = runs.get_job(job_id)
        assert job is not None
        data = job.to_status_dict()
        return data if data["status"] in ("done", "error", "cancelled") else None

    data = wait_until(_terminal, timeout=timeout)
    if data is None:
        raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout}s")
    return data


class TestChosenFileWins:
    """Подпись отвечает на вопрос «что я выбрал», а не «что исполнялось»."""

    def test_rows_carry_the_chosen_name(self, tmp_path: pathlib.Path) -> None:
        sol = _make_task(tmp_path, "print(999)\n", name="my_solution.py")

        job = runs.submit_job("tests", sol, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert data["result"]["rows"][0]["file"] == "my_solution.py"

    def test_temp_name_appears_nowhere_in_the_rows(self, tmp_path: pathlib.Path) -> None:
        """Ни в таблице, ни в разборе — во всём, что уедет на экран.

        Проверяются именно ``rows``, а не весь ответ. Соседнее поле ``base`` —
        настоящий путь задачи, и фикс его намеренно НЕ трогает (диагностике
        нужен реальный путь). Прежняя редакция теста искала подстроку во всей
        сериализации и на Linux падала всегда: там ``base`` лежит внутри
        ``/tmp/pytest-…``. На Windows тот же путь пишется как
        ``…\\AppData\\Local\\Temp\\…`` — с заглавной, поэтому локально тест
        проходил, а три ОС в CI краснели.
        """
        sol = _make_task(tmp_path, "print(999)\n")

        job = runs.submit_job("tests", sol, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert "tmp" not in json.dumps(data["result"]["rows"], ensure_ascii=False).lower()

    def test_row_without_tests_is_named_too(self, tmp_path: pathlib.Path) -> None:
        """Ветка «NO TESTS» — отдельная строка кода, её легко забыть.

        Именно так таблица и разбор разъезжались бы между собой: подпись
        подменена в одном месте и не подменена в соседнем.
        """
        solution = tmp_path / "my_solution.py"
        solution.write_text("print(1)\n", encoding="utf-8")

        job = runs.submit_job("tests", solution, {"lang": "ru"}, code="print(1)\n")
        data = _poll_until_terminal(job.id)

        row = data["result"]["rows"][0]
        assert row["status"] == "NO TESTS"
        assert row["file"] == "my_solution.py"

    def test_folder_without_chosen_file_is_named_by_folder(self, tmp_path: pathlib.Path) -> None:
        """Код из редактора без выбранного файла подписан папкой задачи.

        Проверяется именно имя папки, а не «отсутствие ``tmp``»: относительный
        путь папки к самой себе — ``"."``, и такая подпись формально проходит
        проверку на временное имя, ничего при этом не сообщая.
        """
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "input_1.txt").write_text("4\n", encoding="utf-8")
        (tests / "expected_1.txt").write_text("5\n", encoding="utf-8")

        job = runs.submit_job("tests", tmp_path, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert data["result"]["rows"][0]["file"] == tmp_path.name


class TestHistory:
    """Временное имя портило не только экран, но и записанные данные."""

    def test_history_records_the_chosen_name(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        recorded: dict[str, object] = {}

        def spy(*args: object, **kwargs: object) -> None:
            recorded.update(kwargs)

        monkeypatch.setattr(viewmodels, "_record_history_if_enabled", spy)
        monkeypatch.setattr(viewmodels, "_history_enabled", lambda: True)
        sol = _make_task(tmp_path, "print(int(input()) + 1)\n", name="my_solution.py")
        temp = tmp_path / "tmpZZZZ.py"
        temp.write_text("print(int(input()) + 1)\n", encoding="utf-8")

        viewmodels.grade_path(temp, display_path=sol)

        assert recorded["solution_name"] == "my_solution.py"


class TestDiagnosticsKeepsTheRealPath:
    """Подменяется подпись, а не путь исполнения (#1211, non-goal)."""

    def test_real_file_is_the_one_executed(self, tmp_path: pathlib.Path) -> None:
        """На диске неверное решение, в теле верное — вердикт от тела."""
        sol = _make_task(tmp_path, "print(999)\n")
        original = sol.read_text(encoding="utf-8")

        job = runs.submit_job("tests", sol, {"lang": "ru"}, code="print(int(input()) + 1)\n")
        data = _poll_until_terminal(job.id)

        assert data["result"]["rows"][0]["status"] == "OK"
        assert sol.read_text(encoding="utf-8") == original
