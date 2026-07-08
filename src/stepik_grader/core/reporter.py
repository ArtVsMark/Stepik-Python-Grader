"""reporter.py — вывод результатов грейдера: таблицы, цвета, verbose-diff.

Архитектурный слой: Application / UI.
Владеет rich-опциональной зависимостью (_console/_RICH) и всеми функциями
форматирования/печати таблиц корректности и бенчмарка. Не содержит бизнес-логики
запуска решений — это core/grader_core.py.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 1).
Перенесён в core/ (Issue #26).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from stepik_grader.core.glossary import lookup_from_error
from stepik_grader.core.result import TestResult

if TYPE_CHECKING:
    from core.grader_core import TestCase

__all__ = [
    "fmt_time",
    "format_correctness_row",
    "print_correctness_header",
    "print_correctness_results",
    "format_benchmark_row",
    "print_benchmark_header",
    "print_benchmark_results",
    "print_case_verbose",
    "rich_track",
]

# rich — опциональная зависимость для цветного вывода таблиц и прогресс-баров.
# При её отсутствии грейдер откатывается на простой текстовый вывод.
try:
    from rich.console import Console
    from rich.progress import track as rich_track
    from rich.table import Table
    from rich.text import Text

    _console: Console | None = Console(width=200)
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False

    # Минимальные заглушки, чтобы имена Console/Table/Text были всегда определены
    # (используются в аннотациях и в коде, не зависящем от _RICH).
    class Console:  # type: ignore[no-redef]
        def print(self, *args: Any, **kwargs: Any) -> None:
            print(*args)

    class Table:  # type: ignore[no-redef]
        pass

    class Text:  # type: ignore[no-redef]
        pass

    def rich_track(sequence: Any, description: str = "") -> Any:  # type: ignore[misc]  # noqa: ARG001
        return sequence


_SEP = "-" * 107


def fmt_time(t: float) -> str:
    """Отформатировать время с автовыбором единиц (s / ms / µs / ns).

    Фиксированный ``:.4f`` (секунды) обнуляет суб-миллисекундные тайминги в
    выводе режимов 3/4 — например, 150 мкс печаталось как "0.0001" или
    "0.0000". Автовыбор единиц сохраняет значащие цифры на любом масштабе.
    Используется только для колонок min/median/mean/max/stdev бенчмарка
    (режимы 3/4); режимы 1/2 (format_correctness_row) не затронуты.
    """
    if t >= 1:
        return f"{t:.3f} s"
    if t >= 1e-3:
        return f"{t * 1e3:.3f} ms"
    if t >= 1e-6:
        return f"{t * 1e6:.3f} µs"
    return f"{t * 1e9:.3f} ns"


def format_correctness_row(
    path: str, base_dir: str, result: dict[str, Any], *, col_file: int
) -> str:
    """Сформатировать строку таблицы корректности для режимов 1 и 2."""
    total = result["total"]
    passed = result["passed"]
    ok = passed == total and result["failed"] == 0 and result["errors"] == 0 and total > 0
    status = "OK" if ok else "FAIL"
    rel = os.path.relpath(path, base_dir)
    total_t = result["total_time"]
    avg_t = result["avg_time"]
    mem = result["peak_memory_mb"]
    first_fail = result["first_fail"]
    return (
        f"{rel:<{col_file}} {passed:>3}/{total:<3}  "
        f"{total_t:>10.4f}  {avg_t:>9.4f}  "
        f"{mem:>9.2f} MB  {status:>6}  {str(first_fail):>9}"
    )


def print_correctness_header(*, col_file: int) -> None:
    """Напечатать заголовок таблицы корректности для режимов 1 и 2."""
    print(_SEP)
    print(
        f"{'File':<{col_file}} {'Passed':>7}  "
        f"{'Total time':>10}  {'Avg time':>9}  "
        f"{'Memory, MB':>12}  {'Status':>6}  {'Fail test':>9}"
    )
    print(_SEP)


def format_benchmark_row(path: str, base_dir: str, data: dict[str, Any], *, col_file: int) -> str:
    """Сформатировать строку benchmark-таблицы для режимов 3 и 4."""
    rel_path = os.path.relpath(path, base_dir)
    return (
        f"{rel_path:<{col_file}} {data['runs']:>4}  "
        f"{fmt_time(data['min']):>10}  {fmt_time(data['median']):>10}  "
        f"{fmt_time(data['mean']):>10}  {fmt_time(data['max']):>10}  "
        f"{fmt_time(data['stdev']):>10}  "
        f"{data['peak_memory_mb']:>7.2f} MB  "
        f"{data['relative'] * 100:>7.1f}%  {data['verdict']}"
    )


def print_benchmark_header(*, col_file: int, memory_header: str = "Memory") -> None:
    """Напечатать заголовок benchmark-таблицы для режимов 3 и 4.

    memory_header — подпись колонки памяти. Режим 3 (``run_benchmark``)
    всегда меряет RSS через psutil → ``"Memory"``. Режим 4
    (``run_microbench_mode``) для stdin-блоков меряет пик Python-heap через
    tracemalloc, а не RSS → ``"Py-heap"``: одна колонка, разные методики,
    поэтому подпись обязана отражать методику, а не подразумевать RSS
    (issue #66).
    """
    print(_SEP)
    print(
        f"{'File':<{col_file}} {'Runs':>4}  "
        f"{'Min':>10}  {'Median':>10}  {'Mean':>10}  {'Max':>10}  "
        f"{'Std dev':>10}  {memory_header:>9}  {'Relative':>8}  {'Verdict'}"
    )
    print(_SEP)


# ---------------------------------------------------------------------------
# Цветной вывод (rich) с откатом на plain-text
# ---------------------------------------------------------------------------

# Цвета статусов корректности (режимы 1/2) и вердиктов TLE/RE/WA.
_STATUS_COLORS: dict[str, str] = {
    "OK": "green",
    "AC": "green",
    "FAIL": "red",
    "WA": "red",
    "TLE": "red",
    "RE": "red",
    "ERROR": "red",
}

# Цвета вердиктов бенчмарка (режимы 3/4).
_VERDICT_COLORS: dict[str, str] = {
    "SIMILAR": "green",
    "SLOWER": "yellow",
    "MUCH_SLOWER": "red",
    "MUCH SLOWER": "red",
}


def _correctness_status(result: dict[str, Any]) -> str:
    """Вернуть "OK"/"FAIL" для строки таблицы корректности."""
    total = result["total"]
    ok = result["passed"] == total and result["failed"] == 0 and result["errors"] == 0 and total > 0
    return "OK" if ok else "FAIL"


def print_correctness_results(
    rows: list[tuple[str, dict[str, Any]]], base_dir: str, *, col_file: int
) -> None:
    """Напечатать таблицу корректности (rich при наличии, иначе plain-text)."""
    if _RICH and _console is not None:
        table = Table(show_lines=False)
        table.add_column("File", style="cyan", no_wrap=True)
        table.add_column("Passed", justify="right")
        table.add_column("Total time", justify="right")
        table.add_column("Avg time", justify="right")
        table.add_column("Memory, MB", justify="right")
        table.add_column("Status", justify="center")
        table.add_column("Fail test", justify="right")
        for path, result in rows:
            status = _correctness_status(result)
            color = _STATUS_COLORS.get(status, "white")
            table.add_row(
                os.path.relpath(path, base_dir),
                f"{result['passed']}/{result['total']}",
                f"{result['total_time']:.4f}",
                f"{result['avg_time']:.4f}",
                f"{result['peak_memory_mb']:.2f}",
                Text(status, style=color),
                str(result["first_fail"]),
            )
        _console.print(table)
        return

    print_correctness_header(col_file=col_file)
    for path, result in rows:
        print(format_correctness_row(path, base_dir, result, col_file=col_file))


def print_benchmark_results(
    rows: list[tuple[str, dict[str, Any]]],
    base_dir: str,
    *,
    col_file: int,
    title: str = "",
    memory_header: str = "Memory",
) -> None:
    """Напечатать benchmark-таблицу (rich при наличии, иначе plain-text).

    memory_header — подпись колонки памяти, см. print_benchmark_header
    (issue #66): ``"Memory"`` (RSS, режим 3) или ``"Py-heap"`` (режим 4).
    """
    if _RICH and _console is not None:
        table = Table(title=title or None, show_lines=False)
        table.add_column("File", style="cyan", no_wrap=True)
        for name, mw in [
            ("Runs", 4),
            ("Min", 10),
            ("Median", 10),
            ("Mean", 10),
            ("Max", 10),
            ("Std dev", 10),
            (memory_header, 9),
        ]:
            table.add_column(name, justify="right", min_width=mw)
        table.add_column("Relative", justify="right", min_width=8)
        table.add_column("Verdict", justify="center", min_width=10)
        for path, data in rows:
            verdict = data["verdict"]
            color = _VERDICT_COLORS.get(verdict, "white")
            table.add_row(
                os.path.relpath(path, base_dir),
                str(data["runs"]),
                fmt_time(data["min"]),
                fmt_time(data["median"]),
                fmt_time(data["mean"]),
                fmt_time(data["max"]),
                fmt_time(data["stdev"]),
                f"{data['peak_memory_mb']:.2f}",
                f"{data['relative'] * 100:.1f}%",
                Text(verdict, style=color),
            )
        _console.print(table)
        return

    print_benchmark_header(col_file=col_file, memory_header=memory_header)
    for path, data in rows:
        print(format_benchmark_row(path, base_dir, data, col_file=col_file))


def _cprint(text: str, *, style: str = "") -> None:
    """Печать строки со стилем (rich) или без (plain). markup отключён — безопасно
    для произвольного вывода решения (скобки не интерпретируются как разметка).

    Инвариант: _RICH == True гарантирует _console is not None (Console() создаётся
    при успешном импорте rich), поэтому дополнительная проверка не нужна.
    """
    if _RICH and style:
        _console.print(text, style=style, markup=False)  # type: ignore[union-attr]
    else:
        print(text)


def print_case_verbose(case: TestCase, r: dict[str, Any]) -> None:
    """Подробный вывод одного тест-кейса (режим 1, verbose): вердикт + diff при WA.

    ``r`` — case-result dict (форма ``run_single_test()``, issue #116); сразу
    конвертируется в типизированный ``TestResult`` (issue #113/#114), дальше
    функция читает только его поля, а не произвольные ключи словаря.
    """
    result = TestResult.from_dict(r)
    icon = "✓" if result.passed else "✗"
    color = "green" if result.passed else "red"
    _cprint(f"  {icon} Test {case.index}: {result.verdict}", style=color)

    if result.error:
        _cprint(f"    [ERROR] {result.error}", style="red")
        # issue #72: подсказка по типу исключения + ссылка на глоссарий.
        entry = lookup_from_error(result.error)
        if entry is not None:
            _cprint(f"    💡 {entry.exception}: {entry.hint}", style="yellow")
            _cprint(f"       {entry.url}", style="blue")
        return
    if result.passed:
        return

    # WA: компактное сравнение expected vs actual + diff.
    expected = " | ".join(result.expected) or "(empty)"
    actual = " | ".join(result.output) or "(empty)"
    _cprint(f"    Expected: {expected}")
    _cprint(f"    Actual:   {actual}")
    if result.diff:
        _cprint("    Diff:")
        for line in result.diff.splitlines():
            if line.startswith("+"):
                _cprint(f"    {line}", style="green")
            elif line.startswith("-"):
                _cprint(f"    {line}", style="red")
            else:
                _cprint(f"    {line}", style="dim")
