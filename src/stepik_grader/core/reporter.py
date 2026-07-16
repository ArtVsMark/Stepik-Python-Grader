"""reporter.py — вывод результатов грейдера: таблицы, цвета, verbose-diff.

Архитектурный слой: Application / UI.
Владеет rich-опциональной зависимостью (_console/_RICH) и всеми функциями
форматирования/печати таблиц корректности и бенчмарка. Не содержит бизнес-логики
запуска решений — это core/grader_core.py.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 1).
Перенесён в core/ (Issue #26).
"""

from __future__ import annotations

import pathlib
import shutil
from typing import TYPE_CHECKING, Any

from stepik_grader.core.error_glossary import resolve_error_hint
from stepik_grader.core.result import TestResult

if TYPE_CHECKING:
    from stepik_grader.core.grader_core import TestCase

__all__ = [
    "fmt_time",
    "format_correctness_row",
    "print_correctness_header",
    "print_correctness_results",
    "format_benchmark_row",
    "print_benchmark_header",
    "print_benchmark_results",
    "print_case_verbose",
    "print_stats_summary",
    "print_insights_summary",
    "print_progress_summary",
    "print_lint_block",
    "rich_track",
]

# Ширина вывода: кап 200 (как раньше), но сжимаемся под узкий терминал (issue
# #409) — на 80–120-колоночных экранах таблицы/разделители больше не переносятся
# и не распухают. Без терминала (CI, пайп) fallback=(200, …) сохраняет прежний
# вывод, поэтому детерминизм тестов не меняется.
_TABLE_WIDTH = 107  # проектная ширина таблиц корректности/бенчмарка
_TERM_WIDTH = min(shutil.get_terminal_size(fallback=(200, 24)).columns, 200)

# rich — опциональная зависимость для цветного вывода таблиц и прогресс-баров.
# При её отсутствии грейдер откатывается на простой текстовый вывод.
try:
    from rich.console import Console
    from rich.progress import track as rich_track
    from rich.table import Table
    from rich.text import Text

    _console: Console | None = Console(width=_TERM_WIDTH)
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


_SEP = "-" * min(_TABLE_WIDTH, _TERM_WIDTH)


def _safe_rel(path: pathlib.Path, base: pathlib.Path) -> str:
    """Относительный путь для колонок таблиц, устойчивый к разным anchor'ам.

    ``Path.relative_to(base, walk_up=True)`` кидает ``ValueError``, когда путь и
    база имеют разные anchor'ы (относительный ввод ``task.py`` против
    абсолютного ``base``; разные диски на Windows) — issue #440. В этом случае
    отдаём сам путь строкой, а не роняем режимы 1/2/3/4 трейсбеком.
    """
    try:
        return str(path.relative_to(base, walk_up=True))
    except ValueError:
        return str(path)


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


def _correctness_status(result: dict[str, Any]) -> str:
    """Вернуть "OK"/"FAIL"/"NO TESTS" для строки таблицы корректности (режимы 1/2).

    ``total == 0`` (тесты не найдены или директория tests/ пуста) — это не
    провал решения, а отсутствие проверки; docs/result-contract.md фиксирует
    "NO TESTS" как отдельный исход. Раньше ``total == 0`` попадал в ``FAIL``
    наравне с реально неверными решениями (issue B-1 / CC-1 повторного
    аудита 2026-07-13).
    """
    total = result["total"]
    if total == 0:
        return "NO TESTS"
    ok = result["passed"] == total and result["failed"] == 0 and result["errors"] == 0
    return "OK" if ok else "FAIL"


def format_correctness_row(
    path: pathlib.Path, base_dir: pathlib.Path, result: dict[str, Any], *, col_file: int
) -> str:
    """Сформатировать строку таблицы корректности для режимов 1 и 2."""
    total = result["total"]
    passed = result["passed"]
    status = _correctness_status(result)
    rel = _safe_rel(path, base_dir)
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


