"""atomic_io.py — атомарная запись JSON (общий leaf-хелпер, issue #551, ADR-0011).

Архитектурный слой: Infrastructure / Utilities (top-level shared-leaf).

Единый атомарный JSON-писатель для потребителей во ВСЁМ пакете — включая
подпакеты (``glossary/``/``rules/``), которые по архитектурному инварианту НЕ
импортируют ``core/``. Поэтому модуль живёт на верхнем уровне ``stepik_grader``
(не в ``core/``, решение зафиксировано в ADR-0011): ребро ``glossary → core`` не
возникает, а общий писатель всё равно доступен и core-модулям
(``user_settings``), и подпакетам. Как ``core/storage.py``, ничего из проекта не
импортирует (stdlib-only, leaf).

Пишет во временный файл в ТОЙ ЖЕ директории и заменяет цель через ``os.replace``
(атомарный rename на POSIX и Windows) — прерывание/краш/конкурентная запись не
оставляют усечённый файл: читатель видит либо старую, либо новую полную версию
(прежний ``open("w")`` сначала обрезал целевой файл, обрыв рвал backlog #363).
``mkstemp`` даёт уникальное имя (параллельные писатели не делят temp) и права
0600 на время записи. Temp рядом с целью — чтобы ``replace`` шёл в пределах одной
ФС (rename между ФС не атомарен).
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
from typing import Any

__all__ = ["atomic_write_json"]


def atomic_write_json(path: pathlib.Path, data: Any, *, fsync: bool = True) -> None:
    """Атомарно записать ``data`` как JSON в ``path`` (создавая директории).

    ``data`` — любое JSON-сериализуемое значение (dict/list/...). ``fsync=True``
    (по умолчанию) форсит запись на диск до ``replace`` — durability ценой одного
    fsync; ``fsync=False`` для часто пишущихся не критичных файлов (настройки),
    где достаточно атомарности замены без гарантии сохранности при сбое питания.

    Raises:
        OSError: при сбое записи/``replace`` (temp best-effort убирается, цель не
            тронута); вызывающая сторона решает, глушить ли (best-effort-писатели
            вроде ``save_missing_queue`` оборачивают вызов в ``try/except``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # mkstemp в той же директории → уникальное имя (параллельные писатели не
    # делят temp) и приватные права 0600 на время записи.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        tmp_path.replace(path)
    except OSError:
        # best-effort уборка temp при сбое записи/replace — цель не тронута.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
