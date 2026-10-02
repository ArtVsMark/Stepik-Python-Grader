"""Маркер сверяется целиком, а не началом (issue #1384, правило 141).

`<!-- ci-failures -->` совпал бы с `<!-- ci-failures-old -->`, а метка
`needs-rebase` — с `needs-rebase-manual`: сравнение началом ошибается в сторону
«прошло», то есть молча берёт чужую запись за свою.

Обе стороны гейта важны одинаково. Он обязан находить подстановку маркера в
`startswith` — и обязан молчать на обычных префиксах (`agent/` у веток,
`test-results-` у отчётов), где начало строки и есть предмет. Гейт, краснеющий
на рабочем коде, отключают целиком.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_marker_matching.py"
    spec = importlib.util.spec_from_file_location("check_marker_matching", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_marker_matching", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


class TestMarkerNames:
    def test_named_marker_is_recognised(self) -> None:
        assert guard.marker_names(ast.parse('MARKER = "что-то"')) == {"MARKER"}

    def test_hidden_comment_is_recognised_by_its_shape(self) -> None:
        """Имя может быть любым — форма значения выдаёт маркер сама."""
        assert guard.marker_names(ast.parse('_TAG = "<!-- ci-failures -->"')) == {"_TAG"}

    def test_prefix_is_not_a_marker(self) -> None:
        """У префикса есть осмысленное продолжение — в этом вся разница."""
        assert guard.marker_names(ast.parse('_BRANCH_PREFIX = "agent/"')) == set()

    def test_generated_prefix_is_not_a_marker_either(self) -> None:
        """Даже если значение начинается как скрытый комментарий: имя решает."""
        source = '_GENERATED_PREFIX = "<!-- СГЕНЕРИРОВАНО"'

        assert guard.marker_names(ast.parse(source)) == set()

    def test_non_string_constant_is_ignored(self) -> None:
        assert guard.marker_names(ast.parse("BUDGET = 90")) == set()


class TestFindings:
    _MARKER = 'MARKER = "<!-- x -->"\n\n\n'

    def test_startswith_on_a_marker_is_flagged(self) -> None:
        source = self._MARKER + "def f(t: str) -> bool:\n    return t.startswith(MARKER)\n"

        problems = guard.markers_matched_by_prefix({"злой.py": source})

        assert problems == [("злой.py", "MARKER")]

    def test_removeprefix_on_a_marker_is_flagged(self) -> None:
        """Срезать маркер началом — та же ошибка, только другой стороной."""
        source = self._MARKER + "def f(t: str) -> str:\n    return t.removeprefix(MARKER)\n"

        assert len(guard.markers_matched_by_prefix({"злой.py": source})) == 1

    def test_containment_passes(self) -> None:
        source = self._MARKER + "def f(t: str) -> bool:\n    return MARKER in t\n"

        assert guard.markers_matched_by_prefix({"добрый.py": source}) == []

    def test_startswith_on_a_prefix_passes(self) -> None:
        source = '_P = "agent/"\n\n\ndef f(t: str) -> bool:\n    return t.startswith(_P)\n'

        assert guard.markers_matched_by_prefix({"добрый.py": source}) == []

    def test_broken_file_is_skipped_not_crashed(self) -> None:
        """Неразбираемый файл — не предмет этого гейта: о нём скажет линтер."""
        assert guard.markers_matched_by_prefix({"битый.py": "def ("}) == []

    def test_live_repository_passes(self) -> None:
        assert guard.markers_matched_by_prefix() == []
