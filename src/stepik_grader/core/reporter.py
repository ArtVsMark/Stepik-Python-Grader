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

    def rich_track(sequence: Any, description: str = "") -> Any:  # noqa: ARG001
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


def print_benchmark_header(*, col_file: int) -> None:
    """Напечатать заголовок benchmark-таблицы для режимов 3 и 4."""
    print(_SEP)
    print(
        f"{'File':<{col_file}} {'Runs':>4}  "
        f"{'Min':>10}  {'Median':>10}  {'Mean':>10}  {'Max':>10}  "
        f"{'Std dev':>10}  {'Memory':>9}  {'Relative':>8}  {'Verdict'}"
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
) -> None:
    """Напечатать benchmark-таблицу (rich при наличии, иначе plain-text)."""
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
            ("Memory", 9),
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

    print_benchmark_header(col_file=col_file)
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
    """Подробный вывод одного тест-кейса (режим 1, verbose): вердикт + diff при WA."""
    passed = r["passed"]
    verdict = r.get("verdict", "AC" if passed else "WA")
    icon = "✓" if passed else "✗"
    color = "green" if passed else "red"
    _cprint(f"  {icon} Test {case.index}: {verdict}", style=color)

    if r["error"]:
        _cprint(f"    [ERROR] {r['error']}", style="red")
        return
    if passed:
        return

    # WA: компактное сравнение expected vs actual + diff.
    expected = " | ".join(r.get("expected", case.expected_lines)) or "(empty)"
    actual = " | ".join(r.get("output", [])) or "(empty)"
    _cprint(f"    Expected: {expected}")
    _cprint(f"    Actual:   {actual}")
    diff = r.get("diff", "")
    if diff:
        _cprint("    Diff:")
        for line in diff.splitlines():
            if line.startswith("+"):
                _cprint(f"    {line}", style="green")
            elif line.startswith("-"):
                _cprint(f"    {line}", style="red")
            else:
                _cprint(f"    {line}", style="dim")
