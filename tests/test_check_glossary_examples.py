"""Примеры карточек остаются валидным Python (issue #955).

Храповик исполнения помечал несобирающийся пример как `unverifiable` и проходил
мимо — то есть чем сильнее сломан пример, тем меньше к нему вопросов. Этот гард
закрывает дыру встречным способом, и тесты стерегут именно его свойство
храповика: бюджет не должен молча расти.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_glossary_examples.py"
    spec = importlib.util.spec_from_file_location("check_glossary_examples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_glossary_examples", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _base(tmp_path: pathlib.Path, cards: list[dict[str, Any]]) -> pathlib.Path:
    (tmp_path / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_valid_example_is_not_flagged(tmp_path: pathlib.Path) -> None:
    base = _base(tmp_path, [{"id": "ok", "examples": ["for i in range(2):", "    print(i)"]}])

    assert guard.broken_examples(base) == []


def test_lost_indentation_is_flagged(tmp_path: pathlib.Path) -> None:
    """Ровно тот дефект: тело блока лежит на нулевом уровне."""
    base = _base(tmp_path, [{"id": "плохая", "examples": ["for i in range(2):", "print(i)"]}])

    broken = guard.broken_examples(base)

    assert len(broken) == 1
    assert broken[0][0].endswith("/плохая")
    assert "IndentationError" in broken[0][1] or "SyntaxError" in broken[0][1]


def test_single_line_example_is_fine(tmp_path: pathlib.Path) -> None:
    base = _base(tmp_path, [{"id": "одна", "examples": ["print(len('abc'))  # → 3"]}])

    assert guard.broken_examples(base) == []


def test_card_without_examples_is_ignored(tmp_path: pathlib.Path) -> None:
    base = _base(tmp_path, [{"id": "без-примеров", "term": "что-то"}])

    assert guard.broken_examples(base) == []


def test_live_base_stays_within_budget() -> None:
    """Главное: бюджет не растёт молча — это и есть храповик."""
    broken = guard.broken_examples()

    assert len(broken) <= guard.BUDGET, (
        f"карточек с несобирающимися примерами {len(broken)} при бюджете {guard.BUDGET}. "
        "Бюджет опускают починкой, а не правкой числа."
    )


def test_budget_is_not_wider_than_reality() -> None:
    """Бюджет, оторвавшийся от факта, перестаёт быть храповиком.

    Допуск в 10 карточек — на разницу версий Python: часть примеров использует
    синтаксис, который на 3.12 ещё не разбирается.
    """
    broken = guard.broken_examples()

    assert guard.BUDGET - len(broken) <= 10, (
        f"бюджет {guard.BUDGET} против фактических {len(broken)}: опустите бюджет, "
        "иначе он молча разрешает будущую поломку."
    )
