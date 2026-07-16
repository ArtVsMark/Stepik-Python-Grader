"""stats.py — opt-in локальная статистика запусков (issue #268).

Архитектурный слой: Infrastructure / Utilities. Зависит только от stdlib
(json/pathlib/platform/time) — leaf-модуль, как и ``core/cache.py``.

Идея: пользователь сам не видит, откуда берутся его WA/RE ("70% моих WA —
форматирование вывода"), а мейнтейнер вслепую приоритизирует улучшения
(доля Windows, популярность режимов). Философия проекта запрещает любую
сетевую телеметрию — вместо неё локальный JSON Lines журнал, opt-in
(``--stats`` / ``[tool.stepik-grader] stats = true``), который никогда не
покидает машину пользователя.

Формат — JSON Lines (``.grader_stats.jsonl``), не единый JSON-объект, как у
``GraderCache`` (issue #56): запись — это ``append`` одной строки, без
перечитывания и перезаписи всего файла на каждый прогон — при прерывании
процесса (Ctrl+C, crash) корневой файл не может быть повреждён из-за
недописанной перезаписи, максимум теряется последняя незавершённая строка.
Ротация по размеру (``_MAX_BYTES``) — грубая (оставляет вторую половину
строк), не логарифмическая: для локального личного журнала точность не
важна, важно не дать файлу расти неограниченно.

Best-effort по всему модулю (тот же принцип, что ``GraderCache``/
``glossary_missing_queue``): битый файл, отсутствие прав на запись, полный
диск — никогда не должны ронять грейдинг, только тихо пропустить запись.
"""

from __future__ import annotations

import json
import pathlib
import platform
import threading
import time
from typing import Any

__all__ = ["STATS_FILE_NAME", "read_summary", "record_run"]

STATS_FILE_NAME = ".grader_stats.jsonl"
_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB — ротация (оставить новую половину строк)
_SCHEMA_VERSION = 1

# Процессный лок вокруг ротации + append (issue #352). Web-слой пишет статистику
# из многопоточного ThreadPoolExecutor (web/runs.py); без сериализации
# read-modify-write ротации (_rotate_if_needed: прочитать файл целиком и
# переписать половину) конкурентные потоки могут затирать записи друг друга.
# Process-level Lock достаточно для модели «один процесс, много потоков»;
# межпроцессную гонку (CLI и web одновременно) он НЕ закрывает — её снимет
# переход истории на SQLite/WAL (issue #344).
_WRITE_LOCK = threading.Lock()


def _default_path() -> pathlib.Path:
    return pathlib.Path.cwd() / STATS_FILE_NAME


def _rotate_if_needed(path: pathlib.Path) -> None:
    """Оставить новую половину строк, если файл превысил ``_MAX_BYTES``.

    Вызывается перед каждой записью — сам append дешёвый (``stat()``), а
    перечитывание всего файла происходит только когда лимит реально
    превышен (редко для личного журнала на диске)."""
    try:
        if not path.is_file() or path.stat().st_size <= _MAX_BYTES:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = lines[len(lines) // 2 :]
        path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except OSError:
        pass


def record_run(
    mode: int,
    verdicts: dict[str, int],
    total_time: float,
    *,
    stats_path: pathlib.Path | None = None,
) -> None:
    """Дописать одну запись о прогоне (issue #268).

    ``mode`` — 1..4 (номер режима CLI); ``verdicts`` — тальи по вердиктам
    (для режимов 1/2 — AC/WA/RE/TLE по кейсам, для 3/4 —
    SIMILAR/SLOWER/MUCH_SLOWER/ERR по решениям); ``total_time`` — суммарное
    время прогона в секундах (приближённо для 3/4 — mean × runs).

    Best-effort: любая ``OSError`` (нет прав, диск полон, ``.grader_stats.
    jsonl`` — директория) тихо проглатывается — запись статистики не должна
    ронять грейдинг, тот же принцип, что у ``GraderCache`` (issue #56).
    """
    path = stats_path or _default_path()
    entry = {
        "v": _SCHEMA_VERSION,
        "ts": time.time(),
        "mode": mode,
        "os": platform.system(),
        "verdicts": verdicts,
        "total_time": total_time,
    }
    try:
        with _WRITE_LOCK:
            _rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_summary(stats_path: pathlib.Path | None = None) -> dict[str, Any]:
    """Собрать сводку по всем записанным прогонам (``stats``-команда CLI).

    Отсутствующий файл — пустая сводка (``total_runs=0``), не ошибка.
    Каждая строка парсится независимо: битая/неполная строка (обрыв записи
    при крэше, ручное редактирование) просто пропускается, не роняя чтение
    остальных строк — тот же принцип graceful degradation, что у
    ``GraderCache._load()``.
    """
    path = stats_path or _default_path()
    by_mode: dict[int, int] = {}
    by_os: dict[str, int] = {}
    verdict_totals: dict[str, int] = {}
    total_runs = 0
    total_time = 0.0

    if path.is_file():
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            raw_lines = []
        for raw_line in raw_lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            mode = entry.get("mode")
            if isinstance(mode, int):
                by_mode[mode] = by_mode.get(mode, 0) + 1
                total_runs += 1

            os_name = entry.get("os")
            if isinstance(os_name, str):
                by_os[os_name] = by_os.get(os_name, 0) + 1

            verdicts = entry.get("verdicts")
            if isinstance(verdicts, dict):
                for verdict, count in verdicts.items():
                    if isinstance(verdict, str) and isinstance(count, int):
                        verdict_totals[verdict] = verdict_totals.get(verdict, 0) + count

            entry_time = entry.get("total_time")
            if isinstance(entry_time, int | float):
                total_time += entry_time

    return {
        "total_runs": total_runs,
        "by_mode": by_mode,
        "by_os": by_os,
        "verdict_totals": verdict_totals,
        "total_time": total_time,
    }
