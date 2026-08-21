"""issue #629: вывод решения не должен расти в памяти без границы.

Лимит был только у ``SandboxRunner``; дефолтный ``LocalRunner`` копил
stdout/stderr в список без предела, и решение с бесконечным ``print``
набивало RAM хоста за секунды таймаута — при пуле параллельных web-job'ов
это клало весь процесс по OOM.

Лимит применяется к НАКОПЛЕНИЮ, а не к чтению: дренаж продолжается, иначе
заполнится OS pipe-буфер и ребёнок зависнет на ``write`` до таймаута.

Второй заход закрыл два пути, где ``RunSpec`` собирается мимо ``grader_core``
и лимит не проставлялся вовсе (поле оставалось ``None`` → безлимитный
``communicate()``): пошаговый трейс (``core/tracer.py``) и микробенчмарк
(``core/microbench_runner.py``). У трейса дыра была сквозной — вывод программы
копился ещё и внутри дочернего процесса, в ``io.StringIO``, где ``max_steps``
объём не ограничивает (``print('x' * 10**9)`` — это один шаг).
"""

from __future__ import annotations

import dataclasses
import pathlib
import threading

from stepik_grader.config import CONFIG
from stepik_grader.core import tracer
from stepik_grader.core.microbench_runner import run_microbench
from stepik_grader.core.runner import LocalRunner, RunOutcome, RunSpec, _OutputBudget
from stepik_grader.core.tracer import trace_code

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
    """Чанк, пересекающий границу, обрезается по остатку бюджета.

    Переводов строки в этих данных нет, поэтому срез идёт ровно по остатку
    (issue #996, MTX-9-01: граница строки соблюдается там, где она есть).
    """
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


def test_no_limit_and_no_cancel_keeps_full_output(tmp_path: pathlib.Path) -> None:
    """Контроль: без лимита и без cancel вывод цел — потолка нет, обрезать нечего.

    issue #1248: этот случай обслуживал отдельный ``communicate()``; теперь путь
    общий, и проверка сторожит, что снятие лимита по-прежнему означает «без
    потолка», а не «другой сборщик вывода».
    """
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


# ---------------------------------------------------------------------------
# Пошаговый трейс: буфер stdout ВНУТРИ дочернего процесса (core/tracer.py)
# ---------------------------------------------------------------------------


def test_bounded_stdout_without_limit_keeps_everything() -> None:
    """``None`` — прежнее поведение голого ``io.StringIO``."""
    buf = tracer._BoundedStdout(None)

    buf.write("x" * 10_000)

    assert buf.getvalue() == "x" * 10_000
    assert buf.truncated is False


def test_bounded_stdout_clips_at_the_limit() -> None:
    """Запись через границу урезается, но программе рапортуется целиком."""
    buf = tracer._BoundedStdout(10)

    assert buf.write("abc") == 3
    # Полная длина в ответе — не косметика: короткий возврат из write() ловят
    # обёртки вывода в пользовательском коде, и трейс подменился бы ошибкой.
    assert buf.write("defghijklmno") == 12
    assert buf.getvalue() == "abcdefghij"
    assert buf.truncated is True


def test_bounded_stdout_survives_writes_after_exhaustion() -> None:
    """После исчерпания бюджета вызовы не падают и ничего не копят."""
    buf = tracer._BoundedStdout(4)

    buf.write("abcd")
    assert buf.write("efgh") == 4
    assert buf.getvalue() == "abcd"
    # tell() — источник step.stdout_len: он не должен уехать за длину stdout.
    assert buf.tell() == len(buf.getvalue()) == 4


def test_trace_caps_stdout_after_step_limit_is_spent() -> None:
    """``max_steps`` режет ЧИСЛО шагов, но не ОБЪЁМ вывода.

    Исчерпав лимит шагов, программа продолжает исполняться и печатать — до
    фикса буфер трассировщика рос дальше без границы (здесь это 2 МБ).
    """
    code = "for _ in range(2000):\n    print('x' * 1000)\ndone = True\n"

    result = tracer.run_trace(code, "<trace-test>", 10, 5_000)

    assert result["truncated"] is True  # шаги обрезаны на 10-м
    assert len(result["stdout"]) == 5_000  # вывод — 5 КБ вместо 2 МБ
    assert result["stdout_truncated"] is True
    assert result["error"] is None  # программа доработала штатно, без исключения


def test_trace_step_offsets_stay_inside_captured_stdout() -> None:
    """``step.stdout_len`` не уезжает за длину обрезанного stdout (срез плеера)."""
    code = "for i in range(50):\n    print('y' * 100)\n"

    result = tracer.run_trace(code, "<trace-test>", 500, 1_000)

    assert result["stdout_truncated"] is True
    assert result["steps"]
    assert all(step["stdout_len"] <= len(result["stdout"]) for step in result["steps"])


def test_trace_notes_truncation_in_stderr(capsys) -> None:
    """Пометка — в stderr, как на грейд-пути: stdout показывается пользователем."""
    tracer.run_trace("print('x' * 5000)\n", "<trace-test>", 100, 100)

    captured = capsys.readouterr()
    assert "вывод обрезан" in captured.err


