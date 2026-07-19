"""Tests for core/tracer.py — пошаговый трейс исполнения (issue #318).

``trace_code`` исполняет self-contained bootstrap через активный
grader_core._RUNNER (issue #396) — тесты гоняют реальную трассировку (не мок),
проверяя структуру JSON-трейса: кадры стека, heap со ссылками по id (aliasing),
события, лимиты, stdin.
"""

from __future__ import annotations

import io
import json
import sys

from stepik_grader.core import tracer as t
from stepik_grader.core.tracer import DEFAULT_MAX_STEPS, trace_code


def _globals(step: dict) -> dict:
    """Локальные имена внешнего (module) кадра шага."""
    return step["stack"][0]["locals"]


def test_assignments_produce_steps() -> None:
    trace = trace_code("x = 1\ny = 2\nz = x + y\n")
    assert trace["error"] is None
    assert len(trace["steps"]) >= 3
    last = trace["steps"][-1]
    assert _globals(last)["z"] == 3


def test_aliasing_shared_reference() -> None:
    # a и b указывают на один список — одинаковый ref; мутация видна обоим.
    trace = trace_code("a = [1, 2]\nb = a\nb.append(3)\n")
    last = trace["steps"][-1]
    gl = _globals(last)
    assert gl["a"] == gl["b"]  # один и тот же {"ref": ...}
    ref = gl["a"]["ref"]
    obj = last["heap"][ref]
    assert obj["kind"] == "seq"
    assert obj["n"] == 3


def test_nested_structure_in_heap() -> None:
    trace = trace_code('m = {"xs": [1, 2]}\n')
    last = trace["steps"][-1]
    m_ref = _globals(last)["m"]["ref"]
    m_obj = last["heap"][m_ref]
    assert m_obj["kind"] == "map"
    # значение по ключу — ссылка на вложенный список, тоже в heap
    _, val_ref = m_obj["entries"][0]
    assert "ref" in val_ref
    assert last["heap"][val_ref["ref"]]["kind"] == "seq"


def test_function_call_adds_stack_frame() -> None:
    trace = trace_code("def f(n):\n    return n + 1\nx = f(5)\n")
    depths = [len(s["stack"]) for s in trace["steps"]]
    assert max(depths) == 2  # module + f
    # во время вызова во внутреннем кадре виден параметр n
    call_steps = [s for s in trace["steps"] if len(s["stack"]) == 2]
    assert any(s["stack"][1]["locals"].get("n") == 5 for s in call_steps)
    assert _globals(trace["steps"][-1])["x"] == 6


def test_loop_and_stdout_growth() -> None:
    trace = trace_code("for i in range(3):\n    print(i)\n")
    assert trace["stdout"] == "0\n1\n2\n"
    # stdout_len монотонно не убывает по шагам
    lens = [s["stdout_len"] for s in trace["steps"]]
    assert lens == sorted(lens)


def test_exception_recorded() -> None:
    trace = trace_code("x = 1\ny = x / 0\n")
    assert trace["error"]["type"] == "ZeroDivisionError"
    assert any(s["event"] == "exception" for s in trace["steps"])


def test_step_limit_truncates() -> None:
    trace = trace_code("i = 0\nwhile i < 100000:\n    i += 1\n", max_steps=40)
    assert trace["truncated"] is True
    assert len(trace["steps"]) == 40


def test_stdin_is_fed_to_program() -> None:
    trace = trace_code("n = int(input())\nprint(n * 2)\n", stdin="21")
    assert trace["stdout"] == "42\n"


def test_trace_is_valid_json_with_non_finite_floats() -> None:
    # inf/nan невалидны в JSON — кодировщик сводит их к строкам; трейс
    # сериализуется/парсится без ошибок (allow_nan=False внутри).
    trace = trace_code("a = float('inf')\nb = float('nan')\n")
    dumped = json.dumps(trace, allow_nan=False)  # не должно бросить
    assert json.loads(dumped)["error"] is None


