"""У проверки три исхода, а не два (issue #1384, правило 039).

«Чисто», «нашли проблему» и «проверка не отработала» — три разных исхода с
тремя разными действиями. Третий теряется чаще всего: скрипт, сходивший в
GitHub и не получивший ответа, печатает то же, что и скрипт, ничего не нашедший.

Гейт проверяется тем, что обязан отвергнуть (правило 140), и обеими сторонами:
он не должен молчать о двух исходах и не должен шуметь на трёх.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_three_outcomes.py"
    spec = importlib.util.spec_from_file_location("check_three_outcomes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_three_outcomes", module)
    spec.loader.exec_module(module)
    return module


guard = _load()

_TWO_OUTCOMES = "import gh_rest\n\n\ndef main() -> int:\n    gh_rest.pull('o/r', 1)\n    return 0\n"
_THREE_OUTCOMES = (
    "import gh_rest\n\n\ndef main() -> int:\n"
    "    try:\n        gh_rest.pull('o/r', 1)\n"
    "    except gh_rest.RateLimited:\n        return gh_rest.EXIT_WAIT\n"
    "    return 0\n"
)


def test_two_outcomes_are_flagged() -> None:
    """«Прав нет» и «нарушений нет» не должны выглядеть одинаково."""
    problems = guard.scripts_without_third_outcome({"ходок.py": _TWO_OUTCOMES})

    assert len(problems) == 1
    assert "отказ источника не отличён" in problems[0][1]


def test_three_outcomes_pass() -> None:
    assert guard.scripts_without_third_outcome({"ходок.py": _THREE_OUTCOMES}) == []


def test_script_without_external_source_is_not_watched() -> None:
    """Предмет узкий намеренно: у чтения своих файлов отказа-состояния нет."""
    assert guard.scripts_without_third_outcome({"свой.py": "import json\n"}) == []


def test_declared_debt_is_skipped() -> None:
    known = next(iter(guard.KNOWN_DEBT))

    assert guard.scripts_without_third_outcome({known: _TWO_OUTCOMES}) == []


def test_every_debt_entry_names_a_reason() -> None:
    """Молча внесённое исключение — отключённая проверка (правило 057)."""
    for name, reason in guard.KNOWN_DEBT.items():
        assert len(reason) > 30, f"{name}: причина без содержания"


def test_return_two_counts_as_the_third_outcome() -> None:
    """Форму не навязываем: код 2 — та же развилка, что и EXIT_WAIT."""
    source = (
        "import gh_rest\n\n\ndef main() -> int:\n"
        "    try:\n        gh_rest.pull('o/r', 1)\n"
        "    except gh_rest.GitHubError:\n        return 2\n"
        "    return 0\n"
    )

    assert guard.scripts_without_third_outcome({"ходок.py": source}) == []


def test_live_repository_passes() -> None:
    """Живой предмет: у всех наших ходоков наружу третий исход отличён."""
    assert guard.scripts_without_third_outcome() == []
