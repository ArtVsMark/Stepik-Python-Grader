"""grader_core.py — исполнение решений и агрегация статистики.

Архитектурный слой: Application / Business logic.
Отвечает за:
  - исполнение одного тест-кейса в subprocess (run_single_test) — выбор
    stdin/wrapper-стратегии, лимит памяти, точный тайминг;
  - агрегацию статистики по всем тест-кейсам (run_tests, run_benchmark,
    run_microbench_mode).

Обнаружение файлов-решений, загрузка тест-кейсов и резолюция test_dir —
core/test_loader.py. Определение режима запуска (stdin vs function) —
core/mode_detector.py. Генерация wrapper-скриптов — core/wrapper_builder.py.
Все три реэкспортируются здесь по имени для обратной совместимости (Issue
#45 A-01 — этот файл был 1200+ строк).

Не содержит вывода (rich-таблицы) — это core/reporter.py; не содержит CLI/меню —
это cli.py.

Извлечён из grader.py (Issue #20, finding #4 / CLAUDE.md Sprint 7, шаг 2).
Перенесён в core/ (Issue #26).
"""

from __future__ import annotations

import contextlib
import difflib
import pathlib
import shutil
import statistics
import tempfile
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from stepik_grader.config import CONFIG, get_config

__all__ = [
    "BenchStats",
    "TestCase",
    "active_runner",
    "collect_grouped_files",
    "find_all_solution_files",
    "is_function_only_solution",
    "is_solution_file",
    "load_test_cases",
    "load_text_lines",
    "preflight_solution",
    "resolve_test_dir",
    "run_benchmark",
    "run_microbench_mode",
    "run_single_test",
    "run_spec",
    "run_tests",
    "set_runner",
]
# TIMEOUT_SECONDS/ENCODING/SIMILAR_THRESHOLD/MUCH_SLOWER_THRESHOLD/
# MEASURE_CHILD_MEMORY/MICROBENCH_MAX_CASES — намеренно НЕ в __all__ (issue #52
# Q-03). Это просто module-level алиасы значений CONFIG (см. ниже), а не
# самостоятельный публичный API; их присутствие в __all__ создавало неявную
# зависимость на конкретные имена констант вместо GraderConfig. grader.py
# по-прежнему реэкспортирует их явно по имени (backward-compat __all__ этого
# фасада не менялся) — новый код должен читать stepik_grader.config.CONFIG.

# run_single_test() делегирует фактический subprocess-запуск LocalRunner'у
# (issue #136/#137/#138, docs/dev/design/server-mode.md § Runner-слой) — не меняет
# поведение, только выделяет абстракцию Runner для будущего SandboxRunner
# (issue #157). _apply_memory_limit/_measure_peak_memory реэкспортированы по
# имени (тот же паттерн, что для test_loader.py и др. — Issue #45 A-01):
# grader_core._apply_memory_limit/._measure_peak_memory и grader.py facade
# продолжают работать без изменений.
# test_loader.py / mode_detector.py / wrapper_builder.py — извлечены из этого
# файла (Issue #45 A-01). Реэкспортируются по имени (не через `import *`),
# чтобы __all__ и приватные имена, на которые опирается grader.py/cli.py/тесты,
# остались доступны как grader_core.X независимо от физического места
# определения. microbench_runner.py / normalizers.py — первоисточники
# timeit-бенчмарка и нормализации float-вывода, не затронуты этим разбиением.
from stepik_grader.core.microbench_runner import apply_relative_ranking, run_microbench
from stepik_grader.core.mode_detector import (
    _ast_class_names,
    _ast_function_name,
    _ast_function_names,
    _block_invokes_solution,
    _detect_run_mode,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _is_python_code_block,
    _is_safe_constant,  # noqa: F401  (реэкспорт для grader.py, не вызывается здесь напрямую)
    _read_meta_function_name,
    is_function_only_solution,
)
from stepik_grader.core.normalizers import (
    floats_equal_with_precision,
    split_output_lines,
    strip_trailing_blanks,
)

# issue #940: сравнение перешло на floats_equal_with_precision, но сам
# `_normalize_output_line` остаётся реэкспортом для фасада grader.py (см. его
# __all__) — удалять его отсюда нельзя, это публичное имя.
from stepik_grader.core.normalizers import normalize_floats as _normalize_output_line  # noqa: F401
from stepik_grader.core.result import BenchResult, CaseResult, SolutionResult, Verdict
from stepik_grader.core.runner import (
    TRUNCATION_MARKER,
    RunOutcome,
    RunSpec,
    _apply_memory_limit,  # noqa: F401  (реэкспорт для тестов/grader.py facade)
    _measure_peak_memory,  # noqa: F401  (реэкспорт для тестов/grader.py facade)
    active_runner,
    run_spec,
    set_runner,
)
from stepik_grader.core.test_loader import (
    _SOLUTION_FILE_RE,  # noqa: F401  (реэкспорт для grader.py)
    TestCase,
    _apply_run_mode_override,
    _parse_testblock_file,  # noqa: F401  (реэкспорт для grader.py)
    collect_grouped_files,
    find_all_solution_files,
    is_solution_file,
    load_test_cases,
    load_text_lines,
    resolve_test_dir,
)
from stepik_grader.core.wrapper_builder import (
    _build_call_wrapper,
    _build_function_wrapper,
)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Значения читаются из config.CONFIG (единая точка правды, Sprint 6.3) —
# переопределяются через [tool.stepik-grader] в pyproject.toml.
# issue #830 (ARCH-04): константы ниже — СНИМОК на момент импорта, оставленный
# ради обратной совместимости (на них ссылается grader.py-фасад и внешний код).
# Внутри модуля они больше НЕ используются как дефолты аргументов: значение
# читается функцией `get_config()` в момент ВЫЗОВА.
#
# Почему именно `get_config()`, а не импортированный `CONFIG`: имя `CONFIG`
# связывается с объектом при импорте модуля, поэтому обновление конфига после
# него на нём не отражается. Проверено прогоном — с чтением через `CONFIG`
# решение со `sleep(3)` при `timeout=1.0` всё равно получало AC за 3.1 с.
TIMEOUT_SECONDS: float = CONFIG.timeout_seconds
ENCODING: str = CONFIG.encoding