def test_default_max_steps_is_positive() -> None:
    assert DEFAULT_MAX_STEPS > 0


# ---------------------------------------------------------------------------
# Прямые (in-process) тесты кодировщиков и _Tracer — большая часть tracer.py
# исполняется в subprocess, где coverage её не видит; эти тесты покрывают ядро.
# ---------------------------------------------------------------------------


class TestEncode:
    def test_primitives_inline(self) -> None:
        heap: dict = {}
        assert t._encode(None, heap) is None
        assert t._encode(True, heap) is True
        assert t._encode(7, heap) == 7
        assert t._encode("hi", heap) == "hi"
        assert heap == {}  # примитивы не идут в heap

    def test_big_int_and_non_finite_float(self) -> None:
        assert t._encode(2**70, {}) == {"big": repr(2**70)}
        assert t._encode(float("inf"), {}) == "inf"
        assert t._encode(float("nan"), {}) == "nan"
        assert t._encode(3.5, {}) == 3.5

    def test_long_string_clipped(self) -> None:
        clipped = t._encode("x" * 500, {})
        assert clipped.endswith("…") and len(clipped) == t._MAX_STR + 1

    def test_list_and_alias(self) -> None:
        heap: dict = {}
        shared = [1, 2]
        r1 = t._encode(shared, heap)
        r2 = t._encode(shared, heap)  # тот же объект → тот же ref (aliasing)
        assert r1 == r2 and "ref" in r1
        assert heap[r1["ref"]]["kind"] == "seq"
        assert len(heap) == 1

    def test_dict_object_function_module_opaque(self) -> None:
        heap: dict = {}
        t._encode({"a": 1}, heap)
        assert any(o["kind"] == "map" for o in heap.values())

        class C:
            def __init__(self) -> None:
                self.x = 1

        obj_heap: dict = {}
        t._encode(C(), obj_heap)
        assert any(o["kind"] == "object" and "x" in o["attrs"] for o in obj_heap.values())

        assert t._encode_obj(len, {}, 0)["kind"] == "func"
        assert t._encode_obj(json, {}, 0)["kind"] == "module"
        assert t._encode_obj(3j, {}, 0)["kind"] == "opaque"  # complex — не спец-тип

    def test_depth_cap_falls_back_to_repr(self) -> None:
        assert "repr" in t._encode_obj([1], {}, t._MAX_DEPTH + 1)

    def test_safe_repr_handles_raising_repr(self) -> None:
        class Bad:
            def __repr__(self) -> str:
                raise RuntimeError("boom")

        assert t._safe_repr(Bad()) == "<Bad>"


class TestTracerObject:
    def test_records_step_for_target_frame(self) -> None:
        tracer = t._Tracer(__file__, max_steps=1000, stdout_buf=io.StringIO())
        # __file__ совпадает с co_filename текущего кадра → шаг записывается
        ret = tracer(sys._getframe(), "line", None)
        assert ret is tracer
        assert len(tracer.steps) == 1
        assert tracer.steps[0]["event"] == "line"

    def test_ignores_non_target_file(self) -> None:
        tracer = t._Tracer("/nonexistent/other.py", max_steps=1000, stdout_buf=io.StringIO())
        assert tracer(sys._getframe(), "line", None) is None
        assert tracer.steps == []

    def test_max_steps_sets_truncated(self) -> None:
        tracer = t._Tracer(__file__, max_steps=2, stdout_buf=io.StringIO())
        for _ in range(5):
            tracer(sys._getframe(), "line", None)
        assert len(tracer.steps) == 2
        assert tracer.truncated is True


def test_encode_scope_filters_globals_and_dunders() -> None:
    scope = {"__name__": "__main__", "mod": json, "x": 1, "__custom__": 2}
    out = t._encode_scope(scope, {}, is_global=True)
    assert out == {"x": 1}  # module + __name__ + __custom__ отфильтрованы


