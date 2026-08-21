"""Tests for core/runner.py — Runner Protocol + LocalRunner (issue #136/#139).

Two halves:
- _apply_memory_limit / _measure_peak_memory unit tests, migrated from
  test_grader_core.py after the subprocess-execution code moved into
  core/runner.py (issue #136/#137/#138) — monkeypatch targets updated to the
  runner module, since that's where `resource`/`psutil` now actually live.
- LocalRunner.run() scenario tests (issue #139): timeout, a script living in
  a temp directory, stdout/stderr capture, and launch failure (OSError).
"""

from __future__ import annotations

import contextlib
import pathlib
import sys
import tempfile
import threading
import time
import warnings
from typing import Any

import pytest

from stepik_grader.core.runner import LocalRunner, RunSpec, spec_source_bytes

# ---------------------------------------------------------------------------
# _apply_memory_limit — best-effort RLIMIT_AS cap via prlimit (issue #67, #43 S-01)
# ---------------------------------------------------------------------------


def test_apply_memory_limit_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_memory_mb=None → prlimit не вызывается."""
    from stepik_grader.core import runner

    calls: list[tuple] = []

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(*args):
            calls.append(args)

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(123, None)
    assert calls == []


def test_apply_memory_limit_noop_when_resource_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no `resource` module — must degrade to a no-op, not crash."""
    from stepik_grader.core import runner

    monkeypatch.setattr(runner, "resource", None)
    runner._apply_memory_limit(123, 1024)  # must not raise


def test_apply_memory_limit_calls_prlimit_with_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """prlimit получает (pid, RLIMIT_AS, (bytes, bytes)), bytes = mb * 1024**2."""
    from stepik_grader.core import runner

    calls: list[tuple] = []

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(pid, which, limits):
            calls.append((pid, which, limits))

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(4321, 64)

    expected_bytes = 64 * 1024 * 1024
    assert calls == [(4321, "RLIMIT_AS", (expected_bytes, expected_bytes))]


