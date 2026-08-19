"""stats.py — opt-in локальная статистика запусков (issue #268).

Архитектурный слой: Infrastructure / Utilities. Из проекта зависит только от
top-level stdlib-leaf'а ``atomic_io`` (ADR-0011 — он и заведён, чтобы общий
атомарный писатель был доступен и ``core/*``, и подпакетам без новых рёбер);
в остальном — чистый stdlib, как ``core/cache.py``.

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

import contextlib
import json
import pathlib
import platform
import threading
import time
from typing import Any

from stepik_grader.atomic_io import atomic_write_text

__all__ = ["STATS_FILE_NAME", "purge_stats", "read_summary", "record_run", "stats_path"]

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


def stats_path(path: pathlib.Path | None = None) -> pathlib.Path:
    """Путь к журналу статистики (issue #1005).

    Публичен, потому что о файле приходится ГОВОРИТЬ: сообщение про
    нечитаемый журнал бесполезно без имени файла, который предлагается
    удалить, а вызывающему знать про приватный ``_default_path`` незачем.
    """
    return path or _default_path()


def _rotate_if_needed(path: pathlib.Path) -> None:
    """Оставить новую половину строк, если файл превысил ``_MAX_BYTES``.

    Вызывается перед каждой записью — сам append дешёвый (``stat()``), а
    перечитывание всего файла происходит только когда лимит реально
    превышен (редко для личного журнала на диске)."""
    try:
        if not path.is_file() or path.stat().st_size <= _MAX_BYTES:
            return
        # issue #792 (FST-02): то же декодирование с заменой, что и при чтении
        # сводки. Здесь дефект коварнее: ротация вызывается ПЕРЕД записью, то
        # есть посторонний байт в журнале ронял бы уже сам прогон — но только
        # после того, как файл перерастёт лимит, поэтому на глаза попадается
        # редко и не воспроизводится на свежей установке.
        lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        keep = lines[len(lines) // 2 :]
        # issue #793 (PY-11): атомарная замена вместо перезаписи на месте.
        # Докстринг модуля обещает, что «корневой файл не может быть повреждён
        # из-за недописанной перезаписи», но ротация делала ровно её: обрыв
        # питания или Ctrl+C посреди write_text оставлял журнал наполовину
        # записанным. Приём тот же, что у настроек и очереди глоссария
        # (ADR-0011): temp рядом с целью + replace.
        atomic_write_text(path, "\n".join(keep) + ("\n" if keep else ""))
    except OSError:
        pass


def _needs_leading_newline(path: pathlib.Path) -> bool:
    """Оборвалась ли последняя запись журнала без завершающего ``\\n`` (issue #793).

    Читается один последний байт (``seek`` с конца), а не весь файл: проверка
    выполняется перед КАЖДОЙ записью, а журнал растёт до мегабайт.
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return False
        with path.open("rb") as fh:
            fh.seek(-1, 2)  # os.SEEK_END
            return fh.read(1) != b"\n"
    except OSError:
        return False


def record_run(
    mode: int,
    verdicts: dict[str, int],
    total_time: float,
    *,
    stats_path: pathlib.Path | None = None,
    isolation: str | None = None,
) -> None:
    """Дописать одну запись о прогоне (issue #268).

    ``isolation`` — уровень изоляции прогона (``"none"`` или имя backend'а
    песочницы); ``None`` — поле не пишется, как в записях до issue #997.

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
    if isolation is not None:
        # issue #997 (SBX-5-04): по строке статистики нельзя было отличить
        # прогон под --sandbox от обычного, а вердикты они дают разные.
        entry["isolation"] = isolation
    try:
        with _WRITE_LOCK:
            _rotate_if_needed(path)
            # issue #793 (FST-03): если предыдущая запись оборвалась без
            # завершающего перевода строки (крэш ровно посреди write), append
            # приклеил бы новую строку к огрызку — и пропали бы ОБЕ: склейка не
            # разбирается как JSON. Формат JSONL выбран именно ради «максимум
            # теряется последняя незавершённая строка», поэтому восстанавливаем
            # границу записи перед добавлением новой.
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            if _needs_leading_newline(path):
                line = "\n" + line
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


def read_summary(stats_path: pathlib.Path | None = None) -> dict[str, Any]:
    """Собрать сводку по всем записанным прогонам (``stats``-команда CLI).

    Отсутствующий файл — пустая сводка (``total_runs=0``), не ошибка.
    Каждая строка парсится независимо: битая/неполная строка (обрыв записи
    при крэше, ручное редактирование) просто пропускается, не роняя чтение
    остальных строк — тот же принцип graceful degradation, что у
    ``GraderCache._load()``.

    Число пропущенных строк возвращается в ``skipped`` (issue #1005,
    ``FZZ-5-06``): пропуск сам по себе правильный, но молчаливый. Журнал,
    испорченный целиком, давал ``total_runs=0`` и сообщение «статистика
    выключена или ещё не накопилась» — то есть ровно ту причину, которой нет:
    записи были, их просто не удалось прочитать, и починить это можно было
    только удалением файла, о котором никто не сказал.
    """
    path = stats_path or _default_path()
    by_mode: dict[int, int] = {}
    by_os: dict[str, int] = {}
    verdict_totals: dict[str, int] = {}
    total_runs = 0
    total_time = 0.0
    skipped = 0

    if path.is_file():
        try:
            # issue #792 (FST-02): байты + декодирование с заменой. Прежний
            # read_text падал UnicodeDecodeError (подкласс ValueError, мимо
            # `except OSError`) от единственного постороннего байта, и команда
            # `stats` умирала целиком — вместо того чтобы пропустить одну
            # испорченную строку журнала. Битая строка не разберётся как JSON и
            # будет пропущена ниже, остальные прогоны сохранятся в сводке.
            raw_lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            raw_lines = []
        for raw_line in raw_lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(entry, dict):
                skipped += 1
                continue

            mode = entry.get("mode")
            if isinstance(mode, int):
                by_mode[mode] = by_mode.get(mode, 0) + 1
                total_runs += 1
            else:
                # Запись разобралась как JSON, но без режима она в сводку не
                # попадает ничем — для пользователя это такая же потеря.
                skipped += 1

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
        # issue #1192: среднее время прогона — явным полем, а не «поделите сами».
        # В накопителе копилась только сумма, и вопрос «сколько занимает один
        # прогон» требовал арифметики над двумя другими числами; при пустом
        # журнале эта же арифметика давала ZeroDivisionError у каждого, кто
        # пробовал.
        "avg_time": total_time / total_runs if total_runs else 0.0,
        "skipped": skipped,
    }


def purge_stats(path: pathlib.Path | None = None) -> int:
    """Удалить журнал статистики; вернуть число удалённых записей (issue #813).

    Журнал прогонов — такие же личные данные, как история: что и когда
    запускалось, сколько заняло. Пользователю нужен способ их убрать, не зная
    имени файла. Best-effort: отсутствующий журнал — это 0 записей, не ошибка.
    """
    target = _default_path() if path is None else path
    with _WRITE_LOCK:  # не пересечься с ротацией/дозаписью из web-потока
        if not target.is_file():
            return 0
        try:
            # errors="replace" — как и на чтении сводки (#792): посторонний байт
            # не должен мешать удалению собственных данных пользователя.
            raw = target.read_bytes().decode("utf-8", errors="replace")
            removed = sum(1 for line in raw.splitlines() if line.strip())
        except OSError:
            removed = 0
        with contextlib.suppress(OSError):
            target.unlink()
        return removed