# main/run_trace теперь безопасны in-process: run_trace восстанавливает прежний
# трейс-хук (sys.gettrace()), а не None, поэтому вызов под coverage не глушит её
# CTracer. Эти два теста гоняют точку входа напрямую (в дополнение к субпроцесс-
# ным trace_code-тестам выше) и покрывают тела main/run_trace.


def test_main_writes_json(capsys, tmp_path) -> None:
    target = tmp_path / "prog.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    rc = t.main([str(target)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stdout"] == "hi\n"


def test_main_without_args_is_usage_error(capsys) -> None:
    assert t.main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_run_trace_records_exception_in_process() -> None:
    # in-process путь исключения: exec бросает → error заполнен, шаги записаны.
    result = t.run_trace("x = 1\ny = x / 0\n", "<trace-test>", max_steps=100)
    assert result["error"]["type"] == "ZeroDivisionError"
    assert result["steps"]  # хотя бы один шаг до падения


def test_run_trace_restores_previous_trace_hook() -> None:
    # run_trace обязана вернуть прежний sys.gettrace() (не None), иначе она
    # заглушила бы coverage/pdb на остаток процесса (issue #318).
    sentinel = sys.gettrace()
    t.run_trace("z = 1\n", "<trace-test>", max_steps=10)
    assert sys.gettrace() is sentinel


def test_trace_code_refuses_when_runner_lacks_project_imports(monkeypatch) -> None:
    """issue #550: под --sandbox трассировщик не изолируем (пакет грейдера не
    пробрасывается в песочницу) — trace_code честно отказывает SandboxError'ом.

    Отказ идёт по capability ``supports_project_imports``, а НЕ по имени класса:
    произвольная обёртка / новый backend (Docker/remote) с флагом ``False`` тоже
    распознаётся — имя класса здесь намеренно НЕ ``SandboxRunner``."""
    from stepik_grader.core import grader_core

    class IsolatedRunner:  # имя НЕ "SandboxRunner" — распознаётся по capability
        supports_project_imports = False

        def run(self, spec):
            raise AssertionError("не должно вызываться — трейс отклонён до исполнения")

    monkeypatch.setattr(grader_core, "_RUNNER", IsolatedRunner())
    result = trace_code("x = 1\n")
    assert result["steps"] == []
    assert result["error"]["type"] == "SandboxError"
    assert "--sandbox" in result["error"]["message"]


def test_trace_code_runs_when_runner_supports_project_imports(monkeypatch) -> None:
    """issue #550: раннер с ``supports_project_imports=True`` (``LocalRunner`` и
    любой неизолирующий backend) — трейс исполняется, отказа нет."""
    from stepik_grader.core import grader_core
    from stepik_grader.core.runner import LocalRunner

    monkeypatch.setattr(grader_core, "_RUNNER", LocalRunner())
    trace = trace_code("x = 1\ny = 2\n")
    assert trace["error"] is None
    assert len(trace["steps"]) >= 1


def test_runner_capability_flags_are_explicit() -> None:
    """issue #550 acceptance: ``LocalRunner=True``, ``SandboxRunner=False`` —
    объявлены явно на классах (без конструирования platform-backend'а).

    Флаги трёх backend-классов (``LinuxSandboxRunner``/``MacSandboxRunner``/
    ``WindowsSandboxRunner``) НЕ проверяем импортом здесь: их присваивание
    ``self._backend: Runner`` в фасаде статически гарантирует mypy (без
    ``supports_project_imports`` они не пройдут ``Runner`` Protocol), а прямой
    импорт ``_linux``/``_macos``/``_windows`` тут загрязнил бы ``sys.modules``
    для монкейпатч-тестов ``test_sandbox_runner``."""
    from stepik_grader.core.runner import LocalRunner
    from stepik_grader.core.sandbox import SandboxRunner

    assert LocalRunner.supports_project_imports is True
    assert SandboxRunner.supports_project_imports is False
