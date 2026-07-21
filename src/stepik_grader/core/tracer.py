"""tracer.py — пошаговый трейс исполнения кода (issue #318, эпик #314).

Архитектурный слой: Infrastructure (как ``runner.py``).

Исполняет код в **subprocess** под ``sys.settrace`` и собирает JSON-трейс
«состояние после каждого шага»: кадры стека с локальными переменными и heap
объектов со ссылками по ``id`` — так на фронтенде виден aliasing (две
переменные на один объект) и вложенные структуры. Архитектура — как у
[Python Tutor](https://pythontutor.com/): backend собирает полный трейс → JSON
→ фронтенд-плеер листает шаги вперёд/назад без повторного исполнения.

Два входа:

- ``trace_code(code, stdin, ...)`` — оркестратор (вызывается web-слоем): пишет
  self-contained bootstrap (код инлайнится через ``repr`` + прямой вызов
  ``run_trace``) во временный файл и исполняет его через
  ``grader_core.run_spec()`` активным Runner'ом (``LocalRunner`` или
  ``SandboxRunner`` при ``--serve --sandbox``, issue #396/#640), ловит JSON-трейс из stdout. Общий
  wall-clock таймаут.
- ``python -m stepik_grader.core.tracer <file>`` — прямой/отладочный вход в
  subprocess: ставит ``settrace``, ``exec``'ает код, печатает JSON-трейс.

Лимиты против бесконечных циклов/раздутого трейса: ``max_steps`` (усечение с
флагом ``truncated``), обрезка строк/контейнеров/глубины при кодировании.

Безопасности OS-уровня нет (инвариант CLAUDE.md №4): код исполняется без
изоляции ФС/сети — только доверенный код, как и в грейдинге.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import types
from typing import Any

from stepik_grader.core.runner import RunSpec

__all__ = ["DEFAULT_MAX_STEPS", "trace_code"]

DEFAULT_MAX_STEPS = 1000
_MAX_STR = 200  # обрезка длинных строк в трейсе
_MAX_ELEMS = 100  # макс. элементов контейнера в снимке
_MAX_DEPTH = 8  # глубина рекурсии кодирования вложенных структур
_TRACE_EVENTS = ("line", "call", "return", "exception")

# Имена module-фрейма, которые не показываем как «переменные пользователя».
_HIDDEN_GLOBALS = frozenset(
    {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__builtins__", "__file__"}
)


# ---------------------------------------------------------------------------
# In-subprocess: кодирование значений + сбор трейса под settrace
# ---------------------------------------------------------------------------


def _clip_str(text: str) -> str:
    return text if len(text) <= _MAX_STR else text[:_MAX_STR] + "…"


def _encode(value: Any, heap: dict[str, Any], depth: int = 0) -> Any:
    """Закодировать значение в ref: примитив inline или ``{"ref": "<id>"}``.

    Контейнеры/объекты кладутся в ``heap`` по строковому ``id`` — ссылки
    позволяют фронтенду показать aliasing и вложенность.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # JS теряет точность на больших int — крупные отдаём строкой-repr.
        return value if abs(value) < 2**53 else {"big": repr(value)}
    if isinstance(value, float):
        # NaN/Inf невалидны в JSON — отдаём строкой-repr.
        return (
            value if value == value and value not in (float("inf"), float("-inf")) else repr(value)
        )
    if isinstance(value, str):
        return _clip_str(value)

    oid = str(id(value))
    if oid not in heap:
        heap[oid] = {"type": type(value).__name__}  # заглушка — разрывает циклы
        heap[oid] = _encode_obj(value, heap, depth)
    return {"ref": oid}