# issue #792 (PY-04): кодировка ПОТОКОВ дочернего процесса. Прибита к UTF-8
# намеренно и не настраивается: раннеры (core/runner, все три sandbox-backend'а)
# принудительно выставляют решению PYTHONIOENCODING=utf-8 и PYTHONUTF8=1, так
# что любое другое значение здесь рассинхронизировало бы стороны и давало тихие
# ложные WA. CONFIG.encoding касается только чтения файлов с диска.
_CHILD_IO_ENCODING = "utf-8"
SIMILAR_THRESHOLD: float = CONFIG.similar_threshold
MUCH_SLOWER_THRESHOLD: float = CONFIG.much_slower_threshold
MEASURE_CHILD_MEMORY: bool = CONFIG.measure_child_memory
MICROBENCH_MAX_CASES: int = CONFIG.microbench_max_cases

# ---------------------------------------------------------------------------
# Вспомогательные типы
# ---------------------------------------------------------------------------


@dataclass
class BenchStats:
    """Унифицированная статистика замеров для режимов 3 и 4.

    Устраняет дублирование вычислений между run_benchmark() и _micro_stats().
    """

    timings: list[float]

    @property
    def min(self) -> float:
        """Минимальное время замера."""
        return min(self.timings)

    @property
    def median(self) -> float:
        """Медианное время — основной ориентир при сравнении решений."""
        return statistics.median(self.timings)

    @property
    def mean(self) -> float:
        """Среднее время замера."""
        return statistics.mean(self.timings)

    @property
    def stdev(self) -> float:
        """Среднеквадратичное отклонение; 0.0 при единственном замере."""
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0

    @property
    def max(self) -> float:
        """Максимальное время замера."""
        return max(self.timings)

    def relative_to(self, baseline: float) -> float:
        """Возвращает median / baseline * 100 (процент от эталона)."""
        return (self.median / baseline * 100) if baseline > 0 else 0.0


# _apply_memory_limit/_measure_peak_memory перенесены в core/runner.py вместе
# с самим subprocess-запуском (issue #136/#137/#138, Runner-абстракция —
# docs/dev/design/server-mode.md § Runner-слой). Реэкспортированы по имени ниже — тот же
# паттерн, что и для test_loader.py/mode_detector.py/wrapper_builder.py
# (Issue #45 A-01): grader_core._apply_memory_limit/._measure_peak_memory и
# grader.py facade продолжают работать без изменений.


# ---------------------------------------------------------------------------
# Исполнение и агрегация
# ---------------------------------------------------------------------------

# issue #830 (ARCH-03): сам реестр активного Runner'а переехал в
# ``core/runner.py`` — владельцу протокола. Здесь остаётся РЕЭКСПОРТ: имена
# ``set_runner``/``run_spec``/``active_runner`` — часть публичного фасада ядра
# (ADR-0010), и менять поверхность ради переезда внутренностей незачем.
#
# Что это чинит: ``microbench_runner`` и ``tracer`` (модули нижнего уровня)
# импортировали этот оркестратор ради одного вызова, и оба импорта приходилось
# держать ленивыми, чтобы не собрать цикл. DAG-guard такие рёбра не видит — он
# не спускается в тела функций, поэтому цикл существовал, а тест был зелёным.


def _lines_for_compare(
    actual_lines: list[str],
    expected_lines: list[str],
) -> tuple[list[str], list[str]]:
    """Пара «факт, ожидание», приведённая к текущему режиму сравнения (issue #1111).

    ``compare_mode="stepik"`` (по умолчанию) снимает то, чего не различает чекер
    платформы: хвостовые пробелы строки и хвостовые пустые строки. Найдено
    внешним эталоном — на реальной базе курса решения, принятые Stepik,
    получали ``WA`` ровно на этих различиях.

    ``compare_mode="strict"`` возвращает списки как есть: побайтовая построчная
    сверка для тех, кому нужна именно она (авторы задач, прогонный корпус).
    """
    if get_config().compare_mode == "strict":
        return actual_lines, expected_lines
    return strip_trailing_blanks(actual_lines), strip_trailing_blanks(expected_lines)


def _undecodable_output_result(
    case: TestCase,
    outcome: RunOutcome,
    exc: UnicodeDecodeError,
) -> CaseResult:
    """Вердикт для вывода, который не является корректным UTF-8 (issue #1031).

    Отдельный исход, а не «просто WA»: сравнивать такой вывод с ожиданием
    нельзя — при терпимом декоде любые непредставимые байты схлопываются в
    один и тот же ``�``, и разные выводы становятся неотличимы. Поэтому
    причина называется прямо, а не прячется за ``█`` в отчёте.

    Вывод всё же показывается — терпимым декодом: пустой ``output`` не дал бы
    понять, что вообще напечатало решение, а показанные символы замены вместе
    с текстом ошибки объясняют картину целиком.
    """
    lossy = outcome.stdout.decode(_CHILD_IO_ENCODING, errors="replace")
    bad_byte = exc.object[exc.start : exc.start + 1]
    return {
        "passed": False,
        "output": split_output_lines(lossy),
        "expected": case.expected_lines,
        "diff": "",
        "time": outcome.elapsed,
        "memory": outcome.peak_memory_mb,
        "error": (
            f"вывод решения не является корректным {_CHILD_IO_ENCODING}: "
            f"байт 0x{bad_byte.hex()} в позиции {exc.start}. Сравнить его с ожиданием нельзя — "
            "разные байты дали бы один и тот же символ замены, и неверное решение получило бы AC. "
            "Печатайте текст, а не сырые байты (см. docs/use/configuration.md)."
        ),
        "timed_out": False,
        "verdict": "WA",
        "exit_code": outcome.returncode,
    }


def _fail_result(
    case: TestCase,
    *,
    error: str,
    verdict: Verdict,
    time: float = 0.0,
    memory: float = 0.0,
    timed_out: bool = False,
    exit_code: int | None = None,
) -> CaseResult:
    """Case-result dict для неуспешного раннего исхода ``run_single_test``.

    Общая форма всех возвратов до сравнения вывода
    (RE/TLE/CANCELLED/SANDBOX_VIOLATION): ``passed=False``, пустой ``output``,
    ``expected`` из кейса, без ``diff``. Различаются только
    ``error``/``verdict``/``time``/``memory``/``timed_out``/``exit_code``
    (issue #354 — убирает семь почти одинаковых литералов).
    """
    return {
        "passed": False,
        "output": [],
        "expected": case.expected_lines,
        "diff": "",
        "time": time,
        "memory": memory,
        "error": error,
        "timed_out": timed_out,
        "verdict": verdict,
        "exit_code": exit_code,
    }


