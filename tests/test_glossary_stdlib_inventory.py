"""Tests for the offline stdlib inventory scanner (issue #196).

Покрывает ``stepik_grader.glossary.stdlib_inventory``: детерминированный
офлайн-инвентарь builtins/исключений/курируемых stdlib-модулей, без сети и
без исполнения пользовательского кода.
"""

from __future__ import annotations

from stepik_grader.glossary import NOTABLE_STDLIB_MODULES, StdlibItem, build_stdlib_inventory


def _by_qualname(items: list[StdlibItem]) -> dict[str, StdlibItem]:
    return {item.qualname: item for item in items}


def test_inventory_is_not_empty() -> None:
    items = build_stdlib_inventory()
    assert len(items) > 100


def test_inventory_includes_key_objects() -> None:
    items = _by_qualname(build_stdlib_inventory())
    assert items["ValueError"].kind == "exception"
    assert items["ValueError"].module == "builtins"
    assert items["map"].kind == "class"  # map — builtin-тип (isinstance(map, type))
    assert items["map"].module == "builtins"
    assert items["functools.reduce"].kind == "function"
    assert items["functools.reduce"].module == "functools"


def test_inventory_has_no_duplicate_qualnames() -> None:
    items = build_stdlib_inventory()
    qualnames = [item.qualname for item in items]
    assert len(qualnames) == len(set(qualnames))


def test_inventory_is_sorted_by_qualname() -> None:
    items = build_stdlib_inventory()
    qualnames = [item.qualname for item in items]
    assert qualnames == sorted(qualnames)


def test_inventory_is_deterministic_across_calls() -> None:
    first = build_stdlib_inventory()
    second = build_stdlib_inventory()
    assert first == second


def test_inventory_items_carry_python_version() -> None:
    items = build_stdlib_inventory()
    versions = {item.python_version for item in items}
    assert len(versions) == 1
    major, _, minor = next(iter(versions)).partition(".")
    assert major.isdigit() and minor.isdigit()


def test_inventory_does_not_double_count_builtin_exceptions_as_classes() -> None:
    items = _by_qualname(build_stdlib_inventory())
    assert items["ValueError"].kind == "exception"
    # ValueError не должен также фигурировать под другим kind.
    assert sum(1 for i in build_stdlib_inventory() if i.qualname == "ValueError") == 1


def test_inventory_respects_custom_module_subset() -> None:
    items = _by_qualname(build_stdlib_inventory(modules=frozenset({"math"})))
    assert "math.sqrt" in items
    assert "functools.reduce" not in items
    # Builtins и exceptions всё равно собираются независимо от модулей.
    assert "map" in items
    assert "ValueError" in items


def test_inventory_skips_unimportable_module_without_error() -> None:
    items = build_stdlib_inventory(modules=frozenset({"definitely_not_a_real_stdlib_module"}))
    assert len(items) > 0  # builtins/exceptions по-прежнему собраны


def test_notable_stdlib_modules_are_all_importable() -> None:
    import importlib

    for module_name in NOTABLE_STDLIB_MODULES:
        importlib.import_module(module_name)  # не должно бросать ImportError