def _encode_obj(value: Any, heap: dict[str, Any], depth: int) -> dict[str, Any]:
    """Закодировать «тяжёлый» объект (контейнер/instance) для heap."""
    type_name = type(value).__name__
    if depth > _MAX_DEPTH:
        return {"type": type_name, "repr": _clip_str(_safe_repr(value))}

    if isinstance(value, (list, tuple, set, frozenset)):
        seq = list(value)
        elems = [_encode(v, heap, depth + 1) for v in seq[:_MAX_ELEMS]]
        return {"type": type_name, "kind": "seq", "elems": elems, "n": len(seq)}
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ELEMS]
        entries = [[_encode(k, heap, depth + 1), _encode(v, heap, depth + 1)] for k, v in items]
        return {"type": type_name, "kind": "map", "entries": entries, "n": len(value)}
    if isinstance(value, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
        return {"type": "function", "kind": "func", "name": getattr(value, "__name__", "?")}
    if isinstance(value, types.ModuleType):
        return {"type": "module", "kind": "module", "name": getattr(value, "__name__", "?")}

    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict) and attrs:
        encoded = {
            str(k): _encode(v, heap, depth + 1)
            for k, v in list(attrs.items())[:_MAX_ELEMS]
            if not str(k).startswith("__")
        }
        return {"type": type_name, "kind": "object", "attrs": encoded}
    return {"type": type_name, "kind": "opaque", "repr": _clip_str(_safe_repr(value))}


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _encode_scope(
    scope: dict[str, Any], heap: dict[str, Any], *, is_global: bool
) -> dict[str, Any]:
    """Закодировать локальные/глобальные имена фрейма (без служебных дандеров)."""
    result: dict[str, Any] = {}
    for name, val in scope.items():
        if is_global and (name in _HIDDEN_GLOBALS or isinstance(val, types.ModuleType)):
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        result[name] = _encode(val, heap, 0)
    return result


