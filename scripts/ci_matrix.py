#!/usr/bin/env python3
"""scripts/ci_matrix.py — имена проверок, выведенные из матрицы ``ci.yml``.

Отдельный модуль, потому что ответ на вопрос «как площадка назовёт эту ячейку
и держит ли она слияние» нужен трём сторонам сразу: сверке защиты ветки
(``check_branch_protection``), агрегатору (``ci_aggregate``) и очереди мержа
(``gh_rest``). Первые два уже брали разбор у первого из них; третий не мог —
``check_branch_protection`` импортирует ``gh_rest``, и обратный импорт замкнул
бы цикл. Без общего места третья сторона завела бы **свою** копию признака, и
разошлись бы они молча: очередь считала бы блокирующим то, чего площадка не
блокирует (issue #1532).

Зависимостей у модуля нет намеренно — только stdlib: его импортируют и
транспорт, и гейты.

**Что такое «блокирует».** Ячейка под ``continue-on-error`` (``experimental:
true``) слияние не держит: в списке обязательных её нет и быть не должно.
Признак — суффикс имени, потому что имя площадка складывает из значений
измерений в порядке объявления: ``test (ubuntu-latest, 3.15, true)``.
"""

from __future__ import annotations

import re

__all__ = [
    "blocking_names",
    "is_blocking",
    "matrix_names",
]

#: Блок ``matrix:`` джоба ``test`` — до первого ключа того же уровня.
_MATRIX_RE = re.compile(r"^      matrix:\n(.*?)(?=^    [a-z]|^  [a-z])", re.M | re.S)

#: Список значений одного измерения: ``os: ["a", "b"]``.
_AXIS_RE = re.compile(r"^        ([\w-]+):\s*\[(.+?)\]\s*$", re.M)

#: Добавленная комбинация: ``- {os: "x", python-version: "3.15", experimental: true}``.
_INCLUDE_RE = re.compile(r"^\s*-\s*\{(.+?)\}\s*$", re.M)

#: Хвост имени неэкспериментальной ячейки. Измерение ``experimental`` идёт в
#: имени последним, и его значение — единственный признак, по которому ячейку
#: отличают снаружи: у самого check-run'а поля «обязательна ли» нет.
_STABLE_SUFFIX = ", false)"


def matrix_names(text: str) -> list[str]:
    """Имена матричных проверок, ВЫВЕДЕННЫЕ из ``ci.yml`` (правило 171).

    Эталон берётся из дерева этого же изменения, а не переписывается в
    константу руками. Копия верна ровно до первой правки матрицы и расходится
    **молча**: ruleset и константа остаются согласными друг с другом, а работы
    называются иначе — PR уходит в вечное ожидание, и ни одна проверка при
    этом не краснеет.

    Имя площадка складывает из имени джоба и значений в порядке объявления
    измерений: ``test (ubuntu-latest, 3.12, false)``.

    Args:
        text: Содержимое ``.github/workflows/ci.yml``.

    Returns:
        Имена комбинаций в порядке, в каком их порождает площадка.
    """
    block = _MATRIX_RE.search(text)
    if block is None:
        return []
    body = block.group(1)
    axes: dict[str, list[str]] = {}
    for name, raw in _AXIS_RE.findall(body):
        axes[name] = [item.strip().strip("\"'") for item in raw.split(",") if item.strip()]
    if not axes:
        return []

    order = list(axes)
    names: list[str] = []
    combos: list[tuple[str, ...]] = [()]
    for axis in order:
        combos = [(*combo, value) for combo in combos for value in axes[axis]]
    names.extend(f"test ({', '.join(combo)})" for combo in combos)

    for raw in _INCLUDE_RE.findall(body):
        pairs = dict(
            (
                part.split(":", 1)[0].strip(),
                part.split(":", 1)[1].strip().strip("\"'"),
            )
            for part in raw.split(",")
            if ":" in part
        )
        if set(order) <= set(pairs):
            names.append(f"test ({', '.join(pairs[axis] for axis in order)})")
    return names


def is_blocking(name: str) -> bool:
    """Держит ли проверка с таким именем слияние.

    Матричная ячейка под ``continue-on-error`` — нет: она для того и заведена,
    чтобы падение предрелизной версии не останавливало работу. Всё остальное —
    да: неизвестное имя считается блокирующим намеренно, потому что ошибка в
    эту сторону задерживает PR, а в обратную — пропускает сломанное.

    Args:
        name: Имя check-run'а, как его показывает площадка.

    Returns:
        ``False`` только для экспериментальной ячейки матрицы.
    """
    return not (name.startswith("test (") and not name.endswith(_STABLE_SUFFIX))


def blocking_names(names: list[str]) -> list[str]:
    """Те из имён, что действительно держат слияние (порядок сохраняется)."""
    return [name for name in names if is_blocking(name)]
