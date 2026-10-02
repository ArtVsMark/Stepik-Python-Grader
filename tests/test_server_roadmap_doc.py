"""Guard: серверный трек перенесён из комментариев целиком (issue #1191).

Трек жил в ленте комментариев GitHub-issue: комментарий такого размера нельзя ни
отревьюить в PR, ни найти поиском по `docs/`, ни изменить атомарно. При переносе
главный риск — потерять цель: их 55 в одиннадцати блоках плюс 8 отклонённых и 4
пункта пополнения, и на глаз недостача незаметна.

Тест считает, а не читает. Числа зафиксированы намеренно: изменение состава — это
осознанное решение, которое должно править и тест, а не проходить молча.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_DOC = pathlib.Path(__file__).parent.parent / "docs" / "dev" / "design" / "server-roadmap.md"

#: Тематических блоков в переносе.
_BLOCKS = 11

#: Целей в блоках. Волна аудита дала 78 «сырых», но автор сведения схлопнул
#: дубликаты и вынес часть в отклонённые — в документе это сказано вслух,
#: чтобы следующая сверка не искала пятнадцать потерянных строк.
_GOALS = 55

#: Отклонено как «на самом деле не серверное» — с причиной у каждой.
_REJECTED = 8

#: Пунктов пополнения из аудита 2026-08-10.
_SUPPLEMENT = 4


@pytest.fixture(scope="module")
def text() -> str:
    return _DOC.read_text(encoding="utf-8")


def _table_rows(chunk: str) -> list[str]:
    """Строки данных таблицы — без заголовка и разделителя."""
    return [
        line
        for line in chunk.splitlines()
        if line.startswith("| ") and "---" not in line and not line.startswith("| Цель")
    ]


def _blocks(text: str) -> list[str]:
    """Блоки целей: от `## N. Название` до следующего заголовка любого уровня."""
    parts = re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]
    return [re.split(r"^#{1,2} ", part, flags=re.MULTILINE)[0] for part in parts]


def test_all_blocks_present(text: str) -> None:
    assert len(_blocks(text)) == _BLOCKS


def test_no_goal_lost_in_the_move(text: str) -> None:
    """Сверка по количеству — то, ради чего тест и написан."""
    total = sum(len(_table_rows(block)) for block in _blocks(text))

    assert total == _GOALS, f"целей в блоках: {total}, ожидалось {_GOALS}"


def test_rejected_goals_kept_with_reasons(text: str) -> None:
    """Отклонённая цель фиксируется, а не удаляется: молча выброшенная вернётся."""
    chunk = text.split("# Отклонено как")[1]
    rows = _table_rows(chunk)

    assert len(rows) == _REJECTED
    assert all(row.count("|") >= 3 for row in rows), "у каждой отклонённой нужна причина"


def test_supplement_from_the_later_audit_present(text: str) -> None:
    chunk = text.split("# Пополнение из аудита 2026-08-10")[1].split("# Отклонено")[0]

    assert len(re.findall(r"^### ", chunk, flags=re.MULTILINE)) == _SUPPLEMENT


def test_every_block_names_its_audit_and_dependencies(text: str) -> None:
    """Хронология решений — часть содержания, а не свойство ленты комментариев."""
    for block in _blocks(text):
        assert "Аудит" in block, block[:60]
        assert "Зависит от" in block, block[:60]


def test_design_is_referenced_not_reopened(text: str) -> None:
    """Документ отвечает «что и в каком порядке», а не «как» — ADR в силе."""
    assert "не переоткрывает" in text or "не переоткрываем" in text
    for adr in ("ADR-0001", "ADR-0008", "ADR-0009"):
        assert adr in text, adr
