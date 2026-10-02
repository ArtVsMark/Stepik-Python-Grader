"""statement_adapter.py — web-адаптер условия скачанной задачи (issue #1177).

Слой между эндпоинтом ``GET /api/task/statement`` и файлами в каталоге задачи.
Собирает три вещи и ничего не решает сам:

* ``header`` — курс → секция → урок → шаг из ``meta.json``, без обращений в сеть;
* ``html`` — тело условия, пропущенное через ``core/html_sanitizer``;
* ``attachments`` — что приехало рядом с задачей, а что нет.

**Имя файла условия сюда не приходит извне.** Вызывающий называет каталог
задачи, а какой файл внутри читать — решает этот модуль. Ограничение по
расширению (как у ``_get_source``, issue #811) было бы слабее: оно всё ещё
позволяет назвать чужой файл подходящего типа. Здесь такой возможности нет
вовсе.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.html_sanitizer import sanitize_statement_html

__all__ = [
    "STATEMENT_SCHEMA",
    "read_image",
    "read_statement",
]

_log = get_logger("web")

#: Версия формата ответа. Клиент, увидев чужое число, скажет «обновите», а не
#: молча покажет пустой экран.
STATEMENT_SCHEMA = 1

_RAW_NAME = "task.html"
_LEGACY_NAME = "task.md"
_META_NAME = "meta.json"

#: Заголовки шапки в порядке отображения: курс → секция → урок → шаг.
_HEADER_FIELDS = ("course_title", "section_title", "lesson_title", "step_title")

#: Что разрешено отдавать как картинку условия. SVG здесь НЕТ намеренно: он
#: умеет носить скрипт, а отдаётся со своего origin — это был бы XSS через
#: собственную ручку, обходящий всю очистку HTML.
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def read_statement(task_dir: Path) -> dict[str, Any]:
    """Условие задачи из каталога — готовым ответом API.

    Args:
        task_dir: каталог задачи (уже сконфайненный в workspace вызывающим).

    Returns:
        ``{"kind": "statement", "schema", "header", "html", "attachments"}``
        либо ``{"kind": "empty", ...}``, если условия нет. Пустая строка вместо
        явного «условия нет» неотличима на клиенте от «условие загрузилось и
        оказалось пустым», поэтому случаи разведены.
    """
    if not task_dir.is_dir():
        return {"kind": "empty", "schema": STATEMENT_SCHEMA, "reason": "not_a_task_dir"}

    meta = _read_meta(task_dir)
    raw = _read_raw(task_dir)
    if not raw.strip():
        return {
            "kind": "empty",
            "schema": STATEMENT_SCHEMA,
            "reason": "no_statement",
            "header": _header(meta),
        }

    return {
        "kind": "statement",
        "schema": STATEMENT_SCHEMA,
        "header": _header(meta),
        "html": sanitize_statement_html(raw, _local_files(task_dir, meta)),
        "attachments": _attachments(task_dir, meta),
    }


def _read_meta(task_dir: Path) -> dict[str, Any]:
    """``meta.json`` best-effort: битый или отсутствующий даёт пустую шапку.

    Условие важнее шапки: задача с испорченным ``meta.json`` всё равно должна
    показываться, пусть и без хлебных крошек.
    """
    try:
        data = json.loads((task_dir / _META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_raw(task_dir: Path) -> str:
    """Сырое тело условия: ``task.html``, а для старых задач — ``task.md``.

    До issue #1176 сырой HTML лежал именно в ``task.md``, и у скачанного раньше
    ``task.html`` просто нет. Откат на ``task.md`` делает такие задачи видимыми
    без перекачивания. Для задач нового формата откат не срабатывает никогда —
    ``task.html`` есть, — поэтому Markdown в санитайзер не попадает; а если бы
    попал, текст без тегов проходит очистку без изменений.
    """
    for name in (_RAW_NAME, _LEGACY_NAME):
        try:
            return (task_dir / name).read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def _header(meta: dict[str, Any]) -> dict[str, Any]:
    """Хлебные крошки из ``meta.json``; отсутствующее поле — пустая строка."""
    header: dict[str, Any] = {field: str(meta.get(field) or "") for field in _HEADER_FIELDS}
    position = meta.get("step_position")
    header["step_position"] = position if isinstance(position, int) else None
    return header


def _image_is_allowed(task_dir: Path, name: str) -> bool:
    """Разрешено ли отдавать ``name`` как картинку условия из ``task_dir``.

    Имя сверяется со **списком вложений из ``meta.json``**, а не используется
    как путь: иначе ручка отдачи картинок стала бы примитивом чтения любого
    файла внутри задачи — ровно тем, чем оказался ``_get_source`` в issue #811.
    Расширение проверяется отдельно и только картиночное: отдать `inline`
    что-нибудь вроде `.html` значило бы вернуть XSS через собственный origin.
    """
    if not name or name != Path(name).name or Path(name).suffix.lower() not in _IMAGE_TYPES:
        return False
    meta = _read_meta(task_dir)
    allowed = {
        str(item.get("name", ""))
        for item in _attachment_records(meta)
        if item.get("status") in {"saved", "exists"}
    }
    return name in allowed and (task_dir / name).is_file()


def read_image(task_dir: Path, name: str) -> tuple[bytes, str] | None:
    """Байты картинки-вложения и её MIME; ``None`` — отдавать нечего.

    Чтение живёт здесь, а не в хендлере, не для красоты: ``web/api_routes.py``
    по инварианту ARCH-07 (issue #830) не импортирует ядро напрямую и не
    добавляет собственной логики. Файловый ввод-вывод — как раз логика.
    """
    if not _image_is_allowed(task_dir, name):
        return None
    try:
        body = (task_dir / name).read_bytes()
    except OSError as exc:
        # Гонка между проверкой и чтением: файл удалили в этот зазор.
        _log.warning("вложение недоступно: %s (%s)", name, exc)
        return None
    return body, _IMAGE_TYPES[Path(name).suffix.lower()]


def _image_url(task_dir: Path, name: str) -> str:
    """Адрес картинки для ``src``.

    Голое имя файла сюда не годится: в браузере ``src="files.png"``
    разрешается относительно **корня сервера**, а не каталога задачи, и даёт
    404. Поэтому картинка адресуется собственной ручкой.
    """
    query = urlencode({"path": str(task_dir), "name": name})
    return f"/api/task/image?{query}"


def _local_files(task_dir: Path, meta: dict[str, Any]) -> dict[str, str]:
    """Карта «URL вложения -> чем заменить ``src``» — только по картинкам.

    Не приехавшее и не-картинки в карту не попадают, поэтому подставиться им
    не на что: обещать локальный файл, которого нет, хуже, чем не показать
    картинку.
    """
    return {
        str(item["url"]): _image_url(task_dir, str(item["name"]))
        for item in _attachment_records(meta)
        if item.get("url")
        and item.get("name")
        and item.get("status") in {"saved", "exists"}
        and Path(str(item["name"])).suffix.lower() in _IMAGE_TYPES
        and (task_dir / str(item["name"])).is_file()
    }


def _attachments(task_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Список вложений с отметкой «есть локально» и размером.

    Наличие проверяется по диску, а не только по ``meta.json``: запись говорит,
    что файл когда-то приехал, а вопрос экрана — лежит ли он там сейчас.
    """
    result: list[dict[str, Any]] = []
    for item in _attachment_records(meta):
        name = str(item.get("name", ""))
        target = task_dir / name if name else None
        present = bool(target and target.is_file())
        result.append(
            {
                "name": name,
                "present": present,
                "size": target.stat().st_size if present and target else None,
            }
        )
    return result


def _attachment_records(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Записи ``meta["attachments"]``, устойчиво к чужой форме файла."""
    records = meta.get("attachments")
    if not isinstance(records, list):
        return []
    return [item for item in records if isinstance(item, dict)]
