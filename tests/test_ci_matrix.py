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
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_SCRIPT = _SCRIPTS / "ci_matrix.py"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_NEXT = _ROOT / ".github" / "workflows" / "python-next.yml"
_PYPROJECT = _ROOT / "pyproject.toml"


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


def test_a_cell_without_the_experimental_axis_holds_the_merge(matrix: ModuleType) -> None:
    """Матрица на планке без измерения `experimental` — ячейки обязательны (issue #1564).

    Прежнее правило «обязательна, только если имя кончается на `, false)`»
    объявило бы `test (ubuntu-latest, 3.14)` необязательной: слияние перестало
    бы ждать единственную версию, которую грейдер обещает.
    """
    assert matrix.is_blocking("test (ubuntu-latest, 3.14)") is True


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


def test_the_live_matrix_checks_the_floor_and_holds_the_merge(matrix: ModuleType) -> None:
    """В настоящем `ci.yml` каждая ячейка обязательна и проверяет ровно планку.

    Предрелизная версия живёт отдельным прогоном `python-next.yml` (issue
    #1564): ячейка внутри `ci.yml` либо красила бы общий вердикт, либо
    пряталась под `continue-on-error`.
    """
    names = matrix.matrix_names(_CI.read_text(encoding="utf-8"))
    floor = re.search(r'requires-python = ">=([\d.]+)"', _PYPROJECT.read_text(encoding="utf-8"))

    assert names, "матрица не даёт ни одной ячейки"
    assert matrix.blocking_names(names) == names, "необязательная ячейка в ci.yml"
    assert floor is not None
    assert all(f", {floor.group(1)})" in name for name in names), names


def test_the_next_python_is_checked_by_its_own_workflow() -> None:
    """Следующий цикл проверяется — отдельным прогоном и выше планки."""
    text = _NEXT.read_text(encoding="utf-8")
    floor = re.search(r'requires-python = ">=([\d.]+)"', _PYPROJECT.read_text(encoding="utf-8"))
    versions = re.findall(r'python-version:\s*\["([\d.]+)"\]', text)

    assert floor is not None and versions, "в python-next.yml нет версии"
    assert "allow-prereleases: true" in text
    assert all(
        tuple(map(int, v.split("."))) > tuple(map(int, floor.group(1).split("."))) for v in versions
    ), (versions, floor.group(1))
