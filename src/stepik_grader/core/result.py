"""result.py — типизированная модель результата одного тест-кейса (issue #112/#113).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Контракт полей — канонично в
[`docs/dev/result-contract.md § Case result`](../../../docs/dev/result-contract.md).

``run_single_test()`` **аннотирован** ``CaseResult``, а ``run_tests()`` —
``SolutionResult`` (issue #442); runtime-представление при этом осталось
обычным ``dict``, потому что это публичный контракт CLI/Web/API (issue #116) и
менять его форму не входит в задачу. ``TypedDict`` даёт mypy ловить опечатки
ключей, ничего не меняя в том, что видит потребитель.

``TestResult`` формализует ту же форму как frozen-dataclass — для мест, где
нужна типобезопасность поверх словаря; сейчас это
``core.reporter.print_case_verbose`` (issue #114).

issue #996 (PAIR-2-04): прежний абзац утверждал, что ``run_single_test``
возвращает ``dict[str, Any]``, и расходился и с кодом, и с
``result-contract.md`` — читатель докстринга получал устаревшее описание
типа прямо в модуле, который этот тип и объявляет.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict

__all__ = ["BenchResult", "CaseResult", "SolutionResult", "TestResult", "Verdict"]


def _as_exit_code(value: Any) -> int | None:
    """Код возврата решения из словаря; ``None`` — не измерялся или мусор.

    Терпимость та же, что у остальных полей ``from_dict``: словарь приходит из
    JSON и от сторонних потребителей контракта, и одно испорченное значение не
    должно ронять разбор всего результата.
    """
    if value is None or isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


Verdict = Literal["AC", "WA", "TLE", "RE", "CANCELLED", "SANDBOX_VIOLATION"]


class CaseResult(TypedDict):
    """Типизированная форма case-result dict'а — возврата ``run_single_test`` (issue #442).

    Формализует тот же контракт, что описан в
    [`docs/dev/result-contract.md § Case result`](../../../docs/dev/result-contract.md)
    и в docstring ``run_single_test`` (источник истины полей). В отличие от
    frozen-dataclass ``TestResult`` (типобезопасный носитель формы для
    reporter'а), ``CaseResult`` — это тип самого словаря, который ядро гоняет по
    цепочке CLI→web→pytest-плагин: даёт mypy ловить опечатки ключей и типы полей
    без изменения runtime-представления (по-прежнему обычный ``dict``).

    Все девять контрактных полей + ``exit_code`` присутствуют всегда (их пишут
    ``_fail_result`` и ``_map_outcome_to_result``). ``stdin`` — ``NotRequired``:
    его дописывает ``run_tests`` каждому кейсу постфактум (issue #397), сам
    ``run_single_test`` его не проставляет.
    """

    passed: bool
    verdict: Verdict
    output: list[str]
    expected: list[str]
    diff: str
    time: float
    memory: float
    error: str
    timed_out: bool
    exit_code: int | None
    stdin: NotRequired[str]


class SolutionResult(TypedDict):
    """Типизированная форма агрегата ``run_tests`` — результата по РЕШЕНИЮ (issue #831).

    Тот же контракт, что в
    [`docs/dev/result-contract.md § Solution result`](../../../docs/dev/result-contract.md)
    и в docstring ``run_tests``. Как и ``CaseResult``, это тип самого словаря:
    runtime-представление не меняется, но mypy начинает ловить опечатки ключей
    у потребителей (``cli/commands``, ``web/viewmodels``, ``core/history_recording``,
    ``pytest_plugin``) — самый широко разошедшийся контракт проекта до сих пор был
    единственным без модели.

    ``first_fail`` — индекс первого упавшего кейса либо ``None``, если всё прошло.
    """

    total: int
    passed: int
    failed: int
    errors: int
    total_time: float
    avg_time: float
    peak_memory_mb: float
    first_fail: int | None
    # issue #935: предупреждения загрузки набора кейсов — рассогласование
    # блоков формата 3, непарные файлы, смешение форматов. NotRequired, потому
    # что словари результата собирают и другие пути (кэш, web-адаптеры), а
    # обязательный ключ сломал бы их молча.
    warnings: NotRequired[list[str]]
    cases: list[CaseResult]


class BenchResult(TypedDict):
    """Типизированная форма результата ``run_benchmark`` — замера ОДНОГО решения.

    Обязательный ключ один — ``runs``: ранние выходы (нет кейсов, ошибка кейса,
    отмена) возвращают ``{"error": ..., "runs": 0}`` без статистики, и это часть
    контракта, а не дефект. Поэтому статистические поля — ``NotRequired``, и
    потребитель обязан проверять ``error``/``runs`` перед чтением медианы.

    ``relative``/``verdict`` проставляются ранжированием
    (``microbench_runner.apply_relative_ranking``/``apply_reference_ranking``)
    и в одиночном замере несут нейтральные значения (``1.0``/``"SIMILAR"``).
    """

    runs: int
    error: NotRequired[str]
    cancelled: NotRequired[bool]
    min: NotRequired[float]
    max: NotRequired[float]
    mean: NotRequired[float]
    median: NotRequired[float]
    stdev: NotRequired[float]
    peak_memory_mb: NotRequired[float]
    relative: NotRequired[float]
    verdict: NotRequired[str]


@dataclass(frozen=True)
class TestResult:
    """Результат одного тест-кейса (case result — docs/dev/result-contract.md).

    ``verdict`` — производное поле по контракту (согласовано с
    ``passed``/``timed_out``/``error``), но здесь не пересчитывается и не
    валидируется — за согласованность отвечает вызывающая сторона
    (``run_single_test``), это только типизированный носитель формы.
    """

    # Имя начинается с "Test" — совпадает с python_classes = ["Test*"] в
    # pyproject.toml, поэтому pytest пытался бы собрать её как тестовый класс.
    # Явно исключаем (это модель данных, не тест-кейс).
    __test__ = False

    passed: bool
    verdict: Verdict
    output: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    diff: str = ""
    time: float = 0.0
    memory: float = 0.0
    error: str = ""
    timed_out: bool = False
    # issue #996 (ARCH-1-06): поле контрактное — его пишут и `_fail_result`, и
    # `_map_outcome_to_result`, а `CaseResult` объявляет обязательным. Без него
    # `to_dict()` возвращал словарь, который контракту НЕ удовлетворяет: код
    # возврата решения терялся на round-trip, хотя именно по нему отличают
    # «упало» от «отработало с ненулевым статусом». Дефолт `None` — «не
    # измерялось» (кейс не запускался вовсе).
    exit_code: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TestResult:
        """Собрать из case-result dict (форма ``run_single_test()``).

        Терпимо к отсутствующим необязательным полям: ``verdict`` по
        умолчанию выводится из ``passed`` (``AC``/``WA``) — та же эвристика,
        которую раньше вручную применял ``reporter.print_case_verbose`` для
        неполных dict'ов (в т.ч. в тестах). Обязателен только ``passed``.
        """
        passed = bool(data["passed"])
        verdict = data.get("verdict") or ("AC" if passed else "WA")
        return cls(
            passed=passed,
            verdict=verdict,  # type: ignore[arg-type]
            output=list(data.get("output", [])),
            expected=list(data.get("expected", [])),
            diff=str(data.get("diff", "")),
            time=float(data.get("time", 0.0)),
            memory=float(data.get("memory", 0.0)),
            error=str(data.get("error", "")),
            timed_out=bool(data.get("timed_out", False)),
            exit_code=_as_exit_code(data.get("exit_code")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать обратно в case-result dict (форма ``run_single_test()``)."""
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "output": list(self.output),
            "expected": list(self.expected),
            "diff": self.diff,
            "time": self.time,
            "memory": self.memory,
            "error": self.error,
            "timed_out": self.timed_out,
            "exit_code": self.exit_code,
        }