@dataclass(frozen=True)
class _RunPlan:
    """План запуска одного кейса в ``run_single_test`` (issue #406).

    Либо готовый ``spec`` (плюс ``tmp_wrapper_path`` — временный wrapper-файл
    function-mode, который ``run_single_test`` удаляет после запуска), либо
    ``error`` — ошибка подготовки (нет ``function_name`` / невалидный wrapper),
    при которой запуск не производится и кейс сразу маппится в RE. Инвариант:
    ровно одно из ``spec``/``error`` заполнено.

    ``tmp_wrapper_dir`` — приватный каталог, в котором лежит wrapper (issue
    #945). Уборка сносит именно каталог, а не один файл: каталог и есть то, что
    защищает исполнение (см. ``_prepare_run_spec``). Отдельное поле, а не
    ``tmp_wrapper_path.parent``, чтобы ``rmtree`` не мог уехать в чужую папку,
    если путь придёт откуда-то ещё.
    """

    spec: RunSpec | None = None
    tmp_wrapper_path: pathlib.Path | None = None
    tmp_wrapper_dir: pathlib.Path | None = None
    error: str | None = None


def _prepare_run_spec(
    solution_path: pathlib.Path,
    case: TestCase,
    *,
    timeout: float,
    measure_memory: bool,
    cancel_event: threading.Event | None,
    max_memory_mb: int | None = None,
) -> _RunPlan:
    """Выбрать стратегию запуска кейса и собрать ``RunSpec`` (issue #406).

    stdin-режим → ``RunSpec`` со stdin-байтами кейса (``tmp_wrapper_path`` None).
    function-режим → сгенерировать wrapper (python-generation call-блок или
      legacy function-mode), записать во временный ``.py`` и указать на него
      ``RunSpec.path`` (``tmp_wrapper_path`` задан — вызывающая сторона удаляет
      файл после запуска; сам файл решения не модифицируется). Ошибка
      подготовки (нет ``function_name`` / ``ValueError`` из wrapper-builder) →
      ``_RunPlan(error=...)``: запуск не делается, кейс сразу RE.

    Выделена из ``run_single_test`` — вся не-исполняющая «стратегия+wrapper»
    логика в одном месте, тестируемая без реального subprocess-запуска.
    """
    # issue #641: per-run memory-override (из API limits); None → дефолт CONFIG.
    mem_cap = get_config().max_memory_mb if max_memory_mb is None else max_memory_mb
    if case.test_type != "function":
        stdin_data = "\n".join(case.input_lines) + "\n"
        return _RunPlan(
            spec=RunSpec(
                path=solution_path,
                # issue #792 (PY-04): поток ребёнка — всегда UTF-8, а не
                # CONFIG.encoding. Раннеры принудительно ставят решению
                # PYTHONIOENCODING=utf-8/PYTHONUTF8=1, поэтому кодировать ввод
                # чем-то другим значит гарантированно рассинхронизировать
                # стороны: input() читал бы cp1251-байты как UTF-8. CONFIG.encoding
                # остаётся тем, чем заявлен в документации, — кодировкой ЧТЕНИЯ
                # файлов решений и тестов.
                stdin=stdin_data.encode(_CHILD_IO_ENCODING),
                timeout=timeout,
                measure_memory=measure_memory,
                max_memory_mb=mem_cap,
                max_output_bytes=get_config().max_output_bytes,
                cancel_event=cancel_event,
            )
        )

    # function-маршрут трактует блок как исходный код: с issue #783 разбор
    # формата 3 сохраняет пробелы по краям строк, и ведущий отступ сделал бы
    # блок синтаксически неверным (и в детекторе, и в сгенерированном wrapper'е).
    # stdin-ветка выше данные не трогает — там эти пробелы значимы.
    input_data = "\n".join(case.input_lines).strip()
    # Маршрут выбирается по тому, печатает ли блок результат сам (формат 3),
    # а не по «похоже ли на Python-код»: присваивание `a = 5` — это данные
    # legacy-теста, а не драйвер (issue #622).
    func_name = _read_meta_function_name(solution_path) or _ast_function_name(solution_path)
    # issue #938: драйвером блок считается, если вызывает ЛЮБУЮ функцию решения,
    # а не ту одну, что выбрана для legacy-обёртки. Иначе вердикт зависел от
    # порядка объявлений: `def _helper` выше целевой функции уводил блок
    # `show(5)` в legacy-обёртку и давал NameError на верном решении.
    #
    # issue #996 (RUN-1-06): классы — такие же вызываемые имена решения, как
    # функции, и `test_loader.py` тот же набор уже считает обоими (`callables`).
    # Здесь их не было, и решение ООП-курса из одного `class Vector` при блоке
    # `Vector(-5).length()` драйвером не признавалось: блок уходил в
    # legacy-обёртку, которая импортирует ФУНКЦИЮ, — а `_ast_function_name` при
    # отсутствии функций верхнего уровня доставала вложенный `__init__`.
    # Пользователь получал `NameError: name 'Vector' is not defined` с
    # трейсбеком в /tmp/stepik-wrapper-*/wrapper.py — файл, которого он не писал,
    # про имя, которое в его решении есть. Тот же блок с `print(...)` работал:
    # вердикт зависел от наличия печати, а не от решения.
    known_names = {
        *_ast_function_names(solution_path),
        *_ast_class_names(solution_path),
        *([func_name] if func_name else []),
    }
    if _block_invokes_solution(input_data, known_names):
        # python-generation function-call: блок уже содержит print(func(...))
        wrapper_src = _build_call_wrapper(solution_path, input_data)
    else:
        # legacy function-mode: блок задаёт данные, вызов собираем сами
        if func_name is None:
            return _RunPlan(
                error=(
                    "function_name not found (meta.json missing and no function def in solution)"
                )
            )
        try:
            wrapper_src = _build_function_wrapper(solution_path, input_data, func_name)
        except ValueError as exc:
            return _RunPlan(error=str(exc))

    # Записываем wrapper во временный файл; run_single_test удалит его после запуска.

    # путь уходит в RunSpec раннеру), удаляется вызывающей стороной после запуска.
    # issue #792 (PY-04): wrapper пишется в UTF-8 — его читает интерпретатор
    # дочернего процесса, которому раннер выставил PYTHONUTF8=1. Прежний
    # CONFIG.encoding здесь означал бы, что при cp1251 сгенерированный код
    # физически не разберётся.
    # issue #945: приватный каталог 0700 вместо общего системного temp — тот же
    # вектор, что закрыт в runner.py/tracer.py (issue #799) и в микробенче:
    # каталог исполняемого скрипта CPython ставит ПЕРВЫМ в ``sys.path``
    # дочернего процесса, поэтому в общем ``/tmp`` посторонний мог подложить
    # свой ``json.py`` и подменить импорт внутри wrapper'а. Права файла тут не
    # при чём — цель атаки каталог.
    wrapper_dir = pathlib.Path(tempfile.mkdtemp(prefix="stepik-wrapper-"))
    wrapper_path = wrapper_dir / "wrapper.py"
    wrapper_path.write_text(wrapper_src, encoding=_CHILD_IO_ENCODING)
    return _RunPlan(
        spec=RunSpec(
            path=wrapper_path,
            stdin=None,  # wrapper не читает stdin
            timeout=timeout,
            measure_memory=measure_memory,
            max_memory_mb=mem_cap,
            max_output_bytes=get_config().max_output_bytes,
            # issue #992 (SBX-1-01/SBX-1-02): под изоляцией внутрь попадает
            # только то, что отдали в spec. Обёртка обязана сохранить своё имя —
            # положенная под именем модуля решения, она импортировала саму себя
            # («cannot import name ... from partially initialized module») и
            # роняла ВСЕ function-кейсы верного решения. Рядом кладётся сам
            # модуль решения и его соседи: вне изоляции они доступны по
            # исходному пути, внутри — только так.
            script_name=wrapper_path.name,
            aux_files=_solution_aux_files(solution_path),
            cancel_event=cancel_event,
        ),
        tmp_wrapper_path=wrapper_path,
        tmp_wrapper_dir=wrapper_dir,
    )


