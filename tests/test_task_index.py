"""Дерево скачанных задач и его эндпоинт (issue #1179).

`core/task_index` — leaf на stdlib, зовётся напрямую. Проверяется главным
образом **порядок**: он берётся из иерархии `meta.json`, а не из файловой
системы. Алфавит каталогов врёт дважды — `task10` встаёт перед `task9`, и папки
переименовывают, — а числовые идентификаторы шага и урока такому не подвержены.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stepik_grader.core.task_index import build_task_index, index_signature
from stepik_grader.web.navigation_adapter import INDEX_SCHEMA, read_task_tree

_COURSE = {"course_id": 1, "course_title": "Поколение Python"}
_LESSON = {"section_id": 10, "section_title": "Списки", "lesson_id": 100, "lesson_title": "Урок 3"}


def _task(root: Path, folder: str, **meta: Any) -> Path:
    task = root / folder
    task.mkdir(parents=True, exist_ok=True)
    (task / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return task


def _steps(tree: list[Any]) -> list[int | None]:
    """Позиции шагов первого урока первого курса — в порядке дерева."""
    return [task.step_position for task in tree[0].children[0].children[0].tasks]


class TestOrderIgnoresTheFilesystem:
    def test_step_nine_before_step_ten(self, tmp_path: Path) -> None:
        """При сортировке по имени папки было бы 10, 2, 9."""
        _task(tmp_path, "a/task10", **_COURSE, **_LESSON, step_id=10, step_position=10)
        _task(tmp_path, "a/task2", **_COURSE, **_LESSON, step_id=2, step_position=2)
        _task(tmp_path, "a/task9", **_COURSE, **_LESSON, step_id=9, step_position=9)

        assert _steps(build_task_index(tmp_path)) == [2, 9, 10]

    def test_renamed_folder_does_not_change_the_order(self, tmp_path: Path) -> None:
        """Папки переименовывают — порядок обязан пережить это."""
        _task(tmp_path, "zzz", **_COURSE, **_LESSON, step_id=1, step_position=1)
        _task(tmp_path, "aaa", **_COURSE, **_LESSON, step_id=2, step_position=2)

        assert _steps(build_task_index(tmp_path)) == [1, 2]

    def test_courses_ordered_by_id_not_by_path(self, tmp_path: Path) -> None:
        _task(
            tmp_path, "a", course_id=5, course_title="Пятый", **_LESSON, step_id=1, step_position=1
        )
        _task(
            tmp_path, "z", course_id=1, course_title="Первый", **_LESSON, step_id=2, step_position=1
        )
        tree = build_task_index(tmp_path)

        assert [course.node_id for course in tree] == [1, 5]

    def test_step_without_position_goes_last(self, tmp_path: Path) -> None:
        """Иначе задача без позиции встала бы в начало и сдвинула счётчик."""
        _task(tmp_path, "a/no-pos", **_COURSE, **_LESSON, step_id=9)
        _task(tmp_path, "a/first", **_COURSE, **_LESSON, step_id=1, step_position=1)

        assert _steps(build_task_index(tmp_path)) == [1, None]


class TestRobustness:
    def test_missing_root_gives_an_empty_tree(self, tmp_path: Path) -> None:
        assert build_task_index(tmp_path / "нет") == []

    def test_broken_meta_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Показать такую задачу негде — иерархии у неё нет."""
        _task(tmp_path, "ok", **_COURSE, **_LESSON, step_id=1, step_position=1)
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "meta.json").write_text("{сломано", encoding="utf-8")

        assert len(build_task_index(tmp_path)) == 1

    def test_submissions_meta_does_not_create_a_second_task(self, tmp_path: Path) -> None:
        """В `submissions/` лежит СВОЙ meta.json с вердиктами отправок (#1055)."""
        task = _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        archive = task / "submissions"
        archive.mkdir()
        (archive / "meta.json").write_text('{"x": 1}', encoding="utf-8")

        assert len(_steps(build_task_index(tmp_path))) == 1

    def test_boolean_id_is_not_taken_for_a_number(self, tmp_path: Path) -> None:
        """`bool` — подкласс `int`: `True` молча стал бы идентификатором 1."""
        _task(
            tmp_path,
            "a",
            course_id=True,
            course_title="Странный",
            **_LESSON,
            step_id=1,
            step_position=1,
        )

        assert build_task_index(tmp_path)[0].node_id is None


class TestSignature:
    def test_new_task_changes_the_signature(self, tmp_path: Path) -> None:
        _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        before = index_signature(tmp_path)
        _task(tmp_path, "b", **_COURSE, **_LESSON, step_id=2, step_position=2)

        assert index_signature(tmp_path) != before

    def test_deleted_task_changes_the_signature(self, tmp_path: Path) -> None:
        """Удаление не двигает max(mtime) — счёт файлов ловит именно его."""
        _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        second = _task(tmp_path, "b", **_COURSE, **_LESSON, step_id=2, step_position=2)
        before = index_signature(tmp_path)
        (second / "meta.json").unlink()

        assert index_signature(tmp_path) != before

    def test_missing_root_has_a_stable_signature(self, tmp_path: Path) -> None:
        assert index_signature(tmp_path / "нет") == (0, 0.0)


class TestAdapter:
    def test_reports_schema_and_total(self, tmp_path: Path) -> None:
        _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        data = read_task_tree(tmp_path, db_path=tmp_path / "нет.db", refresh=True)

        assert data["kind"] == "index"
        assert data["schema"] == INDEX_SCHEMA
        assert data["total"] == 1

    def test_without_history_everything_is_untouched(self, tmp_path: Path) -> None:
        """Навигация про «где я в курсе», а не про «что я решил» — работает без базы."""
        _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        data = read_task_tree(tmp_path, db_path=tmp_path / "нет.db", refresh=True)
        task = data["courses"][0]["children"][0]["children"][0]["tasks"][0]

        assert task["status"] == "untouched"

    def test_counts_roll_up_the_tree(self, tmp_path: Path) -> None:
        _task(tmp_path, "a", **_COURSE, **_LESSON, step_id=1, step_position=1)
        _task(tmp_path, "b", **_COURSE, **_LESSON, step_id=2, step_position=2)
        course = read_task_tree(tmp_path, db_path=tmp_path / "нет.db", refresh=True)["courses"][0]

        assert course["total"] == 2
        assert course["solved"] == 0

    def test_empty_tree_is_not_an_error(self, tmp_path: Path) -> None:
        """У человека может не быть ни одной скачанной задачи."""
        data = read_task_tree(tmp_path, db_path=tmp_path / "нет.db", refresh=True)

        assert data["kind"] == "index"
        assert data["courses"] == []


class TestLeafInvariant:
    @pytest.mark.parametrize("forbidden", ["from stepik_grader", "import stepik_grader"])
    def test_scanner_imports_nothing_from_the_project(self, forbidden: str) -> None:
        """Сканер — leaf: разбирает диск в структуры, без project-зависимостей."""
        source = (Path(__file__).parent.parent / "src/stepik_grader/core/task_index.py").read_text(
            encoding="utf-8"
        )

        assert forbidden not in source