@pytest.mark.parametrize(
    "exc",
    [
        AttributeError("no prlimit on macOS"),
        ValueError("invalid limit"),
        OSError("no such process"),
    ],
)
def test_apply_memory_limit_swallows_prlimit_failure(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """prlimit отсутствует на macOS (AttributeError) / нет процесса-прав (OSError)
    — не должно падать, просто пропускаем cap (issue #67)."""
    from stepik_grader.core import runner

    class _FakeResource:
        RLIMIT_AS = "RLIMIT_AS"

        @staticmethod
        def prlimit(*args):
            raise exc

    monkeypatch.setattr(runner, "resource", _FakeResource)
    runner._apply_memory_limit(1, 64)  # must not raise


# ---------------------------------------------------------------------------
# _measure_peak_memory — warn on unreliable reading (Issue #48 R-05)
# ---------------------------------------------------------------------------


class _FakeProc:
    pid = 999999


def test_measure_peak_memory_warns_on_process_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil.Process(pid) itself raising NoSuchProcess -- outer except branch."""
    import psutil

    from stepik_grader.core import runner

    def _raise(_pid: int) -> None:
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(runner.psutil, "Process", _raise)

    result: list[float] = [0.0]
    stop = threading.Event()
    stop.set()

    with pytest.warns(UserWarning, match="unreliable"):
        runner._measure_peak_memory(_FakeProc(), result, stop)

    assert result[0] == 0.0


def test_measure_peak_memory_warns_on_first_sample_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil.Process() succeeds but the immediate first memory_info() sample
    fails -- inner except branch around the pre-loop read."""
    import psutil

    from stepik_grader.core import runner

    class _FakePsutilProcess:
        def memory_info(self) -> None:
            raise psutil.NoSuchProcess(999999)

    monkeypatch.setattr(runner.psutil, "Process", lambda pid: _FakePsutilProcess())

    result: list[float] = [0.0]
    stop = threading.Event()
    stop.set()

    with pytest.warns(UserWarning, match="unreliable"):
        runner._measure_peak_memory(_FakeProc(), result, stop)

    assert result[0] == 0.0


def test_measure_peak_memory_unreliable_warning_deduped_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls (batch grading many trivially-fast solutions) warn once,
    not once per call -- the message text is constant so Python's own
    "default" warning filter suppresses repeats within the same interpreter
    session (issue: pid-specific text used to defeat this dedup)."""
    import psutil

    from stepik_grader.core import runner

    def _raise(_pid: int) -> None:
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(runner.psutil, "Process", _raise)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for _ in range(5):
            result: list[float] = [0.0]
            stop = threading.Event()
            stop.set()
            runner._measure_peak_memory(_FakeProc(), result, stop)

    unreliable = [w for w in caught if "unreliable" in str(w.message)]
    assert len(unreliable) == 1, unreliable


def test_measure_peak_memory_is_published_before_the_thread_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пик виден вызывающей стороне ПОКА поток ещё работает (issue #996).

    Замер копился в локальной переменной и попадал в ``result`` одной строкой
    на выходе из функции — а выход наступает только после ``stop.set()``,
    который ``LocalRunner`` ставит уже ПОСЛЕ сборки ``RunOutcome``. Вердикт
    поэтому читал ноль всегда, при любой памяти решения. Заглушка запоминает,
    что видел бы вызывающий в момент каждого замера: со второго он обязан
    видеть пик, а не ноль.
    """
    import types

    from stepik_grader.core import runner

    result: list[float] = [0.0]
    stop = threading.Event()
    seen_by_caller: list[float] = []
    samples = iter([12.0, 48.0])

    class _Sampled:
        def memory_info(self) -> Any:
            seen_by_caller.append(result[0])
            rss_mb = next(samples, None)
            if rss_mb is None:  # замеры кончились — так же обрывается и реальный процесс
                stop.set()
                rss_mb = 0.0
            return types.SimpleNamespace(rss=int(rss_mb * 1024 * 1024))

        def children(self, recursive: bool = False) -> list[Any]:
            return []

    monkeypatch.setattr(runner.psutil, "Process", lambda pid: _Sampled())

    runner._measure_peak_memory(_FakeProc(), result, stop)

    assert seen_by_caller[0] == 0.0, "до первого замера показывать нечего"
    assert seen_by_caller[1] == pytest.approx(12.0), (
        "первый замер обязан быть опубликован сразу: RunOutcome читает result[0] "
        "до того, как поток остановлен"
    )
    assert result[0] == pytest.approx(48.0), "в result остаётся максимум, а не последний замер"


def test_local_runner_reports_peak_memory_of_a_hungry_solution(
    tmp_path: pathlib.Path,
) -> None:
    """Сквозной путь: память, занятая решением, доходит до ``RunOutcome``.

    Модульного теста мало — дефект был в стыке потока и сборки результата, а
    не в самом замере: ``_run_with_polling`` читает ``result[0]`` раньше, чем
    поток успевал что-либо туда записать. Прогон целиком отдавал 0.0 на
    решении, занявшем 24 МБ.
    """
    path = tmp_path / "hungry.py"
    path.write_text(
        "import time\ndata = bytearray(24 * 1024 * 1024)\ntime.sleep(0.2)\nprint(len(data))\n",
        encoding="utf-8",
    )
    spec = RunSpec(path=path, stdin=None, timeout=30.0, measure_memory=True)

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is False
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.peak_memory_mb > 0.0, "нулевой пик у живого процесса — это потерянный замер"
    assert outcome.peak_memory_mb > 20.0, (
        "24 МБ, занятые решением, обязаны попасть в пик, а не потеряться в округлении"
    )


def test_measure_peak_memory_does_not_walk_the_tree_every_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #996 (MTX-6-04): дерево процессов обходится редко, память — часто.

    ``children(recursive=True)`` перебирает таблицу процессов целиком и стоит
    на порядок дороже одного ``memory_info()``. На опросе каждые 20 мс обход
    съедал заметную долю времени решения — а показывает это время грейдер как
    время решения. Список потомков поэтому живёт полсекунды; сама память
    по-прежнему читается на каждой итерации, иначе пик стал бы грубее.
    """
    import types

    from stepik_grader.core import runner

    walks = 0
    reads = 0
    stop = threading.Event()

    class _Counting:
        def memory_info(self) -> Any:
            nonlocal reads
            reads += 1
            if reads >= 6:  # шесть опросов памяти — и хватит
                stop.set()
            return types.SimpleNamespace(rss=1024 * 1024)

        def children(self, recursive: bool = False) -> list[Any]:
            nonlocal walks
            walks += 1
            return []

    monkeypatch.setattr(runner.psutil, "Process", lambda pid: _Counting())

    # Период опроса намеренно НЕ подменяется: шесть настоящих итераций по
    # 20 мс укладываются в 0.12 с, то есть в один срок жизни кэша. Подмена
    # константы сделала бы тест красным даже там, где дефект ни при чём —
    # достаточно переименовать константу.
    runner._measure_peak_memory(_FakeProc(), [0.0], stop)

    assert reads >= 6, "память обязана читаться на каждой итерации"
    assert walks == 1, f"дерево обошли {walks} раз(а) на {reads} замеров — кэш потомков не работает"


# ---------------------------------------------------------------------------
# sample_tree_rss — суммарный RSS процесса + потомков (issue #556)
# ---------------------------------------------------------------------------


class _FakePsProc:
    """Мини-заглушка ``psutil.Process`` для табличных тестов ``sample_tree_rss``."""

    def __init__(
        self,
        rss_mb: float,
        *,
        children: list[_FakePsProc] | None = None,
        raise_mem: Exception | None = None,
        raise_children: Exception | None = None,
    ) -> None:
        self._rss_mb = rss_mb
        self._children = children or []
        self._raise_mem = raise_mem
        self._raise_children = raise_children

    def memory_info(self) -> Any:
        import types

        if self._raise_mem is not None:
            raise self._raise_mem
        return types.SimpleNamespace(rss=int(self._rss_mb * 1024 * 1024))

    def children(self, recursive: bool = False) -> list[_FakePsProc]:
        if self._raise_children is not None:
            raise self._raise_children
        return self._children


def test_sample_tree_rss_sums_self_and_children() -> None:
    from stepik_grader.core.runner import sample_tree_rss

    root = _FakePsProc(10.0, children=[_FakePsProc(3.0), _FakePsProc(7.0)])
    assert sample_tree_rss(root) == pytest.approx(20.0)


def test_sample_tree_rss_skips_vanished_child_without_zeroing_total() -> None:
    """Исчезнувший в момент обхода потомок пропускается, а не обнуляет итог."""
    import psutil

    from stepik_grader.core.runner import sample_tree_rss

    root = _FakePsProc(
        10.0,
        children=[_FakePsProc(5.0), _FakePsProc(0.0, raise_mem=psutil.NoSuchProcess(1))],
    )
    assert sample_tree_rss(root) == pytest.approx(15.0)


def test_sample_tree_rss_children_error_returns_self_only() -> None:
    import psutil

    from stepik_grader.core.runner import sample_tree_rss

    root = _FakePsProc(10.0, raise_children=psutil.AccessDenied())
    assert sample_tree_rss(root) == pytest.approx(10.0)


def test_sample_tree_rss_root_error_propagates() -> None:
    """Ошибку самого корня НЕ глотаем — это сигнал вызывающей стороне (warn/обрыв)."""
    import psutil

    from stepik_grader.core.runner import sample_tree_rss

    root = _FakePsProc(0.0, raise_mem=psutil.NoSuchProcess(1))
    with pytest.raises(psutil.NoSuchProcess):
        sample_tree_rss(root)


@pytest.mark.skipif(
    sys.platform == "win32", reason="видимость памяти внука с хоста проверяем на POSIX"
)
def test_sample_tree_rss_includes_grandchild_real_process(tmp_path: pathlib.Path) -> None:
    """issue #556 суть: с хоста память внука видна в поддереве, но не в замере
    одного корневого pid. Родитель спавнит внука, аллоцирующего ~60 МБ; ждём,
    пока внук выделит память, и сверяем tree-RSS против own-RSS корня."""
    import subprocess

    import psutil

    from stepik_grader.core.runner import sample_tree_rss

    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', "
        "'buf = bytearray(60 * 1024 * 1024)\\nimport time; time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        ps = psutil.Process(proc.pid)
        tree = own = 0.0
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            try:
                own = ps.memory_info().rss / 1024 / 1024
                tree = sample_tree_rss(ps)
            except psutil.Error:
                break
            if tree - own > 40:
                break
            time.sleep(0.1)
        assert tree - own > 40, f"внук не учтён в дереве: tree={tree:.1f} own={own:.1f}"
    finally:
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# LocalRunner.run() — end-to-end scenarios (issue #139)
# ---------------------------------------------------------------------------


def _write_script(tmp_path: pathlib.Path, body: str, name: str = "sol.py") -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_local_runner_captures_stdout_and_returncode(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print('hello')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is None
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "hello"


def test_local_runner_captures_stderr_on_nonzero_exit(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is None
    assert outcome.returncode == 1
    assert outcome.stderr.decode().strip() == "boom"


def test_local_runner_passes_stdin_through(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(path=path, stdin=b"4\n", timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode().strip() == "5"


def test_local_runner_script_in_nested_tempdir(tmp_path: pathlib.Path) -> None:
    # issue #139 "tempdir" scenario: script path lives in a fresh temp
    # directory tree (same situation as function-mode's NamedTemporaryFile
    # wrapper in grader_core.run_single_test), not just a bare filename.
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    path = _write_script(nested, "print('deep')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "deep"


def test_local_runner_times_out(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "import time; time.sleep(100)\n")
    spec = RunSpec(path=path, stdin=None, timeout=0.1, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert outcome.elapsed == 0.1
    assert outcome.launch_error is None


def test_local_runner_launch_error_is_captured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Точечно ломаем сам spawn (не поведение решения) -- monkeypatch на
    # несуществующий интерпретатор, чтобы Popen поднял OSError/FileNotFoundError.
    from stepik_grader.core import runner

    monkeypatch.setattr(runner.sys, "executable", str(tmp_path / "no-such-python-binary"))
    path = _write_script(tmp_path, "print(1)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is not None
    assert outcome.timed_out is False


def test_local_runner_measures_peak_memory_when_enabled(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "x = [0] * 1000\nprint(len(x))\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=True)

    outcome = LocalRunner().run(spec)

    assert outcome.returncode == 0
    # peak_memory_mb может быть 0.0 на очень короткоживущих процессах (issue
    # #48 R-05, best-effort) -- проверяем только, что замер не сломал запуск.
    assert outcome.peak_memory_mb >= 0.0


def test_local_runner_skips_memory_measurement_when_disabled(tmp_path: pathlib.Path) -> None:
    path = _write_script(tmp_path, "print('ok')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.peak_memory_mb == 0.0


def test_run_spec_defaults() -> None:
    spec = RunSpec(path="x.py", stdin=None, timeout=5.0)
    assert spec.measure_memory is True
    assert spec.max_memory_mb is None
    assert spec.cancel_event is None


# ---------------------------------------------------------------------------
# issue #638: сериализуемый RunSpec — code вместо/помимо локального пути
# ---------------------------------------------------------------------------


def test_run_spec_requires_path_or_code() -> None:
    """RunSpec без path и без code — ValueError: нечего исполнять (#638)."""
    with pytest.raises(ValueError, match="path.*code"):
        RunSpec(stdin=None, timeout=5.0)


def test_spec_source_bytes_prefers_code(tmp_path: pathlib.Path) -> None:
    """spec_source_bytes отдаёт code (переносимо на remote), даже если задан path (#638)."""
    f = tmp_path / "s.py"
    f.write_bytes(b"from-disk")
    assert spec_source_bytes(RunSpec(path=f, code=b"inline", stdin=None, timeout=5.0)) == b"inline"


def test_spec_source_bytes_reads_path(tmp_path: pathlib.Path) -> None:
    """Без code spec_source_bytes читает байты локального path (#638)."""
    f = tmp_path / "s.py"
    f.write_bytes(b"print(1)\n")
    assert spec_source_bytes(RunSpec(path=f, stdin=None, timeout=5.0)) == b"print(1)\n"


def test_local_runner_executes_inline_code() -> None:
    """LocalRunner материализует spec.code во временный .py и исполняет его БЕЗ
    локального path — доказывает сериализуемость ядра RunSpec (#638)."""
    spec = RunSpec(code=b"print('from-code')\n", stdin=None, timeout=5.0, measure_memory=False)
    outcome = LocalRunner().run(spec)
    assert outcome.returncode == 0
    assert b"from-code" in outcome.stdout


def test_local_runner_code_spec_uses_private_dir() -> None:
    """issue #799 (SECC-01): скрипт лежит в приватном каталоге, а не в общем temp.

    Каталог скрипта CPython ставит ПЕРВЫМ в `sys.path`. Пока это был общий
    системный temp, посторонний на многопользовательском хосте мог заранее
    положить туда `json.py` — и `import json` в решении подхватил бы чужой код
    правами владельца грейдера. Права файла (0600) не спасают: атака идёт на
    каталог.

    Проверяем по факту: решение печатает свой `sys.path[0]`, и этот каталог не
    должен совпадать с общим `tempfile.gettempdir()`.

    Пути сравниваются РЕЗОЛВЛЕННЫМИ: на macOS `/var` — симлинк на `/private/var`,
    поэтому дочерний процесс печатает `/private/var/folders/...`, а
    `gettempdir()` отдаёт `/var/folders/...` — сравнение «как есть» падало бы на
    ровном месте.
    """
    spec = RunSpec(
        code=b"import sys; print(sys.path[0])\n",
        stdin=None,
        timeout=5.0,
        measure_memory=False,
    )
    outcome = LocalRunner().run(spec)

    script_dir = pathlib.Path(outcome.stdout.decode().strip())
    shared_temp = pathlib.Path(tempfile.gettempdir()).resolve()
    assert script_dir != shared_temp, "скрипт материализован прямо в общий temp"
    assert script_dir.parent.resolve() == shared_temp, "приватный каталог создаётся внутри temp"


def test_local_runner_code_spec_cleans_up_private_dir() -> None:
    """Приватный каталог удаляется целиком — временные файлы не копятся."""
    spec = RunSpec(
        code=b"import sys; print(sys.path[0])\n",
        stdin=None,
        timeout=5.0,
        measure_memory=False,
    )
    outcome = LocalRunner().run(spec)

    script_dir = pathlib.Path(outcome.stdout.decode().strip())
    assert not script_dir.exists(), "приватный каталог остался после прогона"


def test_local_runner_code_spec_reads_stdin() -> None:
    """code-spec получает stdin по полному пути исполнения (не только запуск, #638)."""
    spec = RunSpec(
        code=b"import sys; sys.stdout.write(sys.stdin.read().upper())\n",
        stdin=b"hi\n",
        timeout=5.0,
        measure_memory=False,
    )
    outcome = LocalRunner().run(spec)
    assert b"HI" in outcome.stdout


def test_run_outcome_defaults() -> None:
    from stepik_grader.core.runner import RunOutcome

    outcome = RunOutcome()
    assert outcome.stdout == b""
    assert outcome.stderr == b""
    assert outcome.returncode == 0
    assert outcome.elapsed == 0.0
    assert outcome.peak_memory_mb == 0.0
    assert outcome.timed_out is False
    assert outcome.launch_error is None
    assert outcome.cancelled is False
    assert outcome.sandbox_violation is None


# ---------------------------------------------------------------------------
# LocalRunner.run() — cancel_event poll-loop (issue #262)
# ---------------------------------------------------------------------------


def test_local_runner_cancel_event_none_matches_prior_behavior(tmp_path: pathlib.Path) -> None:
    """cancel_event=None (default) — identical to the pre-#262 blocking path."""
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(path=path, stdin=b"4\n", timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is False
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "5"


def test_local_runner_cancel_event_not_triggered_runs_to_completion(
    tmp_path: pathlib.Path,
) -> None:
    """cancel_event supplied but never set -- the poll path must still
    produce a correct result (proves the concurrent stdout/stderr drain
    threads work, not just the early-exit branch)."""
    path = _write_script(tmp_path, "print(int(input()) + 1)\n")
    spec = RunSpec(
        path=path, stdin=b"41\n", timeout=5.0, measure_memory=False, cancel_event=threading.Event()
    )

    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is False
    assert outcome.timed_out is False
    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "42"


def test_local_runner_cancel_event_stops_process_early(tmp_path: pathlib.Path) -> None:
    """Cancelling mid-run kills the child well before its full sleep duration
    (proves the 100ms poll loop actually observes the event, not just the
    timeout budget)."""
    path = _write_script(tmp_path, "import time; time.sleep(30)\n")
    cancel_event = threading.Event()
    spec = RunSpec(
        path=path, stdin=None, timeout=60.0, measure_memory=False, cancel_event=cancel_event
    )

    def _cancel_soon() -> None:
        cancel_event.wait(0.3)
        cancel_event.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    outcome = LocalRunner().run(spec)

    assert outcome.cancelled is True
    assert outcome.timed_out is False
    assert outcome.elapsed < 5.0  # well under the 30s sleep and 60s timeout


def test_local_runner_cancel_event_supplied_but_timeout_wins(tmp_path: pathlib.Path) -> None:
    """cancel_event supplied but never set and the child overruns timeout --
    the poll path's own timeout branch must still fire (not just cancel)."""
    path = _write_script(tmp_path, "import time; time.sleep(30)\n")
    spec = RunSpec(
        path=path, stdin=None, timeout=0.2, measure_memory=False, cancel_event=threading.Event()
    )

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert outcome.cancelled is False
    assert outcome.elapsed == 0.2


def test_local_runner_satisfies_runner_protocol() -> None:
    from stepik_grader.core.runner import Runner

    assert isinstance(LocalRunner(), Runner)


# ---------------------------------------------------------------------------
# LocalRunner.run() — TLE/cancel не должны зависать и терять вывод
# (issue #418 группа процессов, #419 stdin-deadlock, #421 частичный вывод)
# ---------------------------------------------------------------------------


def test_local_runner_returns_partial_output_on_timeout(tmp_path: pathlib.Path) -> None:
    """issue #421: вывод, напечатанный до TLE, доступен в outcome.stdout,
    а не выбрасывается."""
    path = _write_script(
        tmp_path,
        "import sys, time\nsys.stdout.write('partial\\n')\nsys.stdout.flush()\ntime.sleep(30)\n",
    )
    spec = RunSpec(path=path, stdin=None, timeout=0.3, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.timed_out is True
    assert b"partial" in outcome.stdout


def test_local_runner_timeout_does_not_hang_on_orphan_holding_pipe(
    tmp_path: pathlib.Path,
) -> None:
    """issue #418: решение спавнит внука, наследующего stdout, и оба висят.
    Без убийства группы процессов communicate() зависла бы на открытом pipe
    внука — run() должен вернуться за ~timeout, а не за время сна внука."""
    path = _write_script(
        tmp_path,
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n",
    )
    spec = RunSpec(path=path, stdin=None, timeout=0.3, measure_memory=False)

    t0 = time.perf_counter()
    outcome = LocalRunner().run(spec)
    wall = time.perf_counter() - t0

    assert outcome.timed_out is True
    assert wall < 8.0, f"зависание на pipe внука: {wall:.1f}s"


def test_local_runner_polling_large_stdin_non_reading_child_times_out(
    tmp_path: pathlib.Path,
) -> None:
    """issue #419: ребёнок не читает stdin и висит; большой stdin переполнил бы
    pipe-буфер и заблокировал бы синхронную запись до входа в poll-цикл. С
    записью stdin в отдельном потоке timeout/cancel всё равно срабатывают."""
    path = _write_script(tmp_path, "import time\ntime.sleep(30)\n")
    big_stdin = b"x" * (1024 * 1024)  # 1 MiB — заведомо больше pipe-буфера (~64 KiB)
    spec = RunSpec(
        path=path,
        stdin=big_stdin,
        timeout=0.3,
        measure_memory=False,
        cancel_event=threading.Event(),
    )

    t0 = time.perf_counter()
    outcome = LocalRunner().run(spec)
    wall = time.perf_counter() - t0

    assert outcome.timed_out is True
    assert wall < 8.0, f"deadlock записи stdin в главном потоке: {wall:.1f}s"


def test_sys_executable_used_for_interpreter(tmp_path: pathlib.Path) -> None:
    # Убедимся, что раннер использует ТОТ ЖЕ интерпретатор, что и текущий
    # процесс (важно для venv на Windows) -- не системный "python"/"python3".
    path = _write_script(tmp_path, "import sys; print(sys.executable)\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode().strip() == sys.executable


# ---------------------------------------------------------------------------
# Parity с удалённым tests/test_executor.py (issue #406 retire): поведение,
# которое раньше проверялось на executor.run_solution, теперь — на LocalRunner.
# ---------------------------------------------------------------------------


def test_local_runner_syntax_error_surfaces_in_stderr(tmp_path: pathlib.Path) -> None:
    """Невалидный синтаксис решения → ненулевой returncode + SyntaxError в stderr."""
    path = _write_script(tmp_path, "def broken(\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.launch_error is None
    assert outcome.returncode != 0
    assert b"SyntaxError" in outcome.stderr


def test_local_runner_forces_utf8_for_cyrillic_output(tmp_path: pathlib.Path) -> None:
    """LocalRunner форсит PYTHONIOENCODING/PYTHONUTF8 ребёнку — кириллица в
    stdout не ломается даже там, где системная локаль cp1251 (Windows)."""
    path = _write_script(tmp_path, "print('Привет, мир')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.returncode == 0
    assert outcome.stdout.decode("utf-8").strip() == "Привет, мир"


def test_local_runner_disables_ansi_traceback_colors(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #726: цветной traceback родителя не протекает в stderr решения.

    Python 3.13+ красит traceback при FORCE_COLOR/PYTHON_COLORS=1 в окружении —
    даже когда stderr это pipe. Унаследованный цвет доезжал сырыми ANSI-кодами
    до web-таблицы, поэтому LocalRunner гасит его явным PYTHON_COLORS=0.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")
    path = _write_script(tmp_path, "raise ValueError('boom')\n")
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    stderr = outcome.stderr.decode("utf-8")
    assert outcome.returncode != 0
    assert "ValueError: boom" in stderr
    assert "\x1b[" not in stderr


def test_local_runner_sets_python_colors_off_in_child_env(tmp_path: pathlib.Path) -> None:
    """Переменные гашения цвета реально доходят до дочернего процесса (#726)."""
    path = _write_script(
        tmp_path,
        "import os\nprint(os.environ.get('PYTHON_COLORS'), os.environ.get('NO_COLOR'))\n",
    )
    spec = RunSpec(path=path, stdin=None, timeout=5.0, measure_memory=False)

    outcome = LocalRunner().run(spec)

    assert outcome.stdout.decode("utf-8").strip() == "0 1"
