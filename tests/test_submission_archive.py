"""Тесты раскладки истории отправок (issue #1055).

Проверяют ровно то, из-за чего корпус реальных решений был неполон: история не
теряется при перекачке шага, сохраняется целиком (а не последняя отправка) и
остаётся невидимой для поиска решений — иначе старые попытки пришли бы в
режимы 2–4 как конкуренты.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from stepik_grader.core.submission_archive import (
    ARCHIVE_META_NAME,
    SUBMISSIONS_DIR_NAME,
    archive_dir_for,
    save_submission_history,
    submission_file_name,
)
from stepik_grader.core.test_loader import find_all_solution_files, is_solution_file


def _submission(sub_id: int, status: str, time: str, code: str, hint: str = "") -> dict[str, Any]:
    """Объект отправки в форме, в которой его отдаёт /api/submissions."""
    return {
        "id": sub_id,
        "status": status,
        "time": time,
        "hint": hint,
        "reply": {"code": code},
    }


def _read_meta(task_dir: pathlib.Path) -> list[dict[str, Any]]:
    meta_path = archive_dir_for(task_dir) / ARCHIVE_META_NAME
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = payload["submissions"]
    return entries


class TestSubmissionFileName:
    """Имя файла отправки: читаемое, безопасное и уникальное."""

    def test_time_status_and_id(self):
        name = submission_file_name(_submission(1234567, "wrong", "2024-03-01T12:00:05Z", "x"))
        assert name == "2024-03-01T12-00-05_wrong_1234567.py"

    def test_no_colons_left(self):
        """Двоеточие из ISO-времени недопустимо в имени файла на Windows."""
        assert ":" not in submission_file_name(
            _submission(1, "correct", "2024-03-01T12:00:05Z", "x")
        )

    def test_missing_fields_fall_back(self):
        name = submission_file_name({"id": 42, "reply": {"code": "x"}})
        assert name == "unknown-time_unknown_42.py"

    def test_same_second_does_not_collide(self):
        """Две отправки в одну секунду — разные файлы: иначе вторая затрёт первую."""
        first = submission_file_name(_submission(1, "wrong", "2024-03-01T12:00:05Z", "a"))
        second = submission_file_name(_submission(2, "wrong", "2024-03-01T12:00:05Z", "b"))
        assert first != second

    def test_not_a_solution_file_name(self):
        """Маска решений не должна видеть историю (иначе — сорок конкурентов)."""
        name = submission_file_name(_submission(1, "wrong", "2024-03-01T12:00:05Z", "x"))
        assert not is_solution_file(name)


class TestSaveSubmissionHistory:
    """save_submission_history ничего не затирает и сохраняет всё."""

    def test_saves_every_submission_not_just_latest(self, tmp_path: pathlib.Path):
        history = [
            _submission(3, "correct", "2024-03-01T12:05:11Z", "print(3)"),
            _submission(2, "wrong", "2024-03-01T12:02:00Z", "print(2)"),
            _submission(1, "wrong", "2024-03-01T12:00:05Z", "print(1)"),
        ]

        saved = save_submission_history(tmp_path, history)

        files = sorted(p.name for p in archive_dir_for(tmp_path).glob("*.py"))
        assert len(files) == 3
        assert {entry.submission_id for entry in saved} == {1, 2, 3}
        codes = {p.read_text(encoding="utf-8") for p in archive_dir_for(tmp_path).glob("*.py")}
        assert codes == {"print(1)", "print(2)", "print(3)"}

    def test_meta_keeps_verdict_and_hint(self, tmp_path: pathlib.Path):
        """Вердикт платформы и подсказка — то, ради чего история и собирается."""
        save_submission_history(
            tmp_path,
            [_submission(7, "wrong", "2024-03-01T12:00:05Z", "print(1)", hint="Тест 3 не пройден")],
        )

        entries = _read_meta(tmp_path)
        assert entries == [
            {
                "submission_id": 7,
                "status": "wrong",
                "time": "2024-03-01T12:00:05Z",
                "file": "2024-03-01T12-00-05_wrong_7.py",
                "hint": "Тест 3 не пройден",
            }
        ]

    def test_redownload_does_not_erase_earlier_submissions(self, tmp_path: pathlib.Path):
        """Перекачка шага, где API отдал только свежую отправку, не теряет прошлые."""
        save_submission_history(
            tmp_path,
            [
                _submission(2, "correct", "2024-03-01T12:02:00Z", "print(2)"),
                _submission(1, "wrong", "2024-03-01T12:00:05Z", "print(1)"),
            ],
        )

        save_submission_history(
            tmp_path, [_submission(3, "correct", "2024-04-01T09:00:00Z", "print(3)")]
        )

        files = sorted(p.name for p in archive_dir_for(tmp_path).glob("*.py"))
        assert files == [
            "2024-03-01T12-00-05_wrong_1.py",
            "2024-03-01T12-02-00_correct_2.py",
            "2024-04-01T09-00-00_correct_3.py",
        ]
        assert [entry["submission_id"] for entry in _read_meta(tmp_path)] == [1, 2, 3]

    def test_existing_file_is_not_rewritten(self, tmp_path: pathlib.Path):
        """Уже сохранённый код не переписывается: файл на диске — источник истины."""
        submission = _submission(1, "wrong", "2024-03-01T12:00:05Z", "print(1)")
        save_submission_history(tmp_path, [submission])
        code_path = archive_dir_for(tmp_path) / submission_file_name(submission)
        code_path.write_text("правка руками", encoding="utf-8")

        save_submission_history(tmp_path, [submission])

        assert code_path.read_text(encoding="utf-8") == "правка руками"

    def test_submissions_without_code_are_skipped(self, tmp_path: pathlib.Path):
        """Отправка без кода сохранять нечего — пустых файлов не плодим."""
        save_submission_history(
            tmp_path,
            [
                {"id": 1, "status": "wrong", "time": "2024-03-01T12:00:05Z", "reply": {}},
                _submission(2, "correct", "2024-03-01T12:02:00Z", "print(2)"),
            ],
        )

        assert [p.name for p in archive_dir_for(tmp_path).glob("*.py")] == [
            "2024-03-01T12-02-00_correct_2.py"
        ]

    def test_empty_history_creates_nothing(self, tmp_path: pathlib.Path):
        """Нет отправок — нет и каталога: пустая папка в каждой задаче никому не нужна."""
        assert save_submission_history(tmp_path, []) == []
        assert not archive_dir_for(tmp_path).exists()

    def test_corrupt_meta_does_not_break_saving(self, tmp_path: pathlib.Path):
        """Битый meta.json — потеря индекса, но не повод уронить скачивание."""
        archive_dir_for(tmp_path).mkdir(parents=True)
        (archive_dir_for(tmp_path) / ARCHIVE_META_NAME).write_text("{не json", encoding="utf-8")

        saved = save_submission_history(
            tmp_path, [_submission(1, "wrong", "2024-03-01T12:00:05Z", "print(1)")]
        )

        assert [entry.submission_id for entry in saved] == [1]
        assert _read_meta(tmp_path)[0]["submission_id"] == 1


class TestArchiveIsInvisibleToSolutionSearch:
    """Файлы истории не должны становиться конкурентами в режимах 2–4."""

    def test_find_all_solution_files_ignores_archive(self, tmp_path: pathlib.Path):
        (tmp_path / "task5_1.py").write_text("print(1)", encoding="utf-8")
        save_submission_history(
            tmp_path,
            [
                _submission(1, "wrong", "2024-03-01T12:00:05Z", "print(1)"),
                _submission(2, "correct", "2024-03-01T12:02:00Z", "print(2)"),
            ],
        )

        found = find_all_solution_files(tmp_path)

        assert [p.name for p in found] == ["task5_1.py"]
        assert all(SUBMISSIONS_DIR_NAME not in p.parts for p in found)
