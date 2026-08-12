"""submission_archive.py — история своих отправок рядом с задачей (issue #1055).

Архитектурный слой: Application (раскладка на диск, Stepik-специфика).

Зачем отдельный каталог. ``solution.py`` хранит ровно последнюю отправку и
перезаписывается при каждой перекачке шага — прошлые попытки, включая неверные,
исчезали. Именно они и нужны корпусу: у каждой есть вердикт платформы, то есть
эталон, с которым сверяется наш собственный вердикт.

Раскладка::

    <задача>/
      task05_1.py                 ← рабочее решение, защищено #554
      solution.py                 ← последняя отправка (поведение сохранено)
      submissions/
        2024-03-01T12-00-05_wrong_1234567.py
        2024-03-01T12-05-11_correct_1234890.py
        meta.json                 ← вердикт, hint и время каждой

Имена намеренно НЕ подпадают под маску решений
``core/test_loader._SOLUTION_FILE_RE``: поиск решений рекурсивен, и файл вида
``task05_3.py`` в этом каталоге пришёл бы в режимы 2–4 как ещё один конкурент —
сорок старых попыток вместо трёх решений. Имя начинается со времени, а не с
``task``, поэтому маска его не видит.

Идентификатор отправки в имени — не украшение: две отправки могут прийтись на
одну секунду, и без него вторая затёрла бы первую, то есть ровно та потеря
данных, ради которой каталог и заводится.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Any

from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.step_content import extract_submission_code
from stepik_grader.core.storage import load_json_file, save_json_file

__all__ = [
    "ARCHIVE_META_NAME",
    "SUBMISSIONS_DIR_NAME",
    "ArchivedSubmission",
    "archive_dir_for",
    "save_submission_history",
    "submission_file_name",
]

_log = get_logger("submission_archive")

SUBMISSIONS_DIR_NAME = "submissions"
ARCHIVE_META_NAME = "meta.json"

# Всё, что не буква/цифра/дефис/подчёркивание, в имени файла заменяется: время
# приходит как ISO (`2024-03-01T12:00:05Z`), а двоеточие на Windows недопустимо.
_UNSAFE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_-]")
_UNKNOWN_TIME = "unknown-time"
_UNKNOWN_STATUS = "unknown"


@dataclass(frozen=True)
class ArchivedSubmission:
    """Одна отправка, сохранённая на диск: файл с кодом плюс вердикт платформы."""

    submission_id: int
    status: str
    time: str
    file_name: str
    hint: str

    def as_meta(self) -> dict[str, Any]:
        """Запись для ``submissions/meta.json``."""
        return {
            "submission_id": self.submission_id,
            "status": self.status,
            "time": self.time,
            "file": self.file_name,
            "hint": self.hint,
        }


def archive_dir_for(task_dir: pathlib.Path) -> pathlib.Path:
    """Каталог истории отправок внутри директории задачи."""
    return task_dir / SUBMISSIONS_DIR_NAME


def _sanitize(value: str, fallback: str) -> str:
    """Приводит кусок имени файла к безопасному виду, пустое — к ``fallback``."""
    cleaned = _UNSAFE_NAME_CHARS.sub("-", value).strip("-")
    return cleaned or fallback


def submission_file_name(submission: dict[str, Any]) -> str:
    """Возвращает имя файла для отправки: ``<время>_<вердикт>_<id>.py``.

    Время берётся из поля ``time`` и режется до секунд: дробная часть и зона
    ничего не различают, а имя удлиняют. Отсутствующие поля не мешают —
    подставляются заглушки, идентификатор всё равно делает имя уникальным.
    """
    raw_time = str(submission.get("time") or "")[:19]
    stamp = _sanitize(raw_time, _UNKNOWN_TIME)
    status = _sanitize(str(submission.get("status") or ""), _UNKNOWN_STATUS)
    submission_id = int(submission.get("id") or 0)
    return f"{stamp}_{status}_{submission_id}.py"


def _load_existing_meta(meta_path: pathlib.Path) -> dict[int, dict[str, Any]]:
    """Читает уже сохранённые записи истории, ключ — id отправки.

    Испорченный или чужой по форме файл не должен ронять скачивание: записи без
    идентификатора просто игнорируются, а прежние файлы с кодом на диске
    остаются нетронутыми в любом случае.
    """
    if not meta_path.exists():
        return {}
    try:
        raw = load_json_file(meta_path)
    except (OSError, ValueError) as exc:
        # ValueError покрывает и JSONDecodeError, и «корень не объект».
        _log.warning("история отправок: %s нечитаем (%s), прежние записи пропущены", meta_path, exc)
        return {}
    entries = raw.get("submissions") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return {}
    known: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            known[int(entry["submission_id"])] = entry
        except (TypeError, ValueError):
            continue
    return known


def save_submission_history(
    task_dir: pathlib.Path,
    submissions: list[dict[str, Any]],
) -> list[ArchivedSubmission]:
    """Раскладывает историю отправок в ``<task_dir>/submissions/``.

    Ничего не затирает: файл с кодом пишется только если его ещё нет, а записи
    прошлых перекачек остаются в ``meta.json``, даже когда API их больше не
    отдал. Отправки без кода (пустой ``reply.code``) пропускаются — сохранять
    нечего.

    Пустая история каталог не создаёт. Возвращает список сохранённых записей
    (то есть всех известных, включая прочитанные из прежнего ``meta.json``),
    отсортированный по времени, затем по идентификатору.
    """
    meta_path = archive_dir_for(task_dir) / ARCHIVE_META_NAME
    known = _load_existing_meta(meta_path)

    fresh: dict[int, dict[str, Any]] = {}
    for submission in submissions:
        if not isinstance(submission, dict):
            continue
        code = extract_submission_code(submission)
        if not code:
            continue
        record = ArchivedSubmission(
            submission_id=int(submission.get("id") or 0),
            status=str(submission.get("status") or ""),
            time=str(submission.get("time") or ""),
            file_name=submission_file_name(submission),
            hint=str(submission.get("hint") or ""),
        )
        fresh[record.submission_id] = record.as_meta()
        code_path = archive_dir_for(task_dir) / record.file_name
        if code_path.exists():
            continue
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(code, encoding="utf-8")

    merged = {**known, **fresh}
    if not merged:
        return []

    ordered = sorted(
        merged.values(),
        key=lambda entry: (str(entry.get("time") or ""), int(entry.get("submission_id") or 0)),
    )
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    save_json_file(meta_path, {"submissions": ordered})
    _log.info("история отправок: %d записей (%s)", len(ordered), meta_path.parent)

    return [
        ArchivedSubmission(
            submission_id=int(entry.get("submission_id") or 0),
            status=str(entry.get("status") or ""),
            time=str(entry.get("time") or ""),
            file_name=str(entry.get("file") or ""),
            hint=str(entry.get("hint") or ""),
        )
        for entry in ordered
    ]