def format_benchmark_row(
    path: pathlib.Path, base_dir: pathlib.Path, data: dict[str, Any], *, col_file: int
) -> str:
    """Сформатировать строку benchmark-таблицы для режимов 3 и 4."""
    rel_path = _safe_rel(path, base_dir)
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
# CANCELLED (issue #262) — вердикт async job-модели, не CLI (там cancel_event
# никогда не передаётся); жёлтый, а не красный — это не провал решения.
# SANDBOX_VIOLATION (issue #266) — только у SandboxRunner (--sandbox);
# красный, как RE/TLE — в отличие от CANCELLED, это признак того, что
# решение реально сделало что-то запрещённое (не просто "долго").
_STATUS_COLORS: dict[str, str] = {
    "OK": "green",
    "AC": "green",
    "FAIL": "red",
    "WA": "red",
    "TLE": "red",
    "RE": "red",
    "ERROR": "red",
    "CANCELLED": "yellow",
    "SANDBOX_VIOLATION": "red",
    "NO TESTS": "yellow",
}

# Цвета вердиктов бенчмарка (режимы 3/4).
_VERDICT_COLORS: dict[str, str] = {
    "SIMILAR": "green",
    "SLOWER": "yellow",
    # issue #397: единый вердикт "MUCH_SLOWER" (подчёркивание) — двойной алиас
    # с пробел-формой больше не нужен (apply_relative_micro унифицирован).
    "MUCH_SLOWER": "red",
}