def _solution_aux_files(solution_path: pathlib.Path) -> tuple[tuple[str, bytes], ...]:
    """Модуль решения и соседние ``*.py`` для переноса внутрь изоляции (issue #992).

    Обёртка импортирует решение, а решение — соседние модули своей папки
    (``helpers.py`` рядом с ``solution.py``). Вне изоляции они доступны по
    исходному пути; внутри видно только то, что материализовано в рабочем
    каталоге, — поэтому список собирается явно.

    Берутся только ``*.py`` верхнего уровня папки решения: пакеты и данные —
    отдельный разговор, а тянуть внутрь всё подряд означало бы копировать в
    изоляцию произвольные файлы пользователя. Нечитаемый файл пропускается —
    он и так не помог бы решению, а падать на подготовке из-за чужого файла
    рядом нельзя.
    """
    aux: list[tuple[str, bytes]] = []
    for candidate in sorted(solution_path.parent.glob("*.py")):
        try:
            aux.append((candidate.name, candidate.read_bytes()))
        except OSError:
            continue
    return tuple(aux)


def _map_outcome_to_result(
    outcome: RunOutcome,
    case: TestCase,
    timeout: float,
) -> CaseResult:
    """Чистая функция: сырой ``RunOutcome`` → словарь-результат кейса (issue #406).

    Порядок веток verdict: ``launch_error`` → RE; ``sandbox_violation`` →
    SANDBOX_VIOLATION; ``cancelled`` → CANCELLED; ``timed_out`` → TLE;
    ненулевой ``returncode`` → RE; иначе сравнение вывода (с нормализацией
    float-строк) → AC/WA (+unified diff). Без I/O и без subprocess — каждую
    ветку verdict можно проверить изолированно (issue #406 acceptance).
    """
    if outcome.launch_error is not None:
        return _fail_result(case, error=outcome.launch_error, verdict="RE")

    if outcome.sandbox_violation is not None:
        # issue #266 — SandboxRunner (core/sandbox/) proactively killed the
        # process for exceeding a quota it detects itself (memory/output_size/
        # cpu), not a genuine timeout or a plain crash: distinct verdict,
        # additive to AC/WA/RE/TLE/CANCELLED (docs/dev/design/server-mode.md § Классы
        # ошибок). Network/filesystem/process-count violations are rejected
        # by the kernel INSIDE the sandbox and surface as an ordinary non-zero
        # exit (RE) instead -- Runner doesn't inspect the child's traceback to
        # relabel those. LocalRunner never sets this field.
        return _fail_result(
            case,
            error=f"Sandbox violation: {outcome.sandbox_violation}",
            verdict="SANDBOX_VIOLATION",
            time=outcome.elapsed,
            memory=outcome.peak_memory_mb,
        )

    if outcome.cancelled:
        # issue #262 — async job model cancellation, distinct from a genuine
        # solution timeout (TLE): a cancelled run must never be mislabeled as
        # "your solution is too slow" in the UI.
        return _fail_result(
            case,
            error="Cancelled by user",
            verdict="CANCELLED",
            time=outcome.elapsed,
        )

    if outcome.timed_out:
        return _fail_result(
            case,
            error=f"Timeout after {timeout}s",
            verdict="TLE",
            time=outcome.elapsed,
            timed_out=True,
        )

    # issue #792 (PY-04): вывод ребёнка декодируется UTF-8 — симметрично тому,
    # как он кодируется на входе и как раннер настраивает сам процесс. С
    # CONFIG.encoding != utf-8 это давало ложные WA: вывод решения читался
    # чужой кодировкой и не совпадал с ожиданием.
    # stderr — только для показа, поэтому декодируется терпимо: битый байт в
    # трейсбеке не должен мешать увидеть сам трейсбек.
    stderr = outcome.stderr.decode(_CHILD_IO_ENCODING, errors="replace")

    if outcome.returncode != 0:
        # Процесс, убитый сигналом (segfault, OOM-killer, отрицательный
        # returncode), не оставляет stderr. Пустая строка ошибки делала такой
        # результат неотличимым от WA в статистике run_tests — она считает
        # ошибки по truthiness поля error, и RE молча попадал в failed
        # (issue #625). Подставляем осмысленное сообщение с кодом выхода.
        return _fail_result(
            case,
            error=stderr.strip() or f"Process exited with code {outcome.returncode} (no stderr)",
            verdict="RE",
            time=outcome.elapsed,
            memory=outcome.peak_memory_mb,
            exit_code=outcome.returncode,
        )

    # issue #1031: декодируем СТРОГО — и только здесь, после ветки RE. Прежний
    # `errors="replace"` схлопывал любые непредставимые байты в один и тот же
    # `�`, поэтому РАЗНЫЕ выводы становились равны: `b"\x80"`, `b"\xff"` и
    # `b"\xfe"` все давали `AC` против ожидания из одного символа замены. Это
    # ложное принятие неверного решения — тот же класс, что #932 и #940.
    #
    # Порядок важен: строгий декод стоит ПОСЛЕ проверки `returncode`, иначе
    # упавшее решение, успевшее напечатать битый байт, получало бы WA вместо
    # честного RE — то есть диагноз подменялся бы на ходу.
    try:
        stdout = outcome.stdout.decode(_CHILD_IO_ENCODING)
    except UnicodeDecodeError as exc:
        return _undecodable_output_result(case, outcome, exc)

    # issue #843: разбор только по настоящим переводам строки. Прежний
    # `splitlines()` резал вывод ещё по восьми управляющим символам, из-за чего
    # `a<VT>b` в одну строку признавалось равным двум настоящим строкам — AC на
    # неверном решении.
    actual_lines = split_output_lines(stdout)
    # issue #1111: режим сравнения. `stepik` (дефолт) не различает того, чего не
    # различает чекер платформы — хвостовые пробелы строки и хвостовые пустые
    # строки; `strict` сверяет побайтово. Нормализуются ОБЕ стороны, но только
    # для решения «passed»: `output`/`expected`/`diff` остаются исходными, иначе
    # студент увидел бы не свой вывод, а его причёсанную копию.
    compare_actual, compare_expected = _lines_for_compare(actual_lines, case.expected_lines)
    passed = compare_actual == compare_expected
    if not passed and len(compare_actual) == len(compare_expected):
        # issue #940: толерантность к записи float сохранена, но она больше не
        # стирает незначащие нули ожидания: `12.30` против `12.3` — не «та же
        # величина», а невыполненное требование «до сотых».
        passed = all(
            floats_equal_with_precision(a, e)
            for a, e in zip(compare_actual, compare_expected, strict=True)
        )
    diff_str = ""
    if not passed:
        diff_str = "\n".join(
            difflib.unified_diff(
                case.expected_lines,
                actual_lines,
                fromfile="expected",
                tofile="actual",
                lineterm="",
            )
        )

    # issue #935 (RUN-1-02/QA-1-04): факт обрезки вывода доходил только до
    # ветки returncode != 0, а здесь `error` был захардкожен пустым. Решение,
    # напечатавшее больше `max_output_bytes`, получало обычный WA — студент
    # искал несуществующую ошибку в своём коде, а причина была в лимите
    # грейдера. Пометка живёт в stderr (в stdout её класть нельзя — он
    # сравнивается с ожиданием), поэтому переносим её в `error` как есть.
    note = ""
    if not passed and TRUNCATION_MARKER in stderr:
        note = next(
            (line.strip() for line in stderr.splitlines() if TRUNCATION_MARKER in line),
            TRUNCATION_MARKER,
        )

    return {
        "passed": passed,
        "output": actual_lines,
        "expected": case.expected_lines,
        "diff": diff_str,
        "time": outcome.elapsed,
        "memory": outcome.peak_memory_mb,
        "error": note,
        "timed_out": False,
        "verdict": "AC" if passed else "WA",
        "exit_code": outcome.returncode,
    }


