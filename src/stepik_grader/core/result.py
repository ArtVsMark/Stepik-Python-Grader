"""result.py — типизированная модель результата одного тест-кейса (issue #112/#113).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Контракт полей — канонично в
[`docs/result-contract.md § Case result`](../../../docs/result-contract.md).
``run_single_test()`` продолжает возвращать ``dict[str, Any]`` — это уже
задокументированный публичный контракт CLI/Web/API (issue #116), и менять его
форму здесь не входит в задачу. ``TestResult`` формализует ту же форму как
типизированную модель для мест, где нужна типобезопасность поверх словаря —
сейчас: ``core.reporter.print_case_verbose`` (issue #114).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict

__all__ = ["Verdict", "CaseResult", "TestResult"]

Verdict = Literal["AC", "WA", "TLE", "RE", "CANCELLED", "SANDBOX_VIOLATION"]


class CaseResult(TypedDict):
    """Типизированная форма case-result dict'а — возврата ``run_single_test`` (issue #442).

    Формализует тот же контракт, что описан в
    [`docs/result-contract.md § Case result`](../../../docs/result-contract.md)
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


@dataclass(frozen=True)
class TestResult:
    """Результат одного тест-кейса (case result — docs/result-contract.md).

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
        }
