"""issue #629: вывод решения не должен расти в памяти без границы.

Лимит был только у ``SandboxRunner``; дефолтный ``LocalRunner`` копил
stdout/stderr в список без предела, и решение с бесконечным ``print``
набивало RAM хоста за секунды таймаута — при пуле параллельных web-job'ов
это клало весь процесс по OOM.

Лимит применяется к НАКОПЛЕНИЮ, а не к чтению: дренаж продолжается, иначе
заполнится OS pipe-буфер и ребёнок зависнет на ``write`` до таймаута.
"""

from __future__ import annotations

import pathlib
import threading

from stepik_grader.core.runner import LocalRunner, RunSpec, _OutputBudget

# ---------------------------------------------------------------------------
# Бюджет накопления
# ---------------------------------------------------------------------------


def test_budget_without_limit_passes_everything() -> None:
    """``None`` — прежнее поведение, без ограничения."""
    budget = _OutputBudget(None)

    assert budget.take(b"x" * 1000) == b"x" * 1000
    assert budget.take(b"y" * 1000) == b"y" * 1000
    assert budget.truncated is False


def test_budget_splits_chunk_at_the_boundary() -> None:
    """Чанк, пересекающий границу, обрезается ровно по остатку бюджета."""
    budget = _OutputBudget(10)

    assert budget.take(b"abc") == b"abc"
    assert budget.take(b"defghijklmno") == b"defghij"  # осталось 7 байт
    assert budget.truncated is True


def test_budget_is_exhausted_after_limit() -> None:
    """После исчерпания возвращается пустой срез — но вызовы не падают."""
    budget = _OutputBudget(4)

    assert budget.take(b"abcd") == b"abcd"
    assert budget.take(b"efgh") == b""
    assert budget.take(b"ijkl") == b""
    assert budget.truncated is True


def test_budget_is_shared_between_streams() -> None:
    """stdout и stderr делят общий бюджет — иначе лимит обходится вдвое."""
    budget = _OutputBudget(10)

    assert budget.take(b"x" * 8) == b"x" * 8  # «stdout»
    assert budget.take(b"y" * 8) == b"yy"  # «stderr» — остался лишь хвост
    assert budget.truncated is True


def test_exact_fit_does_not_mark_truncated() -> None:
    """Ровно уместившийся вывод не помечается обрезанным."""
    budget = _OutputBudget(5)

    assert budget.take(b"abcde") == b"abcde"
    assert budget.truncated is False


# ---------------------------------------------------------------------------
# Интеграция: реальный процесс на poll-пути (его использует web)
# ---------------------------------------------------------------------------


def _loud_script(tmp_path: pathlib.Path) -> pathlib.Path:
    script = tmp_path / "loud.py"
    script.write_text("for _ in range(20000):\n    print('x' * 100)\n", encoding="utf-8")
    return script


def test_polling_path_caps_output_and_marks_truncation(tmp_path: pathlib.Path) -> None:
    """Вывод обрезан по лимиту, в stderr — пометка, процесс дожил до конца."""
    spec = RunSpec(
        path=_loud_script(tmp_path),
        stdin=None,
        timeout=60.0,
        measure_memory=False,
        max_output_bytes=10_000,
        # cancel_event переводит LocalRunner на poll-путь с дренажем — именно
        # его использует web (runs.py/playground.py), где риск OOM реален.
        cancel_event=threading.Event(),
    )

    outcome = LocalRunner().run(spec)

    assert len(outcome.stdout) <= 10_000, "накопление превысило лимит"
    assert "вывод обрезан" in outcome.stderr.decode("utf-8", errors="replace")
    # Процесс НЕ убит: дренаж продолжался, поэтому решение доработало штатно.
    assert outcome.timed_out is False
    assert outcome.returncode == 0


def test_polling_path_without_limit_keeps_full_output(tmp_path: pathlib.Path) -> None:
    """Контроль: без лимита приходит весь вывод — обрезает именно бюджет."""
    script = tmp_path / "chatty.py"
    script.write_text("for _ in range(200):\n    print('y' * 100)\n", encoding="utf-8")
    spec = RunSpec(
        path=script,
        stdin=None,
        timeout=60.0,
        measure_memory=False,
        max_output_bytes=None,
        cancel_event=threading.Event(),
    )

    outcome = LocalRunner().run(spec)

    assert len(outcome.stdout) >= 200 * 100
    assert "вывод обрезан" not in outcome.stderr.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Синхронный/CLI путь (без cancel_event): communicate → bounded poll при лимите
# (issue #629 — раньше капился только web-poll-путь с cancel_event, а
# proc.communicate() читал stdout решения в память без предела → OOM хоста)
# ---------------------------------------------------------------------------


def test_sync_path_caps_output_when_limited(tmp_path: pathlib.Path) -> None:
    """Без cancel_event, но с лимитом — вывод капится (путь CLI/синхронного /api/grade)."""
    spec = RunSpec(
        path=_loud_script(tmp_path),
        stdin=None,
        timeout=60.0,
        measure_memory=False,
        max_output_bytes=10_000,
        # НЕТ cancel_event — раньше это шло в communicate() без предела.
    )

    outcome = LocalRunner().run(spec)

    assert len(outcome.stdout) <= 10_000, "накопление превысило лимит на sync-пути"
    assert "вывод обрезан" in outcome.stderr.decode("utf-8", errors="replace")
    # Процесс НЕ убит: дренаж продолжался, решение доработало штатно.
    assert outcome.timed_out is False
    assert outcome.returncode == 0


def test_fast_path_without_limit_or_cancel_keeps_full_output(tmp_path: pathlib.Path) -> None:
    """Контроль: без лимита и без cancel — быстрый communicate, весь вывод цел."""
    script = tmp_path / "chatty.py"
    script.write_text("for _ in range(200):\n    print('y' * 100)\n", encoding="utf-8")
    spec = RunSpec(
        path=script,
        stdin=None,
        timeout=60.0,
        measure_memory=False,
        max_output_bytes=None,
    )

    outcome = LocalRunner().run(spec)

    assert len(outcome.stdout) >= 200 * 100
    assert "вывод обрезан" not in outcome.stderr.decode("utf-8", errors="replace")
    assert outcome.returncode == 0