def run_single_test(
    solution_path: pathlib.Path,
    case: TestCase,
    *,
    timeout: float | None = None,
    measure_memory: bool | None = None,
    cancel_event: threading.Event | None = None,
    max_memory_mb: int | None = None,
) -> CaseResult:
    """Запустить одно решение на одном тест-кейсе и вернуть словарь с результатами.

    Тонкий оркестратор (issue #406): ``_prepare_run_spec`` выбирает стратегию
    (stdin/function-wrapper) и собирает ``RunSpec`` либо возвращает prep-ошибку;
    ``run_spec()`` исполняет spec через активный Runner; ``_map_outcome_to_result``
    маппит сырой ``RunOutcome`` в словарь-результат. Временный wrapper-файл
    function-mode удаляется здесь, после запуска.

    Для test_type='stdin'  — запускает решение напрямую, подаёт stdin.
    Для test_type='function' — генерирует временный wrapper-скрипт,
      который импортирует функцию и вызывает её с аргументами из input_data.
      Файл решения при этом не модифицируется.

    Возвращаемый словарь:
        passed    (bool)   — прошёл ли тест
        output    (list)   — фактический вывод (строки)
        expected  (list)   — ожидаемый вывод (строки)
        diff      (str)    — unified diff при несовпадении
        time      (float)  — время выполнения в секундах
        memory    (float)  — пик памяти в МБ (0 если measure_memory=False)
        error     (str)    — сообщение об ошибке (пустая = нет ошибки)
        timed_out (bool)   — истёк ли таймаут
        exit_code (int | None) — код возврата процесса решения; None, если
            процесс не завершился нормально (ошибка запуска/таймаут) —
            issue #125, ErrorCard.exit_code в web-слое.
    """
    # issue #830 (ARCH-04): значение конфига читается в момент ВЫЗОВА, а не
    # вмораживается в дефолт при импорте модуля.
    timeout = get_config().timeout_seconds if timeout is None else timeout
    measure_memory = get_config().measure_child_memory if measure_memory is None else measure_memory
    plan = _prepare_run_spec(
        solution_path,
        case,
        timeout=timeout,
        measure_memory=measure_memory,
        cancel_event=cancel_event,
        max_memory_mb=max_memory_mb,
    )
    if plan.error is not None:
        # prep-ошибка (нет function_name / невалидный wrapper) → RE без запуска.
        return _fail_result(case, error=plan.error, verdict="RE")
    if plan.spec is None:  # pragma: no cover — инвариант _RunPlan: error is None ⇒ spec задан
        return _fail_result(case, error="run spec preparation failed", verdict="RE")

    try:
        # issue #640: через публичный run_spec(), а не прямой _RUNNER.run — так
        # `_RUNNER.run(spec)` живёт в единственном месте (run_spec), которое и
        # станет точкой выбора per-request Runner'а при серверном пивоте.
        outcome = run_spec(plan.spec)
    finally:
        # issue #945: сносим каталог целиком, а не один файл — приватный каталог
        # и есть то, что защищает исполнение, оставлять его пустым незачем.
        # ignore_errors — уборка не должна ронять уже посчитанный вердикт.
        if plan.tmp_wrapper_dir is not None:
            shutil.rmtree(plan.tmp_wrapper_dir, ignore_errors=True)
        elif plan.tmp_wrapper_path is not None:
            with contextlib.suppress(OSError):
                plan.tmp_wrapper_path.unlink()

    return _map_outcome_to_result(outcome, case, timeout)


