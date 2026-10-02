"""core/task_index.py — дерево скачанных задач для навигации (issue #1179).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Обходит корень скачанных задач и собирает **курс → секция → урок → шаг**, читая
иерархию из ``meta.json`` каждой задачи.

**Порядок берётся из иерархии, а не из файловой системы**, и это не
стилистика. Алфавит каталогов врёт дважды: ``task10`` встаёт перед ``task9``,
а папки к тому же переименовывают — тогда порядок ломается молча, и стрелка
«вперёд» уводит не туда. Числовые идентификаторы шага и урока такому не
подвержены.

Задачи без ``meta.json`` (папка с решением, собранная руками) в дерево не
попадают: у них нет ни курса, ни позиции шага, и приткнуть их можно только
выдумав место. Такая папка остаётся доступной через ввод пути — навигация её не
заменяет.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "TaskEntry",
    "TaskNode",
    "build_task_index",
    "index_signature",
]

_META_NAME = "meta.json"

#: Задачей считается каталог с ``meta.json``; в него же смотрит `statement_adapter`.
#: Глубина обхода не ограничивается: пользователь волен переложить дерево.
_SKIP_DIRS = frozenset({"tests", "tests.bak", "submissions", "__pycache__", ".git"})


@dataclass(frozen=True)
class TaskEntry:
    """Один шаг — лист дерева.

    Attributes:
        path: каталог задачи.
        step_id: идентификатор шага Stepik; ключ статуса в истории — ``step:<id>``.
        step_position: позиция шага в уроке — по ней шаги и упорядочены.
        title: заголовок шага; пустой, если его нет в ``meta.json``.
    """

    path: Path
    step_id: int | None
    step_position: int | None
    title: str


@dataclass
class TaskNode:
    """Узел дерева: курс, секция или урок.

    Attributes:
        node_id: идентификатор из ``meta.json`` — по нему и сортируются соседи.
        title: человеческое имя; пустое заменяется вызывающим на «без названия».
        children: вложенные узлы (у урока пусто).
        tasks: шаги — только у урока.
    """

    node_id: int | None
    title: str
    children: list[TaskNode] = field(default_factory=list)
    tasks: list[TaskEntry] = field(default_factory=list)


def index_signature(root: Path) -> tuple[int, float]:
    """Подпись дерева для инвалидации кеша: число задач и самый свежий ``meta.json``.

    Одного ``max(mtime)`` мало: скачали новую главу — файлы новые, максимум
    сдвинулся, всё хорошо; а вот **удаление** задачи максимум не двигает, и
    список продолжал бы показывать несуществующее. Число файлов ловит и это,
    и распаковку архива со старыми датами, не стоя ни одного лишнего обращения
    к ФС сверх уже сделанных (тот же довод, что в ``mtime_cache``, issue #996).
    """
    newest = 0.0
    count = 0
    for meta_path in _iter_meta_files(root):
        try:
            newest = max(newest, meta_path.stat().st_mtime)
        except OSError:
            continue
        count += 1
    return count, newest


def build_task_index(root: Path) -> list[TaskNode]:
    """Дерево скачанных задач: курсы → секции → уроки → шаги.

    Args:
        root: корень скачанных задач (обычно ``StepikTasks``).

    Returns:
        Курсы в порядке ``course_id``; пустой список, если корня нет или в нём
        нет ни одной задачи с ``meta.json``.

    Битый ``meta.json`` пропускается вместе со своей задачей: показать её всё
    равно негде — иерархии у неё нет. В сеть функция не ходит и работает только
    с тем, что уже на диске.
    """
    # Синтетический корень: три уровня строятся одним и тем же `_child`, без
    # отдельной ветки «а курс лежит не в children, а в словаре».
    root_node = TaskNode(node_id=None, title="")

    for meta_path in _iter_meta_files(root):
        meta = _read_meta(meta_path)
        if meta is None:
            continue

        course = _child(root_node, meta, "course_id", "course_title")
        section = _child(course, meta, "section_id", "section_title")
        lesson = _child(section, meta, "lesson_id", "lesson_title")

        lesson.tasks.append(
            TaskEntry(
                path=meta_path.parent,
                step_id=_as_int(meta.get("step_id")),
                step_position=_as_int(meta.get("step_position")),
                title=str(meta.get("step_title") or ""),
            )
        )

    _sort_node(root_node)
    return root_node.children


# ---------------------------------------------------------------------------
# Внутреннее
# ---------------------------------------------------------------------------


def _iter_meta_files(root: Path) -> list[Path]:
    """Все ``meta.json`` под корнем, кроме служебных каталогов.

    ``submissions/`` пропускается намеренно: там лежит СВОЙ ``meta.json`` с
    вердиктами отправок (issue #1055), и без фильтра каждая задача с историей
    попадала бы в дерево дважды.
    """
    if not root.is_dir():
        return []
    found: list[Path] = []
    for meta_path in root.rglob(_META_NAME):
        if any(part in _SKIP_DIRS for part in meta_path.relative_to(root).parts[:-1]):
            continue
        found.append(meta_path)
    return sorted(found)


def _read_meta(meta_path: Path) -> dict[str, Any] | None:
    """``meta.json`` как словарь; ``None`` — нечитаемый или не объект."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _as_int(value: Any) -> int | None:
    """Целое из ``meta.json``; ``None`` для пустого и нечислового.

    ``bool`` отсекается явно: он подкласс ``int``, и ``True`` молча стал бы
    идентификатором ``1``.
    """
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _child(parent: TaskNode, meta: dict[str, Any], id_field: str, title_field: str) -> TaskNode:
    """Дочерний узел по ``(id, title)``; создаётся при первой встрече.

    Ключ парный, а не по одному ``id``: у задачи без идентификатора он ``None``,
    и все такие узлы слиплись бы в один. Заголовок их различает.

    Поиск линейный намеренно: у курса горстка секций, у секции — горстка
    уроков. Словарь-индекс поверх списка пришлось бы синхронизировать с
    деревом, а выигрыша на таких размерах нет.
    """
    node_id = _as_int(meta.get(id_field))
    title = str(meta.get(title_field) or "")
    for child in parent.children:
        if child.node_id == node_id and child.title == title:
            return child
    child = TaskNode(node_id=node_id, title=title)
    parent.children.append(child)
    return child


def _node_sort_key(node: TaskNode) -> tuple[int, int, str]:
    """Узлы без идентификатора уезжают в конец, между собой — по названию."""
    if node.node_id is None:
        return (1, 0, node.title)
    return (0, node.node_id, node.title)


def _sort_node(node: TaskNode) -> None:
    """Упорядочить детей и шаги узла рекурсивно."""
    node.children.sort(key=_node_sort_key)
    node.tasks.sort(key=_task_sort_key)
    for child in node.children:
        _sort_node(child)


def _task_sort_key(task: TaskEntry) -> tuple[int, int, str]:
    """Шаги — по позиции в уроке; без позиции — в конец, по имени каталога."""
    if task.step_position is None:
        return (1, 0, task.path.name)
    return (0, task.step_position, task.path.name)
