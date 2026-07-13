"""playground.py — одиночный запуск произвольного кода со stdin (issue #317).

Архитектурный слой: Application/UI (web-адаптер), как ``viewmodels.py``.

Раздел «Песочница»: не проверка против тест-кейсов, а «запусти этот код с
этим вводом и покажи вывод». Переиспользует ``LocalRunner`` (тот же
subprocess-путь, что грейдинг) — общий wall-clock таймаут, best-effort лимит
памяти (POSIX), кооперативная отмена через ``cancel_event``.

Безопасности OS-уровня нет (инвариант CLAUDE.md №4): код исполняется в
subprocess без изоляции ФС/сети, с правами пользователя. Только доверенный код,
как и в грейдинге. Внешние guard'ы (localhost-only, лимит тела #259) — забота
``server.py``.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import threading
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import LocalRunner, RunSpec

__all__ = ["run_playground"]

# Обрезка вывода в ответе: защищает клиента (и память сервера при передаче) от
# решения, печатающего мегабайты. Само исполнение уже ограничено таймаутом.
_MAX_OUTPUT_CHARS = 100_000


def _clip(text: str) -> tuple[str, bool]:
    """Обрезать текст до ``_MAX_OUTPUT_CHARS``; вернуть (текст, был_ли_обрезан)."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text, False
    return text[:_MAX_OUTPUT_CHARS], True


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
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False)
    try:
        tmp.write(code)
        tmp.close()
        outcome = LocalRunner().run(
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
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    duration_ms = int(outcome.elapsed * 1000)
    if outcome.launch_error is not None:
        return _result("RE", stderr=outcome.launch_error, exit_code=None, duration_ms=duration_ms)
    if outcome.timed_out:
        return _result("TLE", exit_code=None, duration_ms=duration_ms)
    if outcome.cancelled:
        return _result("CANCELLED", exit_code=None, duration_ms=duration_ms)

    stdout, clipped_out = _clip(outcome.stdout.decode("utf-8", errors="replace"))
    stderr, clipped_err = _clip(outcome.stderr.decode("utf-8", errors="replace"))
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