def print_correctness_results(
    rows: list[tuple[pathlib.Path, dict[str, Any]]], base_dir: pathlib.Path, *, col_file: int
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
                _safe_rel(path, base_dir),
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
    rows: list[tuple[pathlib.Path, dict[str, Any]]],
    base_dir: pathlib.Path,
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
                _safe_rel(path, base_dir),
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


def print_stats_summary(summary: dict[str, Any]) -> None:
    """Напечатать сводку локальной статистики запусков (issue #268).

    Предполагает непустую сводку (``summary["total_runs"] > 0``) — case
    «данных нет» (стата выключена или ещё не накопилась) печатает вызывающая
    сторона (``cli/__init__.py``) через локализованное сообщение ДО вызова
    этой функции, тем же паттерном, что ``ctx.t("no_results")`` перед
    ``print_correctness_results``/``print_benchmark_results`` в
    ``cli/commands.py`` — reporter.py сам не участвует в i18n (``_t()``
    живёт только в ``cli/__init__.py``), только рендерит уже готовые данные.
    Названия колонок — на английском, как у остальных таблиц этого модуля
    (``File``/``Passed``/``Verdict`` и т.д.) для внутренней консистентности.
    """
    rows: list[tuple[str, str]] = [("Total runs", str(summary.get("total_runs", 0)))]
    for mode, count in sorted(summary.get("by_mode", {}).items()):
        rows.append((f"Mode {mode}", str(count)))
    for os_name, count in sorted(summary.get("by_os", {}).items()):
        rows.append((f"OS: {os_name}", str(count)))
    for verdict, count in sorted(summary.get("verdict_totals", {}).items()):
        rows.append((f"Verdict {verdict}", str(count)))
    rows.append(("Total time", fmt_time(summary.get("total_time", 0.0))))

    if _RICH and _console is not None:
        table = Table(title="Local run stats", show_lines=False)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")
        for name, value in rows:
            table.add_row(name, value)
        _console.print(table)
        return

    print(_SEP)
    for name, value in rows:
        print(f"{name:<20} {value:>15}")
    print(_SEP)


_INSIGHT_STATUS_LABEL: dict[str, str] = {
    "active": "активна",
    "fading": "угасает",
    "watch": "наблюдение",
    "archived": "в архиве",
}


def _insight_reference(card: Any, rules_provider: Any) -> str:
    """Ссылка карточки инсайта: правило PEP (lint) или глоссарий (runtime-error)."""
    if card.category == "lint" and rules_provider is not None:
        rule = rules_provider.get(card.key)
        if rule is not None:
            return rule.pep_url or rule.docs_url or ""
    if card.glossary_id:
        return f"glossary:{card.glossary_id}"
    return ""


def print_insights_summary(cards: list[Any], *, rules_provider: Any = None) -> None:
    """Напечатать сводку карточек «Подучить» (issue #349).

    Предполагает непустой ``cards`` — пустое состояние («данных нет») печатает
    вызывающая сторона локализованным сообщением ДО вызова (тот же паттерн, что
    ``print_stats_summary``). ``rules_provider`` — для ссылок lint-карточек.
    """
    rows = [
        (
            card.key,
            _INSIGHT_STATUS_LABEL.get(card.status, card.status),
            f"{card.hits}/{card.runs_considered}",
            _insight_reference(card, rules_provider),
        )
        for card in cards
    ]
    if _RICH and _console is not None:
        table = Table(title="Подучить", show_lines=False)
        table.add_column("Ключ", style="cyan")
        table.add_column("Статус")
        table.add_column("Замечен", justify="right")
        table.add_column("Ссылка")
        for key, status, seen, ref in rows:
            table.add_row(key, status, seen, ref)
        _console.print(table)
        return
    print(_SEP)
    for key, status, seen, ref in rows:
        print(f"{key:<34} {status:<12} {seen:>8}  {ref}")
    print(_SEP)


def _fmt_duration(secs: float | None) -> str:
    """Секунды → компактная строка (``—``/``42с``/``7м``/``1.3ч``), issue #431."""
    if secs is None:
        return "—"
    if secs < 60:
        return f"{secs:.0f}с"
    if secs < 3600:
        return f"{secs / 60:.0f}м"
    return f"{secs / 3600:.1f}ч"


def print_progress_summary(progress: list[Any]) -> None:
    """Сводка «Прогресс»: попыток/времени до первого полного AC по задаче (issue #431).

    Предполагает непустой ``progress`` — empty state печатает вызывающая сторона
    (тот же паттерн, что ``print_insights_summary``).
    """
    rows = [
        (
            p.task_key or "(без задачи)",
            "✅" if p.solved else "…",
            str(p.attempts),
            _fmt_duration(p.seconds_to_first_ac),
        )
        for p in progress
    ]
    if _RICH and _console is not None:
        table = Table(title="Прогресс (до первого AC)", show_lines=False)
        table.add_column("Задача", style="cyan")
        table.add_column("Решено")
        table.add_column("Попыток", justify="right")
        table.add_column("Время", justify="right")
        for task, solved, attempts, dur in rows:
            table.add_row(task, solved, attempts, dur)
        _console.print(table)
        return
    print(_SEP)
    for task, solved, attempts, dur in rows:
        print(f"{task:<34} {solved:<6} {attempts:>8}  {dur:>8}")
    print(_SEP)


def print_lint_block(violations: list[Any], *, rules_provider: Any = None) -> None:
    """Блок «Стиль» после результатов режимов 1/2 (issue #349).

    Группирует нарушения по коду (``⚐ E501 ×3 [строки …]``) с однострочником
    правила из ``rules_provider``. Пустой список — тихо ничего. Линт НЕ влияет
    на вердикт — чисто информационный блок (эпик #342, § 9.4).
    """
    if not violations:
        return
    by_code: dict[str, list[int]] = {}
    for v in violations:
        by_code.setdefault(v.rule_code, []).append(v.line_no)
    _cprint("Стиль (не влияет на вердикт):", style="yellow")
    for code in sorted(by_code):
        lines = sorted(n for n in by_code[code] if n > 0)
        rule = rules_provider.get(code) if rules_provider is not None else None
        note = f" — {rule.summary or rule.title}" if rule is not None else ""
        shown = ", ".join(str(n) for n in lines[:10])
        more = "…" if len(lines) > 10 else ""
        loc = f" [строки {shown}{more}]" if lines else ""
        _cprint(f"  ⚐ {code} ×{len(by_code[code])}{loc}{note}", style="yellow")


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
        # issue #72/#356: подсказка по типу исключения + ссылка на глоссарий,
        # из общей базы карточек (bundled JSON → компактная карта fallback).
        entry = resolve_error_hint(result.error)
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
