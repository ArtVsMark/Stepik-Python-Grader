"""Тесты scripts/ci_matrix.py — имена ячеек матрицы и что из них держит слияние.

Модуль появился не ради красоты (issue #1532). Разбор матрицы жил в
`check_branch_protection`, а очередь мержа взять его оттуда не могла: тот
импортирует `gh_rest`, и обратный импорт замкнул бы цикл. Без общего места
третья сторона завела бы СВОЮ копию признака — и очередь считала бы блокирующим
то, чего площадка не блокирует. Ровно это и случилось: PR встал из-за
предрелизной ячейки, которой нет в списке обязательных.

Здесь проверяется не «разбирает YAML», а два утверждения: имя складывается
ровно так, как его складывает площадка, и признак «держит слияние» отвечает
одинаково всем трём потребителям.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_SCRIPT = _SCRIPTS / "ci_matrix.py"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_ci_matrix", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def matrix() -> ModuleType:
    """Свежий модуль на каждый тест."""
    return _load_module()


_YAML = """jobs:
  test:
    strategy:
      matrix:
        os: ["ubuntu-latest", "macos-latest"]
        python-version: ["3.12", "3.14"]
        experimental: [false]
        include:
          - {os: "ubuntu-latest", python-version: "3.15", experimental: true}
    steps:
      - run: pytest
"""


# --- имя складывает площадка, а не мы ------------------------------------------


def test_names_follow_the_order_of_the_axes(matrix: ModuleType) -> None:
    """Порядок значений в имени — порядок объявления измерений."""
    names = matrix.matrix_names(_YAML)

    assert "test (ubuntu-latest, 3.12, false)" in names
    assert "test (macos-latest, 3.14, false)" in names


def test_included_combinations_are_named_too(matrix: ModuleType) -> None:
    """Ячейка из `include` попадает в список наравне с произведением осей."""
    assert "test (ubuntu-latest, 3.15, true)" in matrix.matrix_names(_YAML)


def test_a_matrix_that_did_not_parse_is_empty_not_wrong(matrix: ModuleType) -> None:
    """Не разобрали — пусто; выдумывать имена хуже, чем не назвать ни одного."""
    assert matrix.matrix_names("jobs:\n  test:\n    runs-on: ubuntu-latest\n") == []


# --- что держит слияние --------------------------------------------------------


def test_the_experimental_cell_does_not_hold_the_merge(matrix: ModuleType) -> None:
    """Ровно тот случай, ради которого модуль и заведён."""
    assert matrix.is_blocking("test (ubuntu-latest, 3.15, true)") is False


def test_a_stable_cell_holds_the_merge(matrix: ModuleType) -> None:
    assert matrix.is_blocking("test (ubuntu-latest, 3.14, false)") is True


@pytest.mark.parametrize("name", ["ci-complete", "static", "e2e", "docs-guardrails"])
def test_a_plain_job_holds_the_merge(matrix: ModuleType, name: str) -> None:
    """Джоб без матрицы под правило не подпадает — он обязателен как есть."""
    assert matrix.is_blocking(name) is True


def test_an_unknown_name_is_treated_as_blocking(matrix: ModuleType) -> None:
    """Умолчание выбрано в сторону задержки, а не пропуска.

    Ошибка «посчитали блокирующим лишнее» задерживает PR и видна сразу;
    обратная — пропускает сломанное в базу и не видна вовсе.
    """
    assert matrix.is_blocking("проверка-которой-мы-не-знаем") is True


def test_blocking_names_keeps_the_order(matrix: ModuleType) -> None:
    """Порядок сохраняется: отчёт читает человек, и перетасовка ему мешает."""
    given = [
        "test (ubuntu-latest, 3.15, true)",
        "ci-complete",
        "test (macos-latest, 3.14, false)",
    ]

    assert matrix.blocking_names(given) == [
        "ci-complete",
        "test (macos-latest, 3.14, false)",
    ]


# --- живое дерево --------------------------------------------------------------


def test_the_live_matrix_has_both_kinds(matrix: ModuleType) -> None:
    """В настоящем `ci.yml` есть и обязательные ячейки, и предрелизная.

    Ни одной предрелизной — следующий цикл Python не проверяется вовсе; ни
    одной обязательной — матрица не держит ничего.
    """
    names = matrix.matrix_names(_CI.read_text(encoding="utf-8"))
    blocking = matrix.blocking_names(names)

    assert blocking, "матрица не даёт ни одной обязательной ячейки"
    assert len(blocking) < len(names), "предрелизной ячейки в матрице нет"