def _borrowed_test_dir_warning(solution_path: pathlib.Path, test_dir: pathlib.Path) -> str | None:
    """Сообщение о наборе, взятом не из папки решения (issue #917, RUN-2-08/PY-1-06).

    ``resolve_test_dir`` умеет подниматься на уровень выше: решение в подпапке без
    собственных тестов грейдится набором родительского каталога. Приём законный —
    так лежат курсы, где один набор общий на несколько файлов, — но **молчаливый**:
    если наверху тесты другой задачи, пользователь видит «0/5 пройдено» и считает
    ошибочным своё решение, а не подбор набора.

    Возвращает ``None``, когда набор лежит в папке решения или внутри неё
    (``tests/``, ``<stem>/``) — то есть в штатном случае, где предупреждать не о чем.
    """
    solution_dir = solution_path.resolve().parent
    tests_dir = test_dir.resolve()
    if tests_dir == solution_dir or solution_dir in tests_dir.parents:
        return None
    return (
        f"{solution_path.name}: рядом с решением тест-кейсов нет — набор взят из "
        f"{tests_dir}. Если этот набор относится к другой задаче, вердикт к решению "
        "отношения не имеет: положите тесты в папку решения или в её подпапку tests/."
    )


def run_tests(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    verbose: bool = False,
    verbose_callback: Callable[[TestCase, CaseResult], None] | None = None,
    timeout: float | None = None,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
    max_memory_mb: int | None = None,
) -> SolutionResult:
    """Запустить все тест-кейсы для решения и собрать статистику.

    verbose_callback: вызывается для каждого кейса при verbose=True (получает
        TestCase и результирующий dict run_single_test()); печать — забота
        вызывающей стороны (core/reporter.print_case_verbose), а не этой
        функции (issue #45 A-02 — устраняет обратный импорт Application/Logic
        → Application/UI). Если verbose=True, а callback не передан — кейсы
        просто не печатаются.

    progress_callback/cancel_event (issue #262, оба по умолчанию None — CLI и
    синхронный ``/api/grade`` их не передают, поведение не меняется):
    ``progress_callback`` вызывается с ``1`` после каждого завершённого кейса
    (сколько именно кейсов — знает вызывающая сторона, эта функция ничего не
    знает про глобальный total из web-job; см. ``web/runs.py``).
    ``cancel_event`` проверяется ПЕРЕД каждым кейсом (не начинать новый
    subprocess-запуск после отмены) и прокидывается в ``run_single_test()``,
    которая может прервать уже запущенный кейс через ``LocalRunner``-поллинг.

    Возвращаемый словарь:
        total      (int)   — число тест-кейсов
        passed     (int)   — прошло
        failed     (int)   — провалилось
        errors     (int)   — ошибки выполнения
        total_time (float) — суммарное время
        avg_time   (float) — среднее время на тест
        peak_memory_mb (float) — пик памяти (МБ)
        first_fail (int | None) — индекс первого упавшего теста
        warnings   (list[str]) — предупреждения загрузки набора (issue #935):
                             рассогласование блоков формата 3, непарные файлы,
                             смешение форматов, а также набор, взятый не из папки
                             решения (issue #917). Пустой список — набор полон и
                             принадлежит решению
        cases      (list)  — детальные результаты по каждому кейсу; каждый
                             включает "stdin" (вход кейса, issue #397)
    """
    # issue #830 (ARCH-04): значение конфига читается в момент ВЫЗОВА, а не
    # вмораживается в дефолт при импорте модуля.
    timeout = get_config().timeout_seconds if timeout is None else timeout
    # issue #935 (RUN-2-05): загрузчик предупреждает о неполном наборе через
    # `warnings.warn`, то есть в stderr — машиночитаемый вывод об этом молчал,
    # и CI не отличал полный прогон от урезанного. Ловим предупреждения здесь и
    # переносим их в результат: рассогласование блоков формата 3, непарные
    # файлы, смешение форматов — всё, из-за чего «OK N/N» относится не ко
    # всему набору.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        test_cases = load_test_cases(test_dir)
    load_warnings = [str(w.message) for w in caught]
    for message in caught:
        warnings.warn_explicit(message.message, message.category, message.filename, message.lineno)
    # issue #917 (RUN-2-08/PY-1-06): набор, одолженный у родительской папки, —
    # такое же «загружено не то, что думает пользователь», как и усечённый набор
    # выше, поэтому идёт тем же каналом: и в stderr, и в машиночитаемые warnings.
    borrowed = _borrowed_test_dir_warning(solution_path, test_dir)
    if borrowed is not None:
        load_warnings.append(borrowed)
        warnings.warn(borrowed, stacklevel=2)
    # Определяем режим запуска один раз для всех тест-кейсов.
    _apply_run_mode_override(test_cases, solution_path, test_dir)

    results: list[CaseResult] = []
    total_time = 0.0
    passed = 0
    failed = 0
    errors = 0
    first_fail: int | None = None
    peak_mb = 0.0

    for case in test_cases:
        if cancel_event is not None and cancel_event.is_set():
            break
        r = run_single_test(
            solution_path,
            case,
            timeout=timeout,
            cancel_event=cancel_event,
            max_memory_mb=max_memory_mb,
        )
        # issue #397: приложить stdin кейса к результату — web-презентация
        # (grade_path → ErrorCard) больше не перечитывает тест-кейсы вторым
        # проходом ради stdin. Дешевле и без zip по позиции (снимает хрупкость
        # #422 на отмене).
        r["stdin"] = "\n".join(case.input_lines)
        results.append(r)
        total_time += r["time"]
        peak_mb = max(peak_mb, r["memory"])

        if r.get("verdict") == "CANCELLED":
            # Отмена прогона — не вердикт о решении (issue #625). Раньше кейс
            # попадал в errors и выставлял first_fail, из-за чего UI показывал
            # «первый упавший тест» там, где пользователь просто нажал «Отмена».
            # Кейс остаётся в cases/total, чтобы было видно, на чём прервались.
            pass
        elif r["error"]:
            errors += 1
            if first_fail is None:
                first_fail = case.index
        elif r["passed"]:
            passed += 1
        else:
            failed += 1
            if first_fail is None:
                first_fail = case.index

        if verbose and verbose_callback is not None:
            verbose_callback(case, r)
        if progress_callback is not None:
            progress_callback(1)

    # len(results), не len(test_cases): при отмене (cancel_event) цикл
    # прерывается раньше — total должен отражать фактически прогнанные кейсы,
    # иначе avg_time занижается на незавершённые. В обычном (не отменённом)
    # случае оба значения совпадают — существующее поведение не меняется.
    total = len(results)
    avg_time = total_time / total if total else 0.0

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_time": total_time,
        "avg_time": avg_time,
        "peak_memory_mb": peak_mb,
        "first_fail": first_fail,
        # issue #935: предупреждения загрузки набора — часть результата, а не
        # только строка в stderr. Пустой список в обычном прогоне, поэтому
        # потребители контракта (web, кэш, тесты) не ломаются.
        "warnings": load_warnings,
        "cases": results,
    }