class _Tracer:
    """Собирает шаги трейса под ``sys.settrace`` для одного целевого файла."""

    def __init__(self, target_file: str, max_steps: int, stdout_buf: io.StringIO) -> None:
        self.target = target_file
        self.max_steps = max_steps
        self.stdout_buf = stdout_buf
        self.steps: list[dict[str, Any]] = []
        self.truncated = False

    def __call__(self, frame: types.FrameType, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename != self.target:
            return None  # не трейсим библиотечный код — только код пользователя
        if event in _TRACE_EVENTS:
            if len(self.steps) >= self.max_steps:
                self.truncated = True
                return None  # достигнут лимит — прекращаем трейсить этот кадр
            self._record(frame, event)
        return self

    def _record(self, frame: types.FrameType, event: str) -> None:
        heap: dict[str, Any] = {}
        stack: list[dict[str, Any]] = []
        # от текущего кадра вверх до кадров целевого файла; переворачиваем,
        # чтобы [0] был внешним (module), [-1] — текущим.
        chain: list[types.FrameType] = []
        f: types.FrameType | None = frame
        while f is not None:
            if f.f_code.co_filename == self.target:
                chain.append(f)
            f = f.f_back
        for depth, fr in enumerate(reversed(chain)):
            is_global = depth == 0
            stack.append(
                {
                    "func": fr.f_code.co_name,
                    "line": fr.f_lineno,
                    "locals": _encode_scope(fr.f_locals, heap, is_global=is_global),
                }
            )
        self.steps.append(
            {
                "step": len(self.steps),
                "event": event,
                "line": frame.f_lineno,
                "func": frame.f_code.co_name,
                "stack": stack,
                "heap": heap,
                "stdout_len": self.stdout_buf.tell(),
            }
        )


def run_trace(code: str, target_file: str, max_steps: int = DEFAULT_MAX_STEPS) -> dict[str, Any]:
    """Исполнить ``code`` под трассировкой; вернуть ``{steps, stdout, truncated, error}``.

    Штатно вызывается в subprocess (см. ``main``), но безопасна и in-process:
    в ``finally`` восстанавливается **предыдущий** трейс-хук
    (``sys.gettrace()``), а не ``None`` — иначе вызов под coverage/отладчиком
    затёр бы его собственный settrace-хук на весь остаток процесса. stdout
    пользовательского кода перехватывается в буфер, чтобы не смешаться с
    JSON-трейсом; плеер режет его по ``step.stdout_len``.
    """
    stdout_buf = io.StringIO()
    tracer = _Tracer(target_file, max_steps, stdout_buf)
    namespace: dict[str, Any] = {"__name__": "__main__", "__file__": target_file}
    error: dict[str, Any] | None = None

    real_stdout = sys.stdout
    sys.stdout = stdout_buf
    compiled = compile(code, target_file, "exec")
    prev_trace = sys.gettrace()  # обычно None; под coverage/pdb — их CTracer/хук
    sys.settrace(tracer)
    try:
        exec(compiled, namespace)  # noqa: S102 — доверенный код песочницы (нет OS-sandbox)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        sys.settrace(prev_trace)  # вернуть, что было (не None) — не глушить coverage/pdb
        sys.stdout = real_stdout

    return {
        "steps": tracer.steps,
        "stdout": stdout_buf.getvalue(),
        "truncated": tracer.truncated,
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    """Subprocess-точка входа: ``python -m stepik_grader.core.tracer <file>``.

    Читает целевой файл, трассирует его исполнение, печатает JSON-трейс в stdout.
    Штатно спавнится ``trace_code``; безопасна и in-process (``run_trace``
    восстанавливает прежний трейс-хук).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(json.dumps({"error": {"type": "usage", "message": "no target file"}}))
        return 2
    target = args[0]
    max_steps = int(args[1]) if len(args) > 1 else DEFAULT_MAX_STEPS
    code = pathlib.Path(target).read_text(encoding="utf-8")
    result = run_trace(code, target, max_steps)
    # allow_nan=False гарантирует валидный JSON (NaN/Inf уже сведены к строкам).
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


# ---------------------------------------------------------------------------
# Orchestrator: спавн subprocess, захват JSON-трейса
# ---------------------------------------------------------------------------


def trace_code(
    code: str,
    stdin: str = "",
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Оттрейсить ``code`` (со ``stdin``) через активный Runner; вернуть JSON-трейс.

    Возвращает ``{steps, stdout, truncated, error}`` (см. ``run_trace``) либо
    ``{steps: [], error: {...}}`` при таймауте/сбое. Не исполняет код в процессе
    сервера — трассировка идёт в дочернем интерпретаторе.

    Под ``--serve --sandbox`` пошаговый трейс **недоступен** (issue #396):
    трассировщик требует пакет грейдера (``stepik_grader.core.tracer``) в
    дочернем процессе, а ``SandboxRunner`` намеренно не пробрасывает
    site-packages проекта в изоляцию (SECURITY.md) — честно отказываем, а не
    исполняем трейс вне песочницы.
    """
    from stepik_grader.core import grader_core  # локальный импорт: избежать цикла в DAG

    # issue #550: под sandbox трассировщик не изолируем (пакет проекта не в
    # песочнице) — консультируем capability активного раннера
    # (``supports_project_imports``) вместо хрупкого ``type(_RUNNER).__name__``;
    # новый backend (Docker/remote) объявляет флаг сам и не обходит этот отказ.
    if not grader_core.active_runner().supports_project_imports:
        return {
            "steps": [],
            "stdout": "",
            "truncated": False,
            "error": {
                "type": "SandboxError",
                "message": (
                    "Пошаговый трейс недоступен под --sandbox: трассировщик требует "
                    "пакет грейдера, который не пробрасывается в изолированную среду."
                ),
            },
        }

    # Вне песочницы: bootstrap инлайнит код через repr и трейсит прямым вызовом
    # run_trace() (импорт tracer работает — LocalRunner делит окружение с
    # сервером). run_trace печатает JSON-трейс в stdout — тот же контракт, что у
    # ``-m``-входа. UTF-8 окружение ставит сам Runner.
    bootstrap = (
        "import json as _json, sys as _sys\n"
        "from stepik_grader.core import tracer as _tracer\n"
        "_result = _tracer.run_trace(" + repr(code) + ", 'solution.py', " + repr(max_steps) + ")\n"
        "_sys.stdout.write(_json.dumps(_result, ensure_ascii=False, allow_nan=False))\n"
    )

    # delete=False намеренно: путь файла уходит в RunSpec раннеру, чистится в finally.
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".py", encoding="utf-8", delete=False
    )
    try:
        tmp.write(bootstrap)
        tmp.close()
        # issue #640: через публичный grader_core.run_spec(), а не приватный
        # _RUNNER — выбор backend'а спрятан за одной точкой (ADR-0010); active_runner()
        # выше уже консультируется публично для capability-гейта sandbox.
        outcome = grader_core.run_spec(
            RunSpec(
                path=pathlib.Path(tmp.name),
                stdin=stdin.encode("utf-8"),
                timeout=timeout,
                measure_memory=False,
            )
        )
    finally:
        with contextlib.suppress(OSError):  # уборка временного файла
            pathlib.Path(tmp.name).unlink()

    if outcome.timed_out:  # pragma: no cover — таймаут долгого трейса
        return {
            "steps": [],
            "stdout": "",
            "truncated": True,
            "error": {"type": "TimeoutError", "message": f"exceeded {timeout}s"},
        }
    raw = outcome.stdout.decode("utf-8", errors="replace")
    try:
        trace: dict[str, Any] = json.loads(raw)
        return trace
    except json.JSONDecodeError:  # pragma: no cover — дочерний процесс не выдал валидный JSON
        stderr = outcome.launch_error or outcome.stderr.decode("utf-8", errors="replace")
        return {
            "steps": [],
            "stdout": "",
            "truncated": False,
            "error": {"type": "TraceError", "message": stderr[:500] or "no trace produced"},
        }


if __name__ == "__main__":
    raise SystemExit(main())
