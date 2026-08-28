"""Обрезанное сообщает, что оно обрезано (issue #1384, правило 016).

Урезанный результат без признака обрыва выглядит полным, и вывод по нему делают
как по целому. Тесты держат обе стороны гейта — и особенно вторую: он обязан
молчать на отборе по контракту и на разборе формата, иначе покраснеет на
половине кода и его отключат.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_truncation_marks.py"
    spec = importlib.util.spec_from_file_location("check_truncation_marks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_truncation_marks", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def test_silent_truncation_is_flagged() -> None:
    source = "_MAX = 100\n\n\ndef show(text: str) -> str:\n    return text[:_MAX]\n"

    problems = guard.truncations_without_mark({"молчун.py": source})

    assert problems == [("молчун.py", "show")]


def test_ellipsis_counts_as_a_mark() -> None:
    source = '_MAX = 100\n\n\ndef show(text: str) -> str:\n    return text[:_MAX] + "…"\n'

    assert guard.truncations_without_mark({"честный.py": source}) == []


def test_flag_counts_as_a_mark() -> None:
    source = (
        "_MAX = 100\n\n\ndef show(text: str) -> tuple[str, bool]:\n"
        "    return text[:_MAX], len(text) > _MAX  # truncated\n"
    )

    assert guard.truncations_without_mark({"честный.py": source}) == []


def test_full_length_alongside_counts_as_a_mark() -> None:
    """`{"elems": ..., "n": len(seq)}` честнее флага: видно, сколько было."""
    source = (
        "_MAX = 10\n\n\ndef pack(seq: list) -> dict:\n"
        '    return {"elems": seq[:_MAX], "n": len(seq)}\n'
    )

    assert guard.truncations_without_mark({"трассировщик.py": source}) == []


def test_limit_from_a_parameter_is_a_contract_not_a_truncation() -> None:
    """`select(..., max_top=3)` объявляет предел сигнатурой — молчания нет."""
    source = "def select(items: list, *, max_top: int = 3) -> list:\n    return items[:max_top]\n"

    assert guard.truncations_without_mark({"отбор.py": source}) == []


def test_fixed_small_index_is_format_parsing() -> None:
    """`parts[:2]` — разбор формата; маркер здесь был бы шумом."""
    source = 'def split(text: str) -> list:\n    return text.split(":")[:2]\n'

    assert guard.truncations_without_mark({"разбор.py": source}) == []


def test_window_bounds_are_not_a_limit() -> None:
    """`body[: end - start]` — окно разбора, а не усечение результата."""
    source = "def window(body: str, start: int, end: int) -> str:\n    return body[: end - start]\n"

    assert guard.truncations_without_mark({"окно.py": source}) == []


def test_broken_file_is_skipped_not_crashed() -> None:
    assert guard.truncations_without_mark({"битый.py": "def ("}) == []


def test_live_repository_passes() -> None:
    """Живой предмет: в продукте и скриптах молчаливой обрезки нет."""
    assert guard.truncations_without_mark() == []
