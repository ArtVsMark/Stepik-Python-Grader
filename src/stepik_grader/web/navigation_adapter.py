"""navigation_adapter.py — инвентарь скачанных задач со статусами (issue #1179).

Слой между эндпоинтом ``GET /api/tasks/index`` и двумя источниками: деревом
задач на диске (``core/task_index``) и агрегатом прогресса из истории
(``core/history.task_progress``). Сам ничего не считает и в сеть не ходит.

**Статус берётся из истории, а не из файлов задачи.** По содержимому каталога
нельзя отличить «решено» от «лежит с момента скачивания»: `solution.py`
приезжает вместе с задачей, если у пользователя была принятая попытка на
платформе. История же знает, запускал ли он задачу здесь и получал ли ``AC``.

Кеш инвалидируется подписью дерева: скачали главу — список обновился без
перезапуска сервера. Явное «пересканировать» тоже есть — на случай, когда
подпись совпала (задачу заменили другой с тем же числом файлов и датой).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.history import read_task_progress
from stepik_grader.core.history_recording import default_history_db_path
from stepik_grader.core.task_index import TaskEntry, TaskNode, build_task_index, index_signature

__all__ = ["INDEX_SCHEMA", "read_task_tree"]

_log = get_logger("web")

#: Версия формата ответа — клиент, увидев чужое число, скажет «обновите».
INDEX_SCHEMA = 1

#: Статусы шага. «Не начата» — отсутствие записи в истории, а не отдельное
#: значение в базе: заводить его пришлось бы при скачивании, то есть писать
#: историю за пользователя, который ещё ничего не запускал.
_SOLVED = "solved"
_IN_PROGRESS = "in_progress"
_UNTOUCHED = "untouched"

_lock = threading.Lock()
_cache: dict[Path, tuple[tuple[int, float], list[dict[str, Any]]]] = {}


def read_task_tree(
    root: Path, *, db_path: Path | None = None, refresh: bool = False
) -> dict[str, Any]:
    """Дерево скачанных задач со статусами — готовым ответом API.

    Args:
        root: корень скачанных задач (уже сконфайненный вызывающим).
        db_path: база истории; ``None`` — тот же резолвер, что у CLI.
        refresh: пересобрать, даже если подпись дерева не менялась.

    Returns:
        ``{"kind": "index", "schema", "courses", "total"}``. Пустое дерево —
        не ошибка: у человека может не быть ни одной скачанной задачи, и
        интерфейс обязан показать это как «пока пусто», а не как сбой.
    """
    signature = index_signature(root)

    with _lock:
        cached = _cache.get(root)
        if not refresh and cached is not None and cached[0] == signature:
            courses = cached[1]
        else:
            # Статусы читаются здесь же, а не при отдаче: они меняются от
            # прогонов, а не от файлов, и подпись дерева их изменение не ловит.
            # Кеш при этом всё равно окупается — обход дерева дороже.
            courses = _render(build_task_index(root), _statuses(db_path))
            _cache[root] = (signature, courses)

    return {
        "kind": "index",
        "schema": INDEX_SCHEMA,
        "courses": courses,
        "total": sum(_count_tasks(course) for course in courses),
    }


def _statuses(db_path: Path | None) -> dict[str, str]:
    """Карта «ключ задачи -> статус» из агрегата прогресса.

    Best-effort: нет базы, история выключена, файл повреждён — пустая карта,
    и все шаги показываются как «не начата». Навигация обязана работать без
    истории: она про «где я в курсе», а не про «что я решил».

    ``sqlite3.DatabaseError`` ловится отдельно и не случайно: у базы более
    новой схемы (её открыли обновлённой версией, а потом откатились) чтение
    бросает ``SchemaTooNewError`` — потомка именно этого класса. Ронять из-за
    этого навигацию нельзя, но и молчать нельзя: причина уходит в лог.
    """
    path = db_path if db_path is not None else default_history_db_path()
    try:
        rows = read_task_progress(path)
    except (sqlite3.DatabaseError, OSError) as exc:
        _log.warning("статусы задач недоступны, показываю без них: %s", exc)
        return {}
    return {
        str(row["task_key"]): _SOLVED if row.get("first_ac_ts_utc") else _IN_PROGRESS
        for row in rows
        if row.get("task_key")
    }


def _render(courses: list[TaskNode], statuses: dict[str, str]) -> list[dict[str, Any]]:
    """Дерево в JSON-форму с проставленными статусами и счётчиками."""
    return [_render_node(course, statuses) for course in courses]


def _render_node(node: TaskNode, statuses: dict[str, str]) -> dict[str, Any]:
    children = [_render_node(child, statuses) for child in node.children]
    tasks = [_render_task(task, statuses) for task in node.tasks]
    solved = sum(child["solved"] for child in children) + sum(
        1 for task in tasks if task["status"] == _SOLVED
    )
    total = sum(child["total"] for child in children) + len(tasks)
    return {
        "id": node.node_id,
        "title": node.title,
        "children": children,
        "tasks": tasks,
        "solved": solved,
        "total": total,
    }


def _render_task(task: TaskEntry, statuses: dict[str, str]) -> dict[str, Any]:
    key = f"step:{task.step_id}" if task.step_id is not None else None
    return {
        "path": str(task.path),
        "title": task.title,
        "step_position": task.step_position,
        "status": statuses.get(key or "", _UNTOUCHED),
    }


def _count_tasks(course: dict[str, Any]) -> int:
    return int(course["total"])
