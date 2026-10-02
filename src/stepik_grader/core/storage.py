"""storage.py — чтение JSON-файлов и запись секретов.

Архитектурный слой: Infrastructure / Utilities.
Не имеет зависимостей от других модулей проекта (leaf — только stdlib).
Отвечает исключительно за:
  - чтение JSON-файлов с диска,
  - сохранение secrets dict в файл с правами 0600.

issue #996 (``ARCH-3-05``): обычной атомарной записи JSON здесь больше нет —
она одна на весь пакет и живёт в top-level ``atomic_io.atomic_write_json``.
Было два «единых» писателя с разной семантикой прав: здешний наследовал права
уже существующего файла, а общий всегда оставлял 0600. Один и тот же
`chmod g+r` на конфиг сохранялся или отменялся в зависимости от того, какой
модуль его записал. Наследование прав переехало в общий писатель, потребители
(`cache`, `downloader`, `downloader_config`, `stepik_reference`,
`submission_archive`, `web/downloader_adapter`) зовут его напрямую.

``save_secrets`` остался здесь намеренно: у него ДРУГОЙ контракт — 0600
принудительно, права цели не наследуются никогда (см. его докстринг).
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
from typing import Any

__all__ = [
    "load_json_file",
    "save_secrets",
]


def load_json_file(file_path: pathlib.Path) -> dict[str, Any]:
    """Читает JSON-файл и возвращает dict.

    Raises:
        IsADirectoryError: если file_path — директория (кросс-платформенно;
            на Windows open() бросает PermissionError вместо IsADirectoryError).
        ValueError: если корень JSON не является объектом.
    """
    if file_path.is_dir():
        raise IsADirectoryError(f"Ожидался файл, получена директория: {file_path}")
    with file_path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в файле {file_path}")
    return data


def save_secrets(secrets_path: pathlib.Path, data: dict[str, Any]) -> None:
    """Сохраняет secrets dict (OAuth-токены, client_secret) с правами только для владельца.

    На POSIX файл создаётся атомарно в режиме 0600 (``os.open`` с явным
    ``mode``, без окна с более широкими правами между созданием и chmod) —
    и принудительно приводится к 0600, если уже существовал с более
    широкими правами от старой версии. На Windows у ``os.chmod`` нет
    эквивалента Unix-битам group/other (модель доступа — NTFS ACL, а не
    биты режима), поэтому там вызов практически no-op и файл остаётся
    защищён только стандартными правами профиля пользователя ОС
    (issue #243, security audit finding F-04).

    Запись **атомарна** (issue #628): temp-файл в той же директории через
    ``mkstemp`` (создаётся сразу с 0600 — окна с широкими правами нет), затем
    ``os.replace``. Прежний путь ``os.open(..., O_TRUNC)`` писал поверх цели:
    конкурентный читатель мог получить усечённый JSON (``--serve`` работает на
    ``ThreadingHTTPServer``, а обновление токена идёт из потока-обработчика), а
    краш ровно в момент записи затирал ``refresh_token`` — пользователю
    приходилось заново проходить браузерный OAuth. Докстринги
    ``oauth_flow``/``web.auth_adapter``, обещавшие атомарность, теперь верны.
    """
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(
        dir=secrets_path.parent, prefix=f".{secrets_path.name}.", suffix=".tmp"
    )
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        # В отличие от `atomic_io.atomic_write_json` права цели НЕ наследуются:
        # секреты всегда приводятся к 0600, даже если старый файл остался с
        # более широкими правами от прежней версии (issue #243). os.replace
        # переносит режим temp-файла на цель, поэтому гарантия сохраняется и
        # после замены. Ради этого отличия функция и живёт отдельно от общего
        # писателя (issue #996, ARCH-3-05).
        with contextlib.suppress(OSError):
            tmp_path.chmod(0o600)
        tmp_path.replace(secrets_path)
    except BaseException:
        # issue #996 (PY-3-05): не только OSError. Ctrl+C — `KeyboardInterrupt`,
        # он `BaseException` и мимо прежнего перехвата проходил насквозь,
        # оставляя temp с СЕКРЕТАМИ (0600, но навсегда) рядом с целью.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