def test_trace_without_limit_keeps_full_output() -> None:
    """Контроль: ``None`` — весь вывод цел, флаг не поднят."""
    result = tracer.run_trace("print('z' * 5000)\n", "<trace-test>", 100, None)

    assert len(result["stdout"]) == 5001  # 5000 символов + перевод строки
    assert result["stdout_truncated"] is False


def test_trace_code_passes_output_limit_to_runner(monkeypatch) -> None:
    """``RunSpec`` трассировщика несёт ``CONFIG.max_output_bytes``.

    Без него накопление stdout дочернего процесса — весь JSON-трейс — ничем не
    ограничено: ``None`` в ``max_output_bytes`` означает «без потолка».

    Патчится имя в самом ``tracer``: ``run_spec`` он импортирует напрямую,
    поэтому подмена в ``core.runner`` до него уже не доходит.
    """
    from stepik_grader.core import tracer

    specs: list[RunSpec] = []

    def spy(spec: RunSpec) -> RunOutcome:
        specs.append(spec)
        return RunOutcome()

    monkeypatch.setattr(tracer, "run_spec", spy)
    trace_code("x = 1\n")

    assert len(specs) == 1
    assert specs[0].max_output_bytes == CONFIG.max_output_bytes


def test_trace_code_end_to_end_keeps_child_output_bounded() -> None:
    """Сквозной прогон: болтливая программа не приносит свой вывод целиком."""
    code = "for _ in range(2000):\n    print('x' * 100)\n"

    trace = trace_code(code, max_steps=50, max_stdout_chars=10_000)

    assert trace["error"] is None
    assert trace["steps"]
    assert len(trace["stdout"]) == 10_000  # вместо ~200 КБ
    assert trace["stdout_truncated"] is True


# ---------------------------------------------------------------------------
# Микробенчмарк: stdout решения гасится в os.devnull, stderr — нет
# ---------------------------------------------------------------------------


def test_microbench_passes_output_limit_to_runner(monkeypatch) -> None:
    """``RunSpec`` микробенча несёт ``CONFIG.max_output_bytes`` (было ``None``)."""
    from stepik_grader.core import microbench_runner

    specs: list[RunSpec] = []

    def spy(spec: RunSpec) -> RunOutcome:
        specs.append(spec)
        return RunOutcome()

    monkeypatch.setattr(microbench_runner, "run_spec", spy)
    run_microbench("x = 1\n", number=1)

    assert len(specs) == 1
    assert specs[0].max_output_bytes == CONFIG.max_output_bytes


def test_microbench_caps_solution_stderr(monkeypatch) -> None:
    """Решение, льющее в stderr на каждом повторе, не набивает память хоста.

    stdout на время замера уходит в ``os.devnull`` (``_build_bench_script``),
    поэтому реально открыт был только stderr: 103 прогона (3 прогрева + 5×20)
    по 10 КБ — ~1 МБ, который безлимитный ``communicate()`` копил целиком.
    """
    from stepik_grader.core import microbench_runner

    monkeypatch.setattr(
        microbench_runner,
        "CONFIG",
        dataclasses.replace(microbench_runner.CONFIG, max_output_bytes=20_000),
    )
    seen: list[tuple[RunSpec, RunOutcome]] = []
    real_run_spec = microbench_runner.run_spec

    def spy(spec: RunSpec) -> RunOutcome:
        outcome = real_run_spec(spec)  # реальный LocalRunner, не мок
        seen.append((spec, outcome))
        return outcome

    monkeypatch.setattr(microbench_runner, "run_spec", spy)
    run_microbench("import sys\nsys.stderr.write('x' * 10000)\n", number=20)

    assert len(seen) == 1
    spec, outcome = seen[0]
    assert spec.max_output_bytes == 20_000
    # Бюджет общий на stdout+stderr; сверх лимита приезжает только пометка.
    assert len(outcome.stdout) + len(outcome.stderr) <= 20_000 + 200
    assert "вывод обрезан" in outcome.stderr.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# MTX-9-01 (issue #996) — обрезка не порождает псевдо-строку
# ---------------------------------------------------------------------------


def test_budget_cuts_at_a_line_boundary() -> None:
    """Остаток бюджета режется по последнему переводу строки, а не посреди неё.

    Обрыв на произвольном байте даёт огрызок, неотличимый от настоящей строки:
    сравнение видит не «вывод обрезан», а другое содержимое, и пользователь
    получает WA по строке, которой решение не печатало.
    """
    budget = _OutputBudget(10)

    # В бюджет влезли бы «раз\nдва\nтр», но «тр» — половина строки.
    assert budget.take("раз\nдва\nтри\n".encode()) == "раз\n".encode()
    assert budget.truncated is True


def test_budget_keeps_a_chunk_without_newlines() -> None:
    """Перевода строки в куске нет — отдаём срез: это лучше пустого вывода."""
    budget = _OutputBudget(4)

    assert budget.take(b"abcdefgh") == b"abcd"
    assert budget.truncated is True