def preflight_solution(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    timeout: float | None = None,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
    max_memory_mb: int | None = None,
) -> dict[str, Any]:
    """Проверить решение ОДИН раз перед замером скорости (issue #729).

    Режимы 3/4 сравнивают решения по времени, и это осмысленно только для кода,
    который проходит тесты. Без такой проверки в сравнение попадало решение с
    неверным выводом: `run_benchmark` меряет время, не сверяя результат, — WA
    честно получал медиану и место в рейтинге. Падающее с ошибкой отсеивалось
    самим замером, но лишь по факту первого запуска и с сырым traceback вместо
    внятной причины.

    Возвращает ``{"ok", "passed", "total", "verdict", "case", "cancelled"}``:
    ``ok=True`` — решение допускается к замеру;
    ``verdict`` — первый непрошедший вердикт (``WA``/``RE``/``TLE``) или ``""``;
    ``case`` — НОМЕР этого кейса, начиная с 1 (``0``, если провала нет,
    issue #1005/``MTX-3-05``): режимы 1/2 в отчёте кейс называют, а 3/4
    говорили только «не прошёл проверку» — воспроизвести падение было не с
    чего, хотя номер известен здесь же;
    ``cancelled`` — прогон прерван (``cancel_event``), решение не оценено.
    Текст для пользователя формирует UI-слой: тут только факты, без локали.

    Отсутствие тест-кейсов (``total == 0``) — НЕ провал пре-флайта: сказать
    «не прошло тесты» про решение, которое не с чем сверить, было бы неправдой.
    Такой случай пропускается дальше, и о нём сообщает сам замер своим обычным
    «нет тест-кейсов» — там это уже описанное поведение.
    """
    # issue #830 (ARCH-04): значение конфига читается в момент ВЫЗОВА, а не
    # вмораживается в дефолт при импорте модуля.
    timeout = get_config().timeout_seconds if timeout is None else timeout
    result = run_tests(
        solution_path,
        test_dir,
        timeout=timeout,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        max_memory_mb=max_memory_mb,
    )
    cases = result.get("cases", [])
    cancelled = any(case.get("verdict") == "CANCELLED" for case in cases)
    # Номер кейса — его позиция в наборе (1-based): собственного поля с номером
    # у кейса нет, порядок и есть нумерация, как в отчётах режимов 1/2.
    bad_no, bad = next(
        (
            (index, case)
            for index, case in enumerate(cases, 1)
            if case.get("verdict") not in ("AC", "CANCELLED")
        ),
        (0, None),
    )
    total = int(result.get("total", 0))
    passed = int(result.get("passed", 0))
    return {
        "ok": not cancelled and (total == 0 or passed == total),
        "passed": passed,
        "total": total,
        "verdict": str(bad.get("verdict", "")) if bad else "",
        "case": bad_no,
        "cancelled": cancelled,
    }


def run_benchmark(
    solution_path: pathlib.Path,
    test_dir: pathlib.Path,
    *,
    timeout: float | None = None,
    repeats: int = 15,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
    max_memory_mb: int | None = None,
) -> BenchResult:
    """Запустить все тест-кейсы в режиме benchmark и собрать статистику времени.

    Аргумент repeats задаёт число повторений каждого тест-кейса.
    Соответствует профилям нагрузки: low=5, medium=15, high=50, custom=5..100.

    progress_callback/cancel_event (issue #262, по умолчанию None — поведение
    без них не меняется): ``progress_callback`` тикает после каждого
    единичного повтора (case × repeat), а не только после кейса целиком —
    самая мелкая гранулярность, где это дешевле всего (bench — самый долгий
    путь, который и жаловался issue). ``cancel_event`` проверяется перед
    каждым повтором; ранний выход помечается ``"cancelled": True`` в
    результате (отличимо от истечения timeout/иной ошибки).

    Возвращаемый словарь:
        runs       (int)   — число запусков (test_cases * repeats)
        min/max/mean/median/stdev (float) — статистика времени (секунды)
        peak_memory_mb (float)
        relative   (float) — задаётся снаружи при сравнении
        verdict    (str)   — задаётся снаружи
        error      (str)   — пустая строка если нет ошибок
    """
    # issue #830 (ARCH-04): значение конфига читается в момент ВЫЗОВА, а не
    # вмораживается в дефолт при импорте модуля.
    timeout = get_config().timeout_seconds if timeout is None else timeout
    test_cases = load_test_cases(test_dir)
    # Определяем режим запуска один раз — как в run_tests().
    # Иначе function-mode задачи прогоняются в неверном stdin-режиме.
    _apply_run_mode_override(test_cases, solution_path, test_dir)

    times: list[float] = []
    peak_mb = 0.0

    for case in test_cases:
        for _ in range(max(1, repeats)):
            if cancel_event is not None and cancel_event.is_set():
                return {"error": "cancelled", "runs": 0, "cancelled": True}
            r = run_single_test(
                solution_path,
                case,
                timeout=timeout,
                cancel_event=cancel_event,
                max_memory_mb=max_memory_mb,
            )
            if r.get("verdict") == "CANCELLED":
                return {"error": r["error"], "runs": 0, "cancelled": True}
            if r["error"] or r["timed_out"]:
                return {"error": r["error"] or "timeout", "runs": 0}
            times.append(r["time"])
            peak_mb = max(peak_mb, r["memory"])
            if progress_callback is not None:
                progress_callback(1)

    if not times:
        return {"error": "no test cases", "runs": 0}

    bench_stats = BenchStats(timings=times)
    return {
        "runs": len(times),
        "min": bench_stats.min,
        "max": bench_stats.max,
        "mean": bench_stats.mean,
        "median": bench_stats.median,
        "stdev": bench_stats.stdev,
        "peak_memory_mb": peak_mb,
        "relative": 1.0,
        "verdict": "SIMILAR",
        "error": "",
    }


