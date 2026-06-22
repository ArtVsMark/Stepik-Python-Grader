"""microbench_runner.py — timeit-based microbenchmark via exec + io.StringIO.

Архитектура:
    Вместо прямого вызова func(*args) мы имитируем stdin через io.StringIO
    и запускаем полный exec(compiled_code) внутри одного процесса.

    Это устраняет фундаментальное противоречие:
    - тест-файлы tests/N созданы для subprocess (stdin → input() → print())
    - timeit должен вызывать код напрямую, без нового процесса

    Решение: compile() один раз снаружи цикла (амортизирует парсинг),
    затем exec(compiled, {}) в каждой итерации с подменённым sys.stdin.
    sys.stdout подавлен через io.StringIO — print() не замедляет замер.

Типичный вызов из test.py (режим 4):
    stdin_texts = ["\\n".join(tc.input_lines) for tc in test_cases]
    result = run_microbench(source_code, stdin_texts, file_label, repeats)
    results = apply_relative_micro(results)
    print_microbench_table(task_folder, results)
"""

from __future__ import annotations

import io
import statistics
import sys
import timeit
from dataclasses import dataclass, field

SIMILAR_THRESHOLD_PERCENT = 5.0


@dataclass
class MicrobenchResult:
    """Timing result for a single solution file."""

    file: str
    repeats: int
    timings: list[float] = field(default_factory=list)
    error: str = ""
    relative_percent: float = 100.0
    verdict: str = "OK"

    # func_name kept for display compatibility
    func_name: str = "<exec>"

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
    def stdev_time(self) -> float:
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0


def _make_stdin_runner(compiled, stdin_text: str):
    """Return a zero-arg callable that exec's compiled code with stdin mocked.

    compile() is done OUTSIDE this function (once per file) so timeit
    measures only the execution logic, not source parsing.

    sys.stdout is replaced with a fresh io.StringIO on every call so
    print() output doesn't reach the terminal and doesn't distort timing.
    """
    def _run():
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        sys.stdin = io.StringIO(stdin_text)
        sys.stdout = io.StringIO()
        try:
            exec(compiled, {})  # noqa: S102
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
    return _run


def run_microbench(
    source_code: str,
    stdin_texts: list[str],
    file_label: str,
    repeats: int = 1_000,
) -> MicrobenchResult:
    """Timeit microbenchmark: exec source_code with each stdin text.

    Parameters
    ----------
    source_code:
        Full Python source of the solution (function-only или любой скрипт
        с input()).
    stdin_texts:
        Список строк-stdin для каждого тест-кейса.
        Каждая строка — это содержимое тест-файла tests/N (строки через \\n).
        timeit запускается для каждого элемента отдельно, timings складываются.
    file_label:
        Короткий лейбл для таблицы результатов (обычно rel_path файла).
    repeats:
        Количество вызовов timeit.timeit(..., number=repeats) на каждый stdin.
        Общее число замеров = repeats × len(stdin_texts).
    """
    # Compile once — amortise source parsing across all repeats
    try:
        compiled = compile(source_code, file_label, "exec")
    except SyntaxError as exc:
        return MicrobenchResult(
            file=file_label,
            repeats=repeats,
            error=f"SyntaxError: {exc}",
        )

    if not stdin_texts:
        stdin_texts = [""]

    timings: list[float] = []

    for stdin_text in stdin_texts:
        runner = _make_stdin_runner(compiled, stdin_text)
        try:
            # timeit returns total seconds for `number` calls
            total = timeit.timeit(runner, number=repeats)
            per_call = total / repeats
            timings.append(per_call)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc(limit=4)
            return MicrobenchResult(
                file=file_label,
                repeats=repeats,
                error=f"{type(exc).__name__}: {exc}\n{tb}",
            )

    return MicrobenchResult(
        file=file_label,
        repeats=repeats,
        timings=timings,
    )


def apply_relative_micro(results: list[MicrobenchResult]) -> list[MicrobenchResult]:
    """Set relative_percent and verdict on each result relative to the fastest."""
    valid = [r for r in results if r.timings and not r.error]
    if not valid:
        return results

    best = min(r.median_time for r in valid)

    for r in valid:
        r.relative_percent = (r.median_time / best) * 100 if best > 0 else 100.0
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
