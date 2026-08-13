"""core/attachments.py — вложения условия задачи рядом с решением (issue #1112).

Условие говорит «вам доступен текстовый файл ``files.txt``» и даёт ссылку на
``stepik.org/media/attachments/...``. Решение открывает этот файл **по имени**,
из рабочего каталога — значит файл обязан лежать рядом с ним. Загрузчик его не
забирал, поэтому целый жанр задач (работа с файлами) локально не
воспроизводился: принятое платформой решение падало ``FileNotFoundError``, а
глоссарий добросовестно объяснял студенту его несуществующую ошибку.

Границы модуля: скачивание best-effort. Один недоступный файл не роняет
скачивание задачи — про него печатается предупреждение, и в ``meta.json``
остаётся отметка, что вложение не приехало. Молчаливый пропуск здесь хуже
всего: `RE` без объяснения снова спишут на решение.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any
from urllib.parse import unquote

import requests

from stepik_grader.core.stepik_client import (
    ExternalUrlRejected,
    external_download_get,
    is_stepik_url,
)

__all__ = [
    "MAX_ATTACHMENTS",
    "download_attachments",
    "safe_attachment_name",
]

#: Потолок на задачу. Вложений в условии единицы; сотня ссылок означает не
#: щедрого автора, а разметку, которую мы разобрали неверно.
MAX_ATTACHMENTS = 10

#: Что остаётся от имени файла: всё прочее — разделители путей, ``..`` и
#: управляющие символы — вырезается. Имя приходит из недоверенного HTML и
#: превращается в путь на диске, поэтому берётся только basename и только из
#: этого алфавита (issue #838 — тот же класс, что у ссылок на тесты).
_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_attachment_name(url: str) -> str:
    """Имя файла для вложения по URL — без путей, ``..`` и пустых результатов.

    Возвращает ``""``, если из ссылки не удалось получить осмысленное имя
    (ссылка на каталог, пустой хвост): вызывающий такой файл пропускает, а не
    выдумывает имя сам.

    Путь отрезается вручную по ``?``/``#``, а не через ``urlparse().path``:
    последний считает всё после ``;`` параметрами сегмента, и имя
    ``a;rm -rf.txt`` превращалось в ``a`` — то есть кусок имени молча терялся.
    """
    path = unquote(url.split("#", 1)[0].split("?", 1)[0])
    if path.endswith("/"):
        return ""
    cleaned = _SAFE_NAME_RE.sub("_", pathlib.PurePosixPath(path).name).strip("._")
    return cleaned[:120]


def download_attachments(
    task_dir: pathlib.Path,
    links: list[str],
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Скачать вложения условия в каталог задачи; вернуть отчёт по каждому.

    Существующий файл **не перезаписывается**: у задач с файловым вводом его
    правят руками (обрезают, дополняют своими случаями), и перекачка шага не
    имеет права стирать эту работу — то же правило, что у ``submissions/``
    (issue #1055).

    Args:
        task_dir: каталог задачи, рядом с ``task.md``.
        links: ссылки из :func:`task_page_parser.extract_attachment_links`.
        session: авторизованная сессия — используется только для Stepik;
            сторонний хост идёт через ``external_download_get`` без токена.

    Returns:
        По записи на ссылку: ``{"name", "url", "status"}``, где ``status`` —
        ``saved`` / ``exists`` / ``failed`` / ``skipped``. Список едет в
        ``meta.json``: по нему видно, что вложение не приехало, ещё до того,
        как решение упадёт ``FileNotFoundError``.
    """
    report: list[dict[str, Any]] = []
    for url in links[:MAX_ATTACHMENTS]:
        name = safe_attachment_name(url)
        if not name:
            report.append({"name": "", "url": url, "status": "skipped"})
            continue

        target = task_dir / name
        if target.exists():
            report.append({"name": name, "url": url, "status": "exists"})
            continue

        try:
            response = (
                session.get(url, timeout=30) if is_stepik_url(url) else (external_download_get(url))
            )
            response.raise_for_status()
            target.write_bytes(response.content)
        except (requests.RequestException, ExternalUrlRejected, OSError) as exc:
            report.append({"name": name, "url": url, "status": "failed", "error": str(exc)})
            continue

        report.append({"name": name, "url": url, "status": "saved"})
    return report
