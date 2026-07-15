"""microbench_runner.py — timeit-микробенчмарк через активный Runner + os.devnull.

Используется grader.py: ``run_microbench`` импортируется и вызывается из
``grader.run_microbench_mode`` для stdin-режима.

Архитектура:
    Код решения инлайнится (через ``repr``) в self-contained bench-скрипт с
    timeit.repeat; скрипт пишется во временный ``.py`` и исполняется через
    активный ``grader_core._RUNNER`` (``LocalRunner`` или ``SandboxRunner`` при
    ``--serve --sandbox``, issue #417). На время замера stdout решения
    перенаправляется в os.devnull, чтобы его print()-вывод не смешивался с
    числами-таймингами и не ломал парсинг. stdin сбрасывается перед каждой
    итерацией через инжектированную в builtins функцию ``_reset_stdin``.

    Инлайн через ``repr`` (а не heredoc/чтение файла) исключает поломку на
    тройных кавычках внутри решения и делает bench исполнимым в песочнице, где
    смонтирован только сам bench-скрипт.

    Временный файл создаётся с delete=False и удаляется вручную в finally —
    это единственный кросс-платформенный способ: delete_on_close=False (3.12+)
    работает иначе на Linux и Windows.

    Память (Issue #25): psutil-подход режима 3 (отдельный поток, читающий RSS
    дочернего процесса) здесь неприменим — все 5 повторов timeit.repeat идут
    в ОДНОМ subprocess, замерить RSS отдельно для каждого нельзя. Вместо этого
    ``tracemalloc`` включается перед timeit.repeat и выключается сразу после;
    пик выделений Python-heap печатается отдельной строкой (``MEM:<bytes>``)
    после строк с таймингами и парсится отдельно. Это НЕ RSS процесса —
    tracemalloc не видит память, выделенную C-расширениями (numpy и т.п.) —
    но для чистого Python-кода даёт содержательное сравнение.

Дополнительный публичный API (вспомогательные структуры для агрегации):
    MicrobenchResult          — dataclass с таймингами одного решения
    apply_relative_micro      — расстановка относительных процентов и вердиктов
    apply_relative_ranking    — то же для dict-результатов run_benchmark/
                                run_microbench_mode (grader_core.py)

    Печать таблицы результатов в этом модуле НЕ реализована — это
    ответственность вызывающей стороны (grader.py делает это сам).
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import statistics
import tempfile
from dataclasses import dataclass, field
from typing import Any

from stepik_grader.core.runner import RunSpec

__all__ = [
    "ENCODING",
    "SIMILAR_THRESHOLD_PERCENT",
    "WARMUP_RUNS",
    "MicrobenchResult",
    "run_microbench",
    "apply_relative_micro",
    "apply_relative_ranking",
]

ENCODING: str = "utf-8"
SIMILAR_THRESHOLD_PERCENT = 5.0
WARMUP_RUNS = 3


@dataclass
class MicrobenchResult:
    """Timing result for a single solution file."""

    file: str
    repeats: int
    timings: list[float] = field(default_factory=list)
    error: str = ""
    relative_percent: float = 100.0
    verdict: str = "OK"

    @property
    def min_time(self) -> float:
        return min(self.timings) if self.timings else 0.0

    @property
    def median_time(self) -> float:
        return statistics.median(self.timings) if self.timings else 0.0

    @property
    def mean_time(self) -> float:
        return statistics.mean(self.timings) if self.timings else 0.0

    @property
    def max_time(self) -> float:
        return max(self.timings) if self.timings else 0.0

    @property
    def std_dev_time(self) -> float:
        """Standard deviation of per-call timings. Returns 0.0 if fewer than 2 samples."""
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0


def run_microbench(
    source_code: str,
    *,
    stdin_data: str = "",
    number: int = 1000,
    max_memory_mb: int | None = None,
) -> dict[str, Any]:
    """Запустить timeit-microbenchmark для исходного кода.

    Код инлайнится в self-contained bench-скрипт и исполняется через активный
    ``grader_core._RUNNER`` (issue #417), а не напрямую subprocess'ом.
    stdin сбрасывается перед каждой итерацией через _reset_stdin() в начале stmt.

    bench-скрипт пишется во временный файл с delete=False и удаляется в блоке
    finally — единственный надёжный кросс-платформенный способ передать путь
    Runner'у. delete_on_close=False (Python 3.12+) ведёт себя по-разному
    на Linux (удаляет при close) и Windows, и непригоден здесь.

    max_memory_mb: best-effort лимит адресного пространства дочернего процесса
        (RLIMIT_AS, POSIX-only; issue #43 S-01). None — без ограничения.

    Возвращает словарь с ключами:
        times          (list[float]) — список замеров (в секундах на итерацию)
        error          (str)         — сообщение об ошибке (пустая = успех)
        peak_memory_mb (float)       — пик Python-heap (tracemalloc), не RSS
                                        процесса; 0.0 при ошибке
    """
    # bench_script self-contained: код решения инлайнится через repr (без чтения
    # внешних файлов), поэтому bench исполним и в песочнице, где смонтирован
    # только сам bench-скрипт (issue #417). _reset_stdin инжектится через
    # builtins (globals не пробрасываются через timeit.repeat в Python 3.14+).
    # stdout решения подавляется на время замера, чтобы на stdout остались
    # ИСКЛЮЧИТЕЛЬНО 5 чисел-таймингов + строка MEM:.
    bench_script = (
        "import timeit as _timeit, sys as _sys, io as _io, os as _os, "
        "builtins as _builtins, tracemalloc as _tm\n"
        "_stdin = " + repr(stdin_data) + "\n"
        "def _reset_stdin():\n"
        "    _sys.stdin = _io.StringIO(_stdin)\n"
        "_builtins._reset_stdin = _reset_stdin\n"
        "_reset_stdin()\n"
        "_code = " + repr(source_code) + "\n"
        "_stmt = '_reset_stdin()\\n' + _code\n"
        f"_number = {number}\n"
        "_real_stdout = _sys.stdout\n"
        "_devnull = open(_os.devnull, 'w')\n"
        "_sys.stdout = _devnull\n"
        "_tm.start()\n"
        "try:\n"
        "    _times = _timeit.repeat(\n"
        "        stmt=_stmt,\n"
        "        setup='pass',\n"
        "        repeat=5,\n"
        "        number=_number,\n"
        "    )\n"
        "finally:\n"
        "    _sys.stdout = _real_stdout\n"
        "    _devnull.close()\n"
        "_, _peak_bytes = _tm.get_traced_memory()\n"
        "_tm.stop()\n"
        "_per = [t / _number for t in _times]\n"
        "print('\\n'.join(str(t) for t in _per))\n"
        "print('MEM:' + str(_peak_bytes))\n"
    )

    # issue #417: исполнять bench-скрипт через активный grader_core._RUNNER
    # (LocalRunner или SandboxRunner при --serve --sandbox), а не напрямую
    # `python -c` мимо изоляции. Per-call тайминги замеряются ВНУТРИ дочернего
    # процесса (timeit.repeat), поэтому обёртка песочницы их не искажает; лимит
    # памяти теперь ставит сам Runner (RunSpec.max_memory_mb).
    from stepik_grader.core import grader_core  # локальный импорт: избежать цикла в DAG

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding=ENCODING, delete=False)
    try:
        tmp.write(bench_script)
        tmp.flush()
        tmp.close()
        outcome = grader_core._RUNNER.run(
            RunSpec(
                path=pathlib.Path(tmp.name),
                stdin=None,
                timeout=60.0,
                measure_memory=False,
                max_memory_mb=max_memory_mb,
            )
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)

    if outcome.launch_error is not None:
        return {"times": [], "error": outcome.launch_error, "peak_memory_mb": 0.0}
    if outcome.timed_out:
        # issue #47 R-01: no per-call timeout inside the child (timeit.repeat is
        # one opaque call) -- surface the iteration count, not which repeat hung.
        return {
            "times": [],
            "error": (
                f"microbench timeout: exceeded 60s running number={number} "
                "iterations per repeat (5 repeats total)"
            ),
            "peak_memory_mb": 0.0,
        }
    stdout = outcome.stdout.decode(ENCODING, errors="replace")
    stderr = outcome.stderr.decode(ENCODING, errors="replace")
    if outcome.returncode != 0:
        return {"times": [], "error": stderr.strip(), "peak_memory_mb": 0.0}
    try:
        result_lines = stdout.strip().splitlines()
        mem_lines = [line for line in result_lines if line.startswith("MEM:")]
        time_lines = [line for line in result_lines if not line.startswith("MEM:")]
        times = [float(line) for line in time_lines if line.strip()]
        peak_mb = float(mem_lines[-1][len("MEM:") :]) / 1024 / 1024 if mem_lines else 0.0
    except ValueError as exc:
        # float() failed on unparseable child stdout.
        return {"times": [], "error": str(exc), "peak_memory_mb": 0.0}
    return {"times": times, "error": "", "peak_memory_mb": peak_mb}


def apply_relative_micro(results: list[MicrobenchResult]) -> list[MicrobenchResult]:
    """Set relative_percent and verdict on each result relative to the fastest."""
    valid = [r for r in results if r.timings and not r.error]
    if not valid:
        return results

    best = min(r.median_time for r in valid)

    for r in valid:
        if best > 0:
            r.relative_percent = (r.median_time / best) * 100
        else:
            # Все решения имеют нулевое медианное время — считаем равноценными.
            r.relative_percent = 100.0
        delta = r.relative_percent - 100
        if delta <= SIMILAR_THRESHOLD_PERCENT:
            r.verdict = "SIMILAR"
        elif delta <= 15:
            r.verdict = "SLOWER"
        else:
            r.verdict = "MUCH SLOWER"

    for r in results:
        if r.error:
            r.verdict = "ERROR"

    return results


def apply_relative_ranking(
    results: dict[pathlib.Path, dict[str, Any]],
    *,
    similar_threshold: float,
    much_slower_threshold: float,
) -> None:
    """Set 'relative' and 'verdict' on each OK result, relative to the fastest median.

    Entries carrying a truthy 'error' key are left untouched (unranked). Mutates
    `results` in place. Shared by grader.py's mode-3 (subprocess-benchmark) and
    mode-4 (stdin-microbench) ranking, which previously duplicated this loop.
    """
    ok = {k: v for k, v in results.items() if not v.get("error")}
    if not ok:
        return
    min_median = min(v["median"] for v in ok.values())
    for v in ok.values():
        v["relative"] = v["median"] / min_median if min_median > 0 else 1.0
        if v["relative"] <= similar_threshold:
            v["verdict"] = "SIMILAR"
        elif v["relative"] <= much_slower_threshold:
            v["verdict"] = "SLOWER"
        else:
            v["verdict"] = "MUCH_SLOWER"