def _micro_stats(times: list[float]) -> dict[str, float]:
    """Вычислить статистику по списку замеров времени."""
    bench_stats = BenchStats(timings=times)
    return {
        "min": bench_stats.min,
        "max": bench_stats.max,
        "mean": bench_stats.mean,
        "median": bench_stats.median,
        "stdev": bench_stats.stdev,
    }


def _verdict(relative: float) -> str:
    """Вернуть текстовый вердикт по относительному времени."""
    if relative <= SIMILAR_THRESHOLD:
        return "SIMILAR"
    if relative <= MUCH_SLOWER_THRESHOLD:
        return "SLOWER"
    return "MUCH_SLOWER"


def run_microbench_mode(
    solution_paths: list[pathlib.Path],
    test_dir: pathlib.Path,
    *,
    number: int = 1000,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[pathlib.Path, BenchResult]:
    """Запустить timeit-microbench для нескольких решений и вернуть сводную статистику.

    peak_memory_mb (Issue #25) — максимум по всем кейсам решения: RSS через
    psutil для function-call блоков (run_single_test, как в run_benchmark),
    пик Python-heap через tracemalloc для stdin-блоков (run_microbench) —
    два разных метода измерения, см. докстринг core.microbench_runner.

    progress_callback/cancel_event (issue #262, по умолчанию None):
    гранулярность здесь ГРУБЕЕ, чем в ``run_benchmark`` — один тик на решение
    целиком, не на кейс/повтор. ``timeit``-цикл внутри ``run_microbench()``
    не прерываем без переделки ``core/microbench_runner.py`` (вне scope
    issue #262) — отмена проверяется только МЕЖДУ решениями, текущее
    решение в процессе микробенчмарка досчитывается до конца.
    """
    test_cases = load_test_cases(test_dir)
    if not test_cases:
        return {}

    cases_to_bench = test_cases[:MICROBENCH_MAX_CASES]
    results: dict[pathlib.Path, BenchResult] = {}

    for path in solution_paths:
        if cancel_event is not None and cancel_event.is_set():
            break
        code = path.read_text(encoding=ENCODING)

        # issue #623: режим запуска определяется РЕШЕНИЕМ (meta.json/AST), а не
        # только per-case .type — как это уже делают run_tests и run_benchmark.
        # Без override function-only решение уходило на stdin-путь, где микробенч
        # мерит лишь время определения функций и выдаёт бессмысленный SIMILAR.
        # Копия кейсов обязательна: _apply_run_mode_override мутирует список на
        # месте и не откатывает "function" обратно, поэтому общий список протёк
        # бы на следующие решения группы.
        cases_for_path = _apply_run_mode_override(
            [replace(case) for case in cases_to_bench], path, test_dir
        )

        all_times: list[float] = []
        peak_mb = 0.0
        for case in cases_for_path:
            input_data = "\n".join(case.input_lines)

            # Детектор получает выпрямленный блок (issue #783: разбор формата 3
            # сохраняет пробелы по краям), а stdin_data ниже — исходный.
            if case.test_type == "function" and _is_python_code_block(input_data.strip()):
                # Function-call блок — это Python-код, а не stdin.
                # timeit/exec тут не годится: используем subprocess-тайминг
                # через run_single_test (менее точно, зато корректно).
                # run_single_test уже измеряет RSS через psutil (как в режиме 3).
                sub_repeats = max(1, number // 50)
                case_times: list[float] = []
                for _ in range(sub_repeats):
                    r = run_single_test(path, case, timeout=60.0)
                    if r["error"] or r["timed_out"]:
                        results[path] = {
                            "error": f"test {case.index}: {r['error'] or 'timeout'}",
                            "runs": 0,
                        }
                        break
                    case_times.append(r["time"])
                    peak_mb = max(peak_mb, r["memory"])
                else:
                    all_times.extend(case_times)
                    continue
                break

            stdin_data = input_data + "\n"
            bench = run_microbench(
                code, stdin_data=stdin_data, number=number, max_memory_mb=CONFIG.max_memory_mb
            )
            if bench["error"]:
                results[path] = {"error": f"test {case.index}: {bench['error']}", "runs": 0}
                break
            all_times.extend(bench["times"])
            peak_mb = max(peak_mb, bench["peak_memory_mb"])
        else:
            if not all_times:
                # issue #831: ``run_microbench`` возвращает ``times=[]`` при
                # ``error=""`` — так завершается решение, обрывающее замер само
                # (``raise SystemExit``, ``os._exit``): код выхода нулевой,
                # тайминги не напечатаны. Раньше это уходило в ``_micro_stats([])``
                # и роняло ВЕСЬ режим 4 трейсбеком ``min() iterable argument is
                # empty`` — падало не решение, а грейдер.
                results[path] = {"error": "microbench: нет замеров", "runs": 0}
                continue
            stats = _micro_stats(all_times)
            results[path] = {
                "runs": len(all_times),
                "peak_memory_mb": peak_mb,
                "min": stats["min"],
                "max": stats["max"],
                "mean": stats["mean"],
                "median": stats["median"],
                "stdev": stats["stdev"],
            }

        if progress_callback is not None:
            progress_callback(1)

    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    return results
