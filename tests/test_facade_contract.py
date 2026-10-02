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

Заморожен он, впрочем, стал не сразу: до issue #1004 (находка `AUD-1-01`)
здесь перечислялись шесть имён из двадцати девяти, и проверка отвечала на
«объявленное имя на месте», а не на «в фасаде ровно эти имена». Обещание в
этом абзаце было, механизма под ним не было.
"""

from __future__ import annotations

import types

import pytest

from stepik_grader import grader

# Замороженный состав публичного фасада. Список — не дубликат `__all__`, а
# независимая фиксация ожидаемого контракта: расхождение в любую сторону значит,
# что публичная поверхность изменилась и это должно быть осознанным решением
# (см. § Архитектурные инварианты в CLAUDE.md).
_EXPECTED_PUBLIC = {
    # issue #997 (VIS-2-03): точка расширения runner'а целиком в фасаде —
    # set_runner был публичным, а типы для своей реализации жили в core/.
    "LocalRunner",
    "active_runner",
    "RunOutcome",
    "RunSpec",
    "Runner",
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

# Приватные имена, реэкспортируемые фасадом. Докстринг модуля объявляет их
# состав замороженным — и до issue #1004 (находка `AUD-1-01`) заморожены были
# шесть из двадцати девяти: проверялось «объявленное имя на месте», а не «в
# фасаде ровно эти имена». Обе пропущенные стороны опасны по-разному.
#
# Новое приватное имя, приехавшее случайно (звёздный импорт расширили, соседний
# модуль переименовал внутренность), становится де-факто контрактом: его тут же
# начинают патчить в тестах, и убрать его потом уже нельзя.
#
# Исчезнувшее — ломает monkeypatch-тесты набора, но не импорт фасада, поэтому
# падает не там, где сломали.
#
# Список сокращаем осознанно и в PR: «заморожен» значит, что имя уходит
# решением, а не молча.
_EXPECTED_PRIVATE = {
    "_BENCH_PROFILES",
    "_MICRO_PROFILES",
    "_RICH",
    "_SEP",
    "_SOLUTION_FILE_RE",
    "_STATUS_COLORS",
    "_VERDICT_COLORS",
    "_apply_run_mode_override",
    "_ask_bench_profile",
    "_ask_micro_profile",
    "_ask_number",
    "_ast_function_name",
    "_build_call_wrapper",
    "_build_function_wrapper",
    "_console",
    "_correctness_status",
    "_cprint",
    "_detect_run_mode",
    "_interactive_menu",
    "_is_python_code_block",
    "_is_safe_constant",
    "_measure_peak_memory",
    "_micro_stats",
    "_normalize_output_line",
    "_parse_testblock_file",
    "_print_menu",
    "_read_meta_function_name",
    "_resolve_test_dir_from_input",
    "_verdict",
}


def _facade_private_names() -> set[str]:
    """Приватные имена, которые фасад реально отдаёт.

    Дандеры не в счёт — это машинерия модуля, а не реэкспорт. Модули тоже:
    ``import stepik_grader.core.x`` кладёт в пространство имён сам пакет, и
    считать его реэкспортом значило бы замораживать структуру импортов.
    """
    return {
        name
        for name, value in vars(grader).items()
        if name.startswith("_")
        and not name.startswith("__")
        and not isinstance(value, types.ModuleType)
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


def test_private_reexports_match_the_frozen_contract() -> None:
    """Состав приватных реэкспортов заморожен — как и публичный, в обе стороны.

    До issue #1004 (находка `AUD-1-01`) докстринг это обещал, а проверка
    отвечала только на «объявленное имя на месте»: двадцать три реэкспорта
    из двадцати девяти не были заморожены ничем, и появление нового имени
    не замечал никто.
    """
    actual = _facade_private_names()
    appeared = sorted(actual - _EXPECTED_PRIVATE)
    vanished = sorted(_EXPECTED_PRIVATE - actual)

    assert not appeared and not vanished, (
        "приватная поверхность фасада разошлась с замороженным составом.\n"
        f"  приехали и стали де-факто контрактом: {', '.join(appeared) or '—'}\n"
        f"  исчезли (сломают monkeypatch, но не импорт): {', '.join(vanished) or '—'}"
    )


def test_the_private_surface_is_actually_read() -> None:
    """Guard-the-guard: список приватных имён собрался, а не оказался пустым.

    Пустое множество совпало бы с пустым ожиданием, и проверка выше зеленела
    бы на любом фасаде — включая сломанный.
    """
    assert len(_facade_private_names()) > 20


def test_modules_are_not_counted_as_reexports() -> None:
    """Пакет в пространстве имён — не реэкспорт имени.

    Иначе контракт замораживал бы структуру импортов фасада: перенос строки
    ``from ... import`` менял бы «поверхность», ничего не меняя для потребителя.
    """
    assert not any(
        isinstance(getattr(grader, name), types.ModuleType) for name in _facade_private_names()
    )


def test_star_import_exposes_exactly_the_contract() -> None:
    """`from stepik_grader.grader import *` даёт ровно публичный контракт."""
    namespace: dict[str, object] = {}
    exec("from stepik_grader.grader import *", namespace)  # noqa: S102 — проверяем сам механизм
    exported = {name for name in namespace if not name.startswith("__")}
    assert exported == _EXPECTED_PUBLIC


class TestRunnerExtensionPoint:
    """Точка расширения runner доступна целиком из фасада (issue #997, VIS-2-03).

    ``set_runner`` был в фасаде, а типы для написания своего runner'а — только
    в ``core/``: расширять фасад приходилось, импортируя внутренности.
    """

    def test_runner_types_are_reexported(self) -> None:
        from stepik_grader.grader import LocalRunner, Runner, RunOutcome, RunSpec

        assert Runner is not None and RunSpec is not None
        assert RunOutcome is not None and LocalRunner is not None

    def test_extension_point_names_are_public(self) -> None:
        import stepik_grader.grader as facade

        expected = {"Runner", "RunSpec", "RunOutcome", "LocalRunner", "set_runner", "active_runner"}
        assert expected <= set(facade.__all__)
