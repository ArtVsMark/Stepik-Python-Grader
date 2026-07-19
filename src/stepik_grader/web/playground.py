"""playground.py — одиночный запуск произвольного кода со stdin (issue #317).

Архитектурный слой: Application/UI (web-адаптер), как ``viewmodels.py``.

Раздел «Песочница»: не проверка против тест-кейсов, а «запусти этот код с
этим вводом и покажи вывод». Исполняет через ``grading.run_spec()`` — активный
``Runner`` (тот же путь, что грейдинг) — общий wall-clock таймаут, best-effort лимит
памяти (POSIX), кооперативная отмена через ``cancel_event``. По умолчанию это
``LocalRunner``; при ``--serve --sandbox`` (issue #396) — ``SandboxRunner``,
поэтому песочница web изолируется вместе с grade-путём.

По умолчанию (без ``--sandbox``) OS-изоляции нет (инвариант CLAUDE.md №4): код
исполняется в subprocess без изоляции ФС/сети, с правами пользователя. Внешние
guard'ы (localhost-only, лимит тела #259) — забота ``server.py``.
"""

from __future__ import annotations

import contextlib
import pathlib
import tempfile
import threading
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.web.grading import RunSpec, run_spec

__all__ = ["run_playground"]

# Обрезка вывода в ответе: защищает клиента (и память сервера при передаче) от
# решения, печатающего мегабайты. Само исполнение уже ограничено таймаутом.
_MAX_OUTPUT_CHARS = 100_000


def _clip(text: str) -> tuple[str, bool]:
    """Обрезать текст до ``_MAX_OUTPUT_CHARS``; вернуть (текст, был_ли_обрезан)."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text, False
    return text[:_MAX_OUTPUT_CHARS], True


def _decode(data: bytes) -> str:
    """Декодировать вывод и нормализовать переносы (CRLF→LF на Windows)."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_playground(
    code: str, stdin: str = "", *, cancel_event: threading.Event | None = None
) -> dict[str, Any]:
    """Запустить ``code`` со ``stdin`` в subprocess и вернуть результат.

    Форма ответа: ``{status, stdout, stderr, exit_code, duration_ms,
    truncated}``. ``status`` — ``OK`` (код завершился с 0), ``RE`` (ненулевой
    код / не запустился), ``TLE`` (превышен таймаут), ``CANCELLED`` (отменён
    через ``cancel_event``). Никакой сверки с ожидаемым выводом — это
    песочница, не грейдинг.
    """
    # delete=False намеренно: путь файла уходит в RunSpec раннеру, чистится в finally.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".py", encoding="utf-8", delete=False
    )
    try:
        tmp.write(code)
        tmp.close()
        outcome = run_spec(
            RunSpec(
                path=pathlib.Path(tmp.name),
                stdin=stdin.encode("utf-8"),
                timeout=float(CONFIG.timeout_seconds),
                measure_memory=False,  # песочнице пик RSS не нужен — без psutil-потока
                max_memory_mb=CONFIG.max_memory_mb,
                cancel_event=cancel_event,
            )
        )
    finally:
        with contextlib.suppress(OSError):  # уборка временного файла
            pathlib.Path(tmp.name).unlink()

    duration_ms = int(outcome.elapsed * 1000)
    if outcome.launch_error is not None:
        return _result("RE", stderr=outcome.launch_error, exit_code=None, duration_ms=duration_ms)
    if outcome.timed_out:
        return _result("TLE", exit_code=None, duration_ms=duration_ms)
    if outcome.cancelled:
        return _result("CANCELLED", exit_code=None, duration_ms=duration_ms)

    stdout, clipped_out = _clip(_decode(outcome.stdout))
    stderr, clipped_err = _clip(_decode(outcome.stderr))
    return _result(
        "OK" if outcome.returncode == 0 else "RE",
        stdout=stdout,
        stderr=stderr,
        exit_code=outcome.returncode,
        duration_ms=duration_ms,
        truncated=clipped_out or clipped_err,
    )


def _result(
    status: str,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = 0,
    duration_ms: int = 0,
    truncated: bool = False,
) -> dict[str, Any]:
    """Собрать словарь-ответ песочницы со стабильным набором полей."""
    return {
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "truncated": truncated,
    }
