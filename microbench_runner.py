"""microbench_runner.py — timeit-based microbenchmark via exec + io.StringIO.

Архитектура:
    Вместо прямого вызова func(*args) мы имитируем stdin через io.StringIO
    и запускаем полный exec(compiled_code) внутри одного процесса.

    Это устраняет фундаментальное противоречие:
    - тест-файлы tests/N созданы для subprocess (stdin -> input() -> print())
    - timeit должен вызывать код напрямую, без нового процесса

    Решение: compile() один раз снаружи цикла (амортизует парсинг),
    затем exec(compiled, {}) в каждой итерации.

    stdin/stdout перенаправляются через contextlib.redirect_stdin /
    contextlib.redirect_stdout — поток-безопасно в отличие от
    прямой подмены sys.stdin/sys.stdout.

Типичный вызов из test.py (режим 4):
    stdin_texts = ["\\n".join(tc.input_lines) for tc in test_cases]
    result = run_microbench(source_code, stdin_texts, file_label, repeats)
    results = apply_relative_micro(results)
    print_microbench_table(task_folder, results)
"""

from __future__ import annotations

import contextlib
import io
import statistics
import timeit
import traceback
import types
from collections.abc import Callable
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


def _make_stdin_runner(compiled: types.CodeType, stdin_text: str) -> Callable[[], None]:
    """Return a zero-arg callable that exec's compiled code with stdin/stdout redirected.

    Используются contextlib.redirect_stdin + contextlib.redirect_stdout
    вместо прямой подмены sys.stdin/sys.stdout.

    Почему это важно:
    - Прямая подмена sys.stdin = io.StringIO(...) глобальна для всего
      интерпретатора. При параллельном запуске двух потоков это data race.
    - contextlib.redirect_stdin изолирован внутри блока with,
      безопасно восстанавливая оригинал (даже при Exception).

    compile() вынесен за пределы функции (один раз на файл) — timeit замеряет
    только логику выполнения, а не парсинг исходника.
    """
    def _run() -> None:
        fake_stdin = io.StringIO(stdin_text)
        fake_stdout = io.StringIO()
        with contextlib.redirect_stdin(fake_stdin):
            with contextlib.redirect_stdout(fake_stdout):
                exec(compiled, {})  # noqa: S102
                # Примечание: exec(compiled, {}) автоматически получает
                # {'__builtins__': ...} в namespace — это стандартное
                # поведение Python и необходимо для работы input().
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
        Общее число замеров = repeats x len(stdin_texts).
    """
    # Compile once — amortise source parsing across all repeats
    try:
        compiled: types.CodeType = compile(source_code, file_label, "exec")
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
            # timeit возвращает суммарное время (seconds) для number вызовов
            total = timeit.timeit(runner, number=repeats)
            per_call = total / repeats
            timings.append(per_call)
        except Exception as exc:
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
