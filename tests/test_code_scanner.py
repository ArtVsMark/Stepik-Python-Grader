"""test_code_scanner.py — golden-набор для scan_code_concepts (issue #367).

Панель «Функции в коде» (2.д): полнота распознавания builtins/методов
(inventory-driven через web-адаптер), синтаксических конструкций и корректный
разбор `os.path.join` (≠ метод `str.join`) и цепочек `x.strip().split()`.
"""

from __future__ import annotations

import pytest

from stepik_grader.glossary.detector import scan_code_concepts
from stepik_grader.web.glossary_adapter import code_terms


def _kinds(code: str) -> dict[str, str]:
    return {concept: kind for concept, (kind, _snippet) in scan_code_concepts(code).items()}


# --- os.path.join ≠ str.join (issue #367, ключевой баг) --------------------


def test_os_path_join_is_module_function_not_str_method() -> None:
    found = _kinds("import os\np = os.path.join(a, b)")
    assert found.get("os.path.join") == "function"
    assert "join" not in found  # НЕ ложный метод str.join


def test_str_join_still_detected_as_method() -> None:
    found = _kinds('s = ",".join(items)')
    assert found.get("join") == "method"
    assert "os.path.join" not in found


def test_os_path_join_and_str_join_coexist() -> None:
    found = _kinds('import os\na = os.path.join(x, y)\nb = "-".join(z)')
    assert found.get("os.path.join") == "function"  # модульный вызов
    assert found.get("join") == "method"  # отдельный метод str.join


def test_import_os_path_binds_top_level_os() -> None:
    # `import os.path` связывает верхнеуровневый `os` — цепочка всё равно рвётся верно
    found = _kinds("import os.path\np = os.path.join(a, b)")
    assert found.get("os.path.join") == "function"


def test_chained_method_call_detected() -> None:
    # x.strip().split() — корень не Name (Call), обе части — методы
    found = _kinds("y = x.strip().split()")
    assert found.get("strip") == "method"
    assert found.get("split") == "method"


def test_module_call_via_alias() -> None:
    found = _kinds("import numpy as np\na = np.array([1, 2])")
    assert found.get("numpy.array") == "function"


# --- синтаксические конструкции (issue #367, ≥ 9) --------------------------

_CONSTRUCT_CODE = """
xs = [x for x in range(3)]
ss = {x for x in xs}
d = {k: v for k, v in items}
g = (y for y in xs)
f = lambda a: a + 1
t = a if cond else b
s = seq[1:3]
name = f"hi {x}"
val = (n := 5)
first, *rest = xs
with open("f") as fh:
    pass
try:
    pass
except ValueError:
    pass
@deco
def foo():
    pass
"""

_EXPECTED_CONSTRUCTS = {
    "list comprehension",
    "set comprehension",
    "dict comprehension",
    "generator expression",
    "lambda",
    "ternary",
    "slice",
    "f-string",
    "walrus",
    "unpacking",
    "with",
    "try/except",
    "decorator",
}


def test_constructs_detected_full_set() -> None:
    found = _kinds(_CONSTRUCT_CODE)
    constructs = {c for c, kind in found.items() if kind == "construct"}
    assert _EXPECTED_CONSTRUCTS <= constructs
    assert len(constructs) >= 9  # метрика §6


@pytest.mark.parametrize(
    ("snippet", "concept"),
    [
        ("v = [i*i for i in xs]", "list comprehension"),
        ("v = {i for i in xs}", "set comprehension"),
        ("v = {i: i for i in xs}", "dict comprehension"),
        ("v = sum(i for i in xs)", "generator expression"),
        ("f = lambda z: z", "lambda"),
        ("v = a if c else b", "ternary"),
        ("v = s[1:2]", "slice"),
        ('v = f"{x}"', "f-string"),
        ("while (n := next(it)):\n    pass", "walrus"),
        ("a, *b = xs", "unpacking"),
    ],
)
def test_each_construct_individually(snippet: str, concept: str) -> None:
    assert _kinds(snippet).get(concept) == "construct"


def test_syntax_error_yields_empty() -> None:
    assert scan_code_concepts("def (:\n") == {}


# --- inventory-driven builtins/методы (issue #367, через web-адаптер) -------


def test_code_terms_detects_inventory_builtin_beyond_curated() -> None:
    # frozenset/super — вне узкого CODE_TERM_BUILTINS, но в stdlib-инвентаре
    ids = {t["id"] for t in code_terms("a = frozenset([1, 2])")}
    assert any("frozenset" in i for i in ids)


def test_code_terms_detects_inventory_method_beyond_curated() -> None:
    # removeprefix — вне хардкодного _BUILTIN_METHODS, но метод str в инвентаре
    terms = code_terms('s = name.removeprefix("pre")')
    assert any("removeprefix" in t["id"] for t in terms)


# --- исключения и атрибуты модулей (issue #686) ----------------------------


@pytest.mark.parametrize(
    ("snippet", "concept"),
    [
        ("raise ValueError('x')", "ValueError"),
        ("raise KeyError", "KeyError"),
        ("try:\n    pass\nexcept IndexError:\n    pass", "IndexError"),
        ("try:\n    pass\nexcept (TypeError, OSError):\n    pass", "TypeError"),
    ],
)
def test_exception_named_in_code_detected(snippet: str, concept: str) -> None:
    assert _kinds(snippet).get(concept) == "exception"


def test_exception_constructor_call_is_inventory_driven() -> None:
    # Голый `RuntimeError("boom")` (без raise) — Name-вызов, поэтому имя должно
    # быть в наборе builtins: курируемый дефолт сканера исключений не знает,
    # а inventory-набор web-адаптера — знает (issue #686).
    assert "RuntimeError" not in _kinds("e = RuntimeError('boom')")
    terms = {t["id"]: t for t in code_terms("e = RuntimeError('boom')")}
    assert terms["runtimeerror"]["kind"] == "exception"


def test_plain_identifier_is_not_mistaken_for_exception() -> None:
    # Фильтр конвенции имён: обычный класс исключением не становится.
    found = _kinds("class Helper:\n    pass\n\nraise Helper()")
    assert "Helper" not in found


@pytest.mark.parametrize(
    ("snippet", "concept"),
    [
        ("import math\nprint(math.pi)", "math.pi"),
        ("import sys\nargs = sys.argv", "sys.argv"),
        ("import string\nletters = string.ascii_lowercase", "string.ascii_lowercase"),
    ],
)
def test_module_attribute_without_call_detected(snippet: str, concept: str) -> None:
    assert _kinds(snippet).get(concept) == "attribute"


def test_attribute_of_local_object_is_ignored() -> None:
    # Корень не импортированный модуль → не концепция (иначе шум на self.x).
    assert _kinds("obj = make()\nvalue = obj.field") == {}


def test_nested_attribute_link_not_recorded() -> None:
    # `os.path.join(...)` — одна концепция, промежуточный `os.path` не термин.
    found = _kinds("import os\np = os.path.join(a, b)")
    assert "os.path.join" in found
    assert "os.path" not in found


def test_missing_detector_ignores_source_exceptions() -> None:
    # Инвариант: очередь пополнения ловит исключения из ТЕКСТА ошибки, а не из
    # исходника — её поведение issue #686 не меняет.
    from stepik_grader.glossary.detector import MissingConceptDetector

    entries = MissingConceptDetector().detect_from_code(
        "try:\n    pass\nexcept ValueError:\n    pass\n", source="sol.py"
    )
    assert [e.concept for e in entries] == []
