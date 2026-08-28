"""Наружу уходит число, а не его вид (issue #1384, правило 122).

Форматирование — операция с потерей: клиенту, получившему `"1.5 с"`, придётся
разбирать строку обратно вместе с единицей и округлением, которых он не
выбирал. Сырое число он отформатирует сам и так, как нужно его экрану.

Гейт проверяется обеими сторонами: он обязан находить отформатированную
величину в ответе и обязан молчать на человеческих полях, где строка и есть
содержимое.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_raw_values.py"
    spec = importlib.util.spec_from_file_location("check_raw_values", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_raw_values", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def test_formatted_number_in_a_response_is_flagged() -> None:
    source = 'def f(s: float) -> dict:\n    return {"time": f"{s:.1f} с"}\n'

    problems = guard.formatted_numbers_in_responses({"адаптер.py": source})

    assert problems == [("адаптер.py", "time")]


def test_percentage_is_flagged_too() -> None:
    """Доля — та же величина: клиенту нужна она, а не её вид."""
    source = 'def f(x: float) -> dict:\n    return {"coverage": f"{x:.1%}"}\n'

    assert len(guard.formatted_numbers_in_responses({"адаптер.py": source})) == 1


def test_raw_number_passes() -> None:
    source = 'def f(s: float) -> dict:\n    return {"time_ms": int(s * 1000)}\n'

    assert guard.formatted_numbers_in_responses({"адаптер.py": source}) == []


def test_human_field_is_allowed() -> None:
    """`message` — текст по замыслу: число внутри фразы величиной не является."""
    source = 'def f(s: float) -> dict:\n    return {"message": f"заняло {s:.1f} с"}\n'

    assert guard.formatted_numbers_in_responses({"адаптер.py": source}) == []

    assert "message" in guard.HUMAN_KEYS


def test_plain_interpolation_is_not_a_number() -> None:
    """Без числового спецификатора это подстановка имени, а не форматирование."""
    source = 'def f(name: str) -> dict:\n    return {"path": f"{name}.py"}\n'

    assert guard.formatted_numbers_in_responses({"адаптер.py": source}) == []


def test_broken_file_is_skipped_not_crashed() -> None:
    assert guard.formatted_numbers_in_responses({"битый.py": "def ("}) == []


def test_live_web_layer_passes() -> None:
    """Живой предмет: ответы веб-слоя несут числа, а не их вид."""
    assert guard.formatted_numbers_in_responses() == []
