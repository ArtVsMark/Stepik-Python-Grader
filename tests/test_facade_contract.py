"""Контракт фасада `stepik_grader.grader` — исполняемая проверка (issue #836).

CLAUDE.md держит инвариант «НЕ ломать обратную совместимость `__all__` в
grader.py», и это был единственный инвариант контракта без автоматической
проверки. Опасны именно star-имена (`from ...core.grader_core import *`): убери
имя из `__all__` модуля-источника — `import stepik_grader.grader` не упадёт,
упадёт только чужой `from stepik_grader.grader import TestCase`, то есть у
внешнего потребителя, а не в CI.

Приватные реэкспорты (`_build_function_wrapper`, `_verdict`, `_RICH`, …) в
`__all__` не входят, но на них держатся monkeypatch-тесты набора и они
задокументированы в докстринге `grader.py` — их состав тоже заморожен.
"""

from __future__ import annotations

import pytest

from stepik_grader import grader

# Замороженный состав публичного фасада. Список — не дубликат `__all__`, а
# независимая фиксация ожидаемого контракта: расхождение в любую сторону значит,
# что публичная поверхность изменилась и это должно быть осознанным решением
# (см. § Архитектурные инварианты в CLAUDE.md).
_EXPECTED_PUBLIC = {
    "ENCODING",
    "MEASURE_CHILD_MEMORY",
    "MICROBENCH_MAX_CASES",
    "MUCH_SLOWER_THRESHOLD",
    "SIMILAR_THRESHOLD",
    "TIMEOUT_SECONDS",
    "TestCase",
    "collect_grouped_files",
    "find_all_solution_files",
    "format_benchmark_row",
    "format_correctness_row",
    "is_function_only_solution",
    "is_solution_file",
    "load_test_cases",
    "load_text_lines",
    "print_benchmark_header",
    "print_benchmark_results",
    "print_correctness_header",
    "print_correctness_results",
    "resolve_test_dir",
    "run_benchmark",
    "run_microbench_mode",
    "run_single_test",
    "run_tests",
    "set_runner",
}

# Приватные имена, на которые опираются существующие monkeypatch-тесты и
# facade-доступ из тестов/скриптов.
_EXPECTED_PRIVATE = {
    "_ask_number",
    "_BENCH_PROFILES",
    "_MICRO_PROFILES",
    "_interactive_menu",
    "_print_menu",
    "_resolve_test_dir_from_input",
}


@pytest.mark.parametrize("name", sorted(_EXPECTED_PUBLIC))
def test_public_name_is_importable(name: str) -> None:
    """Каждое имя из контракта реально доступно как атрибут фасада."""
    assert hasattr(grader, name), (
        f"grader.{name} исчез: `from stepik_grader.grader import {name}` сломается "
        "у внешнего потребителя, а импорт самого модуля при этом не упадёт"
    )


def test_all_matches_the_frozen_contract() -> None:
    """Состав `__all__` не дрейфует незаметно — ни в плюс, ни в минус."""
    assert set(grader.__all__) == _EXPECTED_PUBLIC


def test_all_has_no_duplicates() -> None:
    assert len(grader.__all__) == len(set(grader.__all__))


@pytest.mark.parametrize("name", sorted(_EXPECTED_PRIVATE))
def test_private_reexport_is_available(name: str) -> None:
    """Приватные реэкспорты держат monkeypatch-тесты набора — тоже контракт."""
    assert hasattr(grader, name)


def test_star_import_exposes_exactly_the_contract() -> None:
    """`from stepik_grader.grader import *` даёт ровно публичный контракт."""
    namespace: dict[str, object] = {}
    exec("from stepik_grader.grader import *", namespace)  # noqa: S102 — проверяем сам механизм
    exported = {name for name in namespace if not name.startswith("__")}
    assert exported == _EXPECTED_PUBLIC
