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

Дополнительный публичный API (вспомогательные структуры для агрегации):
    MicrobenchResult        — dataclass с таймингами одного решения
    apply_relative_micro    — расстановка относительных процентов и вердиктов

    Печать таблицы результатов в этом модуле НЕ реализована — это
    ответственность вызывающей стороны (grader.py делает это сам).
"""

from __future__ import annotations

import contextlib
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any

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
) -> dict[str, Any]:
    """Запустить timeit-microbenchmark для исходного кода.

    Код запускается как строка через python -c.
    stdin сбрасывается перед каждой итерацией через _reset_stdin() в начале stmt.

    Временный файл создаётся с delete=False и удаляется в блоке finally —
    это единственный надёжный кросс-платформенный способ передать путь к файлу
    в subprocess. delete_on_close=False (Python 3.12+) ведёт себя по-разному
    на Linux (удаляет при close) и Windows, и непригоден здесь.

    Возвращает словарь с ключами:
        times  (list[float]) — список замеров (в секундах на итерацию)
        error  (str)         — сообщение об ошибке (пустая = успех)
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
            "import timeit as _timeit, sys as _sys, io as _io, os as _os, builtins as _builtins\n"
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
            "_per = [t / _number for t in _times]\n"
            "print('\\n'.join(str(t) for t in _per))\n"
        )

        try:
            result = subprocess.run(
                [sys.executable, "-c", bench_script],
                capture_output=True,
                text=True,
                timeout=60,
                encoding=ENCODING,
            )
            if result.returncode != 0:
                return {"times": [], "error": result.stderr.strip()}
            times = [float(line) for line in result.stdout.strip().splitlines() if line.strip()]
            return {"times": times, "error": ""}
        except subprocess.TimeoutExpired:
            return {"times": [], "error": "microbench timeout"}
        except (OSError, ValueError) as exc:
            # OSError: subprocess.run() couldn't spawn the child process.
            # ValueError: float(line) failed on unparseable subprocess stdout.
            return {"times": [], "error": str(exc)}
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
