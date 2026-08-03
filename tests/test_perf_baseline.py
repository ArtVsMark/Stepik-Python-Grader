"""Tests for scripts/perf_baseline.py — базовые замеры грейдера (issue #839).

Скрипт лежит в scripts/ (не на sys.path) — грузим его по пути, тем же приёмом,
что и test_combine_coverage.py.

Настоящие замеры здесь НЕ прогоняются: полный отчёт занимает полторы минуты и
измеряет машину, а не корректность. Проверяется механика — что счётчик считает
медиану, что отчёт собирается, что CLI не падает, — и что скрипт остаётся
рабочим после правок в измеряемых API.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "perf_baseline.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_perf_baseline", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрация в sys.modules ДО exec_module обязательна: в скрипте есть
    # dataclass, а `dataclasses` резолвит аннотации через
    # `sys.modules[cls.__module__]` — без записи там получается None и сборка
    # падает на импорте (в отличие от соседних скриптов без dataclass).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


class TestMeasure:
    def test_returns_median_of_samples(self) -> None:
        """Медиана, а не среднее: один выброс не должен сдвигать результат.

        Сравнивать версии по среднему нельзя — на рабочей машине хватает
        одного всплеска (антивирус, своп), чтобы цифра уехала в разы.
        """
        delays = [0.03, 0.001, 0.001, 0.001, 0.001]
        it = iter(delays)

        def _fake_work() -> None:
            import time

            time.sleep(next(it))

        result = _MODULE.measure("проба", _fake_work, repeats=len(delays))

        assert result.repeats == len(delays)
        assert result.min_s <= result.median_s <= result.max_s
        # Медиана ближе к типичному значению, чем к выбросу.
        assert result.median_s < 0.02

    def test_keeps_name_and_note(self) -> None:
        result = _MODULE.measure("имя", lambda: None, repeats=1, note="пояснение")
        assert result.name == "имя"
        assert result.note == "пояснение"


class TestTaskFixture:
    def test_generates_requested_number_of_cases(self, tmp_path: pathlib.Path) -> None:
        """Задача-стенд собирается в формате 3 и содержит ровно N кейсов."""
        solution = _MODULE._make_task(tmp_path / "task", cases=7)

        assert solution.is_file()
        inputs = (tmp_path / "task" / "tests" / "input.txt").read_text(encoding="utf-8")
        assert inputs.count("# TEST_") == 7

    def test_generated_task_is_actually_gradeable(self, tmp_path: pathlib.Path) -> None:
        """Стенд обязан проходить проверку — иначе замер мерил бы путь ошибки.

        Без этого можно годами мерить «сколько стоит уронить решение»: время
        похоже, а сценарий другой.
        """
        from stepik_grader.core.grader_core import run_tests

        solution = _MODULE._make_task(tmp_path / "task", cases=3)
        result = run_tests(solution, tmp_path / "task" / "tests")

        assert result["passed"] == 3, result


class TestCli:
    def test_json_output_is_parseable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`--output json` даёт валидный JSON с метаданными и замерами.

        Гоняем самый дешёвый набор: два кейса и один повтор — проверяется форма
        отчёта, а не производительность машины.
        """
        assert _MODULE.main(["--output", "json", "--quick", "--cases", "2", "--repeats", "1"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["meta"]["кейсов в прогоне"] == "2"
        assert payload["results"], "ни одного замера в отчёте"
        names = {r["name"] for r in payload["results"]}
        assert any("холодный старт" in n for n in names)
        assert any("2 кейсах" in n for n in names)

    def test_text_output_mentions_every_scenario(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _MODULE.main(["--quick", "--cases", "2", "--repeats", "1"]) == 0

        out = capsys.readouterr().out
        assert "Базовые замеры" in out
        assert "медиана" in out
        assert "на один кейс, мс" in out  # производная метрика, ради которой всё и меряется

    def test_quick_skips_the_long_scenario(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`--quick` убирает параллельный сценарий — он самый долгий."""
        _MODULE.main(["--output", "json", "--quick", "--cases", "2", "--repeats", "1"])
        quick_names = {r["name"] for r in json.loads(capsys.readouterr().out)["results"]}

        assert not any("параллельных" in n for n in quick_names)
