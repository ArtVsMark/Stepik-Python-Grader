"""microbench_runner.py — timeit-микробенчмарк через активный Runner + os.devnull.

Используется grader.py: ``run_microbench`` импортируется и вызывается из
``grader.run_microbench_mode`` для stdin-режима.

Архитектура:
    Код решения инлайнится (через ``repr``) в self-contained bench-скрипт с
    timeit.repeat; скрипт пишется во временный ``.py`` и исполняется через
    ``grader_core.run_spec()`` активным Runner'ом (``LocalRunner`` или
    ``SandboxRunner`` при ``--serve --sandbox``, issue #417). На время замера stdout решения
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
import pathlib
import re
import statistics
import tempfile
from dataclasses import dataclass, field
from typing import Any

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import RunSpec

__all__ = [
    "ENCODING",
    "SIMILAR_THRESHOLD_PERCENT",
    "WARMUP_RUNS",
    "MicrobenchResult",
    "apply_reference_ranking",
    "apply_relative_micro",
    "apply_relative_ranking",
    "run_microbench",
    "strip_harness_frames",
]

ENCODING: str = "utf-8"
SIMILAR_THRESHOLD_PERCENT = 5.0
# issue #412: число холостых прогонов stmt ПЕРЕД замером — праймит импорты/кэши,
# чтобы cold-start не завышал min/median. Вшивается в bench-скрипт
# (_build_bench_script) как `_warmup`; вне tracemalloc/timeit.repeat.
WARMUP_RUNS = 3

# issue #726: кадры timeit-обёртки в traceback'е упавшего решения. Замер идёт
# внутри `timeit`, поэтому падение пользовательского кода приходит с хвостом из
# кадров `Lib/timeit.py`, `<timeit-src>` и самого bench-скрипта — для ученика это
# шум: его код инлайнится в bench-скрипт, так что содержательная часть — только
# последняя строка (тип исключения и сообщение).
_FRAME_START = re.compile(r'^\s+File "(?P<file>.*?)", line \d+')
_HARNESS_FILES = ("<timeit-src>",)
_HARNESS_SUFFIXES = ("timeit.py",)


def strip_harness_frames(text: str, *, harness_path: str | None = None) -> str:
    """Убрать из traceback'а кадры timeit-обёртки (issue #726).

    Выбрасываются кадры, чей файл — ``<timeit-src>``, стандартный
    ``timeit.py`` или сам сгенерированный bench-скрипт (``harness_path``).
    Кадры пользовательского кода, вводная строка ``Traceback (most recent call
    last):`` и финальная строка исключения сохраняются; если после фильтрации
    кадров не осталось вовсе, вводная строка тоже убирается — иначе остаётся
    заголовок без содержимого.

    Текст, не похожий на traceback, возвращается без изменений.
    """
    lines = text.splitlines()
    kept: list[str] = []
    kept_frames = 0
    drop_frame = False
    for line in lines:
        match = _FRAME_START.match(line)
        if match is not None:
            file_name = match.group("file")
            drop_frame = (
                file_name in _HARNESS_FILES
                or file_name.endswith(_HARNESS_SUFFIXES)
                or (harness_path is not None and file_name == harness_path)
            )
            if not drop_frame:
                kept_frames += 1
                kept.append(line)
            continue
        # Продолжение кадра — строка исходника или маркеры «~~~^^^» под ней.
        if line.startswith(" ") and (kept_frames or drop_frame):
            if not drop_frame:
                kept.append(line)
            continue
        drop_frame = False
        kept.append(line)

    if not kept_frames and kept and kept[0].startswith("Traceback"):
        kept = kept[1:]
    return "\n".join(kept).strip()


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
        """Минимальный per-call тайминг; 0.0 при отсутствии замеров."""
        return min(self.timings) if self.timings else 0.0

    @property
    def median_time(self) -> float:
        """Медианный per-call тайминг; 0.0 при отсутствии замеров."""
        return statistics.median(self.timings) if self.timings else 0.0

    @property
    def mean_time(self) -> float:
        """Средний per-call тайминг; 0.0 при отсутствии замеров."""
        return statistics.mean(self.timings) if self.timings else 0.0

    @property
    def max_time(self) -> float:
        """Максимальный per-call тайминг; 0.0 при отсутствии замеров."""
        return max(self.timings) if self.timings else 0.0

    @property
    def std_dev_time(self) -> float:
        """Standard deviation of per-call timings. Returns 0.0 if fewer than 2 samples."""
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0


def _build_bench_script(source_code: str, stdin_data: str, number: int) -> str:
    """Собрать self-contained bench-скрипт для timeit-замера одного решения.

    Код решения инлайнится через ``repr`` (без чтения внешних файлов) — bench
    исполним и в песочнице, где смонтирован только сам скрипт (issue #417).
    ``_reset_stdin`` инжектится через builtins (globals не пробрасываются через
    ``timeit.repeat`` в Python 3.14+). stdout решения подавляется в
    ``os.devnull`` на время замера — на stdout остаются ИСКЛЮЧИТЕЛЬНО 5
    чисел-таймингов + строка ``MEM:``.

    Прогрев (issue #412): ``WARMUP_RUNS`` холостых прогонов ``_stmt`` выполняются
    ПЕРЕД замером и ВНЕ ``tracemalloc`` — праймят импорты, ленивую инициализацию
    и кэши, чтобы cold-start первого прогона не завышал min/median (в измеряемые
    время и память прогрев не входит).
    """
    return (
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
        f"_warmup = {WARMUP_RUNS}\n"
        "_real_stdout = _sys.stdout\n"
        "_devnull = open(_os.devnull, 'w')\n"
        "_sys.stdout = _devnull\n"
        "try:\n"
        "    # прогрев до замера: холостые прогоны праймят кэши/импорты (issue #412)\n"
        "    _timeit.timeit(stmt=_stmt, setup='pass', number=_warmup)\n"
        "    _tm.start()\n"
        "    _times = _timeit.repeat(\n"
        "        stmt=_stmt,\n"
        "        setup='pass',\n"
        "        repeat=5,\n"
        "        number=_number,\n"
        "    )\n"
        "    _peak_bytes = _tm.get_traced_memory()[1]\n"
        "    _tm.stop()\n"
        "finally:\n"
        "    _sys.stdout = _real_stdout\n"
        "    _devnull.close()\n"
        "_per = [t / _number for t in _times]\n"
        "print('\\n'.join(str(t) for t in _per))\n"
        "print('MEM:' + str(_peak_bytes))\n"
    )


def run_microbench(
    source_code: str,
    *,
    stdin_data: str = "",
    number: int = 1000,
    max_memory_mb: int | None = None,
) -> dict[str, Any]:
    """Запустить timeit-microbenchmark для исходного кода.

    Код инлайнится в self-contained bench-скрипт и исполняется через
    ``grader_core.run_spec()`` активным Runner'ом (issue #417/#640), а не напрямую subprocess'ом.
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
    bench_script = _build_bench_script(source_code, stdin_data, number)

    # issue #417: исполнять bench-скрипт через активный Runner (LocalRunner или
    # SandboxRunner при --serve --sandbox), а не напрямую `python -c` мимо
    # изоляции. Per-call тайминги замеряются ВНУТРИ дочернего процесса
    # (timeit.repeat), поэтому обёртка песочницы их не искажает; лимит памяти
    # теперь ставит сам Runner (RunSpec.max_memory_mb). issue #640: через
    # публичный grader_core.run_spec(), а не приватный _RUNNER — выбор backend'а
    # спрятан за одной точкой (ADR-0010).
    from stepik_grader.core import grader_core  # локальный импорт: избежать цикла в DAG

    # delete=False намеренно: путь файла уходит в RunSpec раннеру, чистится в finally.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".py", encoding=ENCODING, delete=False
    )
    try:
        tmp.write(bench_script)
        tmp.flush()
        tmp.close()
        outcome = grader_core.run_spec(
            RunSpec(
                path=pathlib.Path(tmp.name),
                stdin=None,
                timeout=60.0,
                measure_memory=False,
                max_memory_mb=max_memory_mb,
                # issue #629: stdout решения на время замера уходит в os.devnull,
                # а вот stderr остаётся открытым — решение, льющее туда traceback'и
                # в цикле по number×5 повторов, без лимита копило бы их в памяти
                # хоста через безлимитный communicate().
                max_output_bytes=CONFIG.max_output_bytes,
            )
        )
    finally:
        with contextlib.suppress(OSError):
            pathlib.Path(tmp.name).unlink()

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
        # issue #726: без кадров timeit-обёртки — они относятся к механике
        # замера, а не к решению (bench-скрипт уже удалён, путь берём из tmp).
        return {
            "times": [],
            "error": strip_harness_frames(stderr.strip(), harness_path=tmp.name),
            "peak_memory_mb": 0.0,
        }
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
            # issue #397: единый вердикт "MUCH_SLOWER" (подчёркивание) во всех
            # путях. Раньше здесь была форма с пробелом ("MUCH SLOWER"), из-за
            # чего insights._BENCH_SLOW (знает только "MUCH_SLOWER") молча не
            # относил такие прогоны к «медленным», а reporter держал двойной алиас.
            r.verdict = "MUCH_SLOWER"

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


def apply_reference_ranking(
    results: dict[pathlib.Path, dict[str, Any]],
    reference_path: pathlib.Path,
    *,
    similar_threshold: float,
    much_slower_threshold: float,
) -> None:
    """Rank each OK result relative to ``reference_path`` instead of the fastest.

    Sibling of ``apply_relative_ranking`` (issue #397: обе baseline-стратегии
    ранжирования живут в core, а не дублируются в web-презентации). Та же формула
    относительного времени, но baseline — медиана эталонного решения, а не
    ``min(median)``. Вердикты: ``REFERENCE`` (сам эталон), ``FASTER`` (заметно
    быстрее эталона — симметрично «похожести» вокруг порога), ``SIMILAR``,
    ``SLOWER``, ``MUCH_SLOWER``. Мутирует ``results`` in place; entries с
    truthy ``error`` не трогаются. Вызывающая сторона обязана убедиться, что
    ``reference_path in results`` и у него нет ``error`` (как в ``grade_benchmark``).
    """
    ok = {k: v for k, v in results.items() if not v.get("error")}
    base_median = ok[reference_path]["median"]
    faster_bound = 1.0 / similar_threshold if similar_threshold > 0 else 1.0
    for path, v in ok.items():
        v["relative"] = v["median"] / base_median if base_median > 0 else 1.0
        if path == reference_path:
            v["verdict"] = "REFERENCE"
        elif v["relative"] < faster_bound:
            v["verdict"] = "FASTER"
        elif v["relative"] <= similar_threshold:
            v["verdict"] = "SIMILAR"
        elif v["relative"] <= much_slower_threshold:
            v["verdict"] = "SLOWER"
        else:
            v["verdict"] = "MUCH_SLOWER"
