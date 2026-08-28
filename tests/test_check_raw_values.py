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


class TestGuardsOverFormatted:
    """Правило 137: сторожу показывают сырое значение, а не его вид.

    Отформатированное непусто всегда — `f"{x}"` от `None` это `"None"`, — то
    есть форматирование уничтожает ровно тот признак, по которому сторож узнаёт
    молчание источника. Поэтому обе стороны: что гейт это находит и что он не
    трогает проверки формы, чей предмет как раз отформатированное.
    """

    def _tree(self, tmp_path: pathlib.Path, source: str) -> tuple[pathlib.Path, ...]:
        (tmp_path / "mod.py").write_text(source, encoding="utf-8")
        return (tmp_path,)

    def test_f_string_guard_is_flagged(self, tmp_path: pathlib.Path) -> None:
        roots = self._tree(tmp_path, 'def f(value):\n    if not f"{value}":\n        return None\n')

        found = _load().guards_over_formatted(roots)

        assert found and "f-строкой" in found[0]

    def test_str_call_guard_is_flagged(self, tmp_path: pathlib.Path) -> None:
        roots = self._tree(tmp_path, "def f(value):\n    if str(value):\n        return 1\n")

        assert _load().guards_over_formatted(roots) != []

    def test_format_method_guard_is_flagged(self, tmp_path: pathlib.Path) -> None:
        roots = self._tree(
            tmp_path, 'def f(value):\n    assert "{}".format(value)\n    return value\n'
        )

        assert _load().guards_over_formatted(roots) != []

    def test_raw_guard_passes(self, tmp_path: pathlib.Path) -> None:
        roots = self._tree(tmp_path, "def f(value):\n    if value is None:\n        return None\n")

        assert _load().guards_over_formatted(roots) == []

    def test_formatting_inside_a_larger_test_is_not_a_guard(self, tmp_path: pathlib.Path) -> None:
        """`if str(x) in known` проверяет принадлежность, а не молчание источника."""
        roots = self._tree(
            tmp_path, "def f(value, known):\n    if str(value) in known:\n        return 1\n"
        )

        assert _load().guards_over_formatted(roots) == []

    def test_live_repository_passes(self) -> None:
        assert _load().guards_over_formatted() == []
