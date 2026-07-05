"""microbench_runner.py — timeit-микробенчмарк через subprocess + os.devnull.

Используется grader.py: ``run_microbench`` импортируется и вызывается из
``grader.run_microbench_mode`` для stdin-режима.

Архитектура:
    Исходник решения пишется во временный файл и запускается через
    ``python -c`` с timeit.repeat. На время замера stdout решения
    перенаправляется в os.devnull, чтобы его print()-вывод не смешивался с
    числами-таймингами и не ломал парсинг. stdin сбрасывается перед каждой
    итерацией через инжектированную в builtins функцию ``_reset_stdin``.

    Передача исходника через файл (а не heredoc) исключает поломку на тройных
    кавычках (''' / \"\"\") внутри решения.

    Временный файл создаётся с delete=False и удаляется вручную в finally —
    это единственный кросс-платформенный способ: delete_on_close=False (3.12+)
    работает иначе на Linux и Windows и непригоден для передачи пути в subprocess.

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
    run_microbench_with_timeout — защитный ThreadPoolExecutor-таймаут для
                                произвольного fn(); не используется в текущих
                                вызовах run_microbench() (уже защищены
                                subprocess.run(timeout=60)), см. докстринг.

    Печать таблицы результатов в этом модуле НЕ реализована — это
    ответственность вызывающей стороны (grader.py делает это сам).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# resource — POSIX-only (RLIMIT_AS best-effort memory cap, issue #43 S-01).
# На Windows модуль отсутствует; лимит памяти там не применяется — тот же
# паттерн graceful degradation, что и в grader_core.py / executor.py.
try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

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


def _apply_memory_limit(pid: int, max_memory_mb: int | None) -> None:
    """Best-effort лимит RLIMIT_AS на дочерний pid ПОСЛЕ spawn (issue #67).

    Дублирует одноимённый helper из grader_core.py — грейдер сам импортирует
    microbench_runner (не наоборот), кросс-импорт создал бы цикл в DAG;
    helper небольшой, дублирование дешевле нарушения слойности.

    ``resource.prlimit`` вместо preexec_fn — потокобезопасно (не форкает в
    многопоточном родителе). Linux-only: на macOS prlimit отсутствует
    (``AttributeError``), на Windows нет модуля ``resource`` — там no-op.
    """
    if resource is None or max_memory_mb is None:
        return
    limit_bytes = max_memory_mb * 1024 * 1024
    try:
        resource.prlimit(pid, resource.RLIMIT_AS, (limit_bytes, limit_bytes))  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass


def run_microbench(
    source_code: str,
    *,
    stdin_data: str = "",
    number: int = 1000,
    max_memory_mb: int | None = None,
) -> dict[str, Any]:
    """Запустить timeit-microbenchmark для исходного кода.

    Код запускается как строка через python -c.
    stdin сбрасывается перед каждой итерацией через _reset_stdin() в начале stmt.

    Временный файл создаётся с delete=False и удаляется в блоке finally —
    это единственный надёжный кросс-платформенный способ передать путь к файлу
    в subprocess. delete_on_close=False (Python 3.12+) ведёт себя по-разному
    на Linux (удаляет при close) и Windows, и непригоден здесь.

    max_memory_mb: best-effort лимит адресного пространства дочернего процесса
        (RLIMIT_AS, POSIX-only; issue #43 S-01). None — без ограничения.

    Возвращает словарь с ключами:
        times          (list[float]) — список замеров (в секундах на итерацию)
        error          (str)         — сообщение об ошибке (пустая = успех)
        peak_memory_mb (float)       — пик Python-heap (tracemalloc), не RSS
                                        процесса; 0.0 при ошибке
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        encoding=ENCODING,
        delete=False,
    )
    try:
        tmp.write(source_code)
        tmp.flush()
        tmp.close()
        code_path = tmp.name

        # Весь вспомогательный код помещаем в bench_script через exec,
        # чтобы _reset_stdin была доступна в глобальном пространстве stmt.
        # stmt — строка, выполняемая timeit; globals не пробрасываются через repeat()
        # в Python 3.14+, поэтому инжектируем функцию через builtins.
        # stdout решения подавляется на время замера: иначе его print()-вывод
        # попадает на stdout вперемешку с таймингами и портит парсинг.
        # Реальный stdout сохраняется и восстанавливается только для печати таймингов,
        # так что на stdout оказываются ИСКЛЮЧИТЕЛЬНО 5 чисел-таймингов.
        # os и contextlib используются здесь (в finally), а не только внутри bench_script.
        bench_script = (
            "import timeit as _timeit, sys as _sys, io as _io, os as _os, "
            "builtins as _builtins, tracemalloc as _tm\n"
            "_stdin = " + repr(stdin_data) + "\n"
            "def _reset_stdin():\n"
            "    _sys.stdin = _io.StringIO(_stdin)\n"
            "_builtins._reset_stdin = _reset_stdin\n"
            "_reset_stdin()\n"
            f"with open({code_path!r}, encoding='utf-8') as _f:\n"
            "    _code = _f.read()\n"
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

        try:
            # issue #67: Popen (не subprocess.run), чтобы поставить лимит памяти
            # на pid ПОСЛЕ spawn через prlimit — preexec_fn небезопасен.
            proc = subprocess.Popen(
                [sys.executable, "-c", bench_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=ENCODING,
            )
            _apply_memory_limit(proc.pid, max_memory_mb)
            try:
                stdout, stderr = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                # issue #47 R-01: no per-call timeout exists inside the child
                # (timeit.repeat runs number x 5 as one opaque call, so we can't
                # report which iteration hung) -- surface the iteration count
                # instead, so a hang at number=50000 isn't indistinguishable
                # from one at number=5.
                return {
                    "times": [],
                    "error": (
                        f"microbench timeout: exceeded 60s running number={number} "
                        "iterations per repeat (5 repeats total)"
                    ),
                    "peak_memory_mb": 0.0,
                }
            if proc.returncode != 0:
                return {"times": [], "error": stderr.strip(), "peak_memory_mb": 0.0}
            lines = stdout.strip().splitlines()
            mem_lines = [line for line in lines if line.startswith("MEM:")]
            time_lines = [line for line in lines if not line.startswith("MEM:")]
            times = [float(line) for line in time_lines if line.strip()]
            peak_mb = float(mem_lines[-1][len("MEM:") :]) / 1024 / 1024 if mem_lines else 0.0
            return {"times": times, "error": "", "peak_memory_mb": peak_mb}
        except (OSError, ValueError) as exc:
            # OSError: Popen couldn't spawn the child process.
            # ValueError: float(line) failed on unparseable subprocess stdout.
            return {"times": [], "error": str(exc), "peak_memory_mb": 0.0}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)


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
    results: dict[str, dict[str, Any]],
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


def run_microbench_with_timeout(
    fn: Callable[[], list[float]],
    timeout: float = 60.0,
) -> list[float]:
    """Запускает fn() с защитным таймаутом (Sprint 7.3).

    Not currently wired into grader_core.run_microbench_mode(): run_microbench()
    above already wraps its subprocess.run() call in timeout=60, which reliably
    kills the child process on TimeoutExpired -- the calling thread is guaranteed
    to unblock. Wrapping an already-subprocess-bounded call in a ThreadPoolExecutor
    would add no real protection and, if fn() ever DOES hang past `timeout`, this
    function abandons the worker thread without killing whatever fn() was running
    (subprocess.run's own kill() is the only thing that actually reclaims the
    child process). Kept available for a future fn() that isn't already
    subprocess-bounded.

    Parameters
    ----------
    fn:
        Функция, возвращающая список замеров времени.
    timeout:
        Максимальное время ожидания в секундах.

    Returns
    -------
    Список замеров или пустой список при превышении таймаута.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return []
