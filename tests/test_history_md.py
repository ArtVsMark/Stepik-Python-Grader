"""Guard: HISTORY.md держит форму, ради которой заводился (issue #1181).

Документ собирает историю проекта в одном месте: пролог о происхождении,
релизы единым форматом, таблица эволюции метрик. Раньше это жило
в двух файлах (`docs/archive/history.md` и `docs/use/versions.md`), и они
разошлись — ровно так же, как разошлись «1700+/2100+» в метриках.

Тест сторожит не текст, а инварианты: ни один релиз не потерян, таблица одна,
записи не разрастаются в журнал работ. Содержание правится свободно.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_HISTORY = _ROOT / "HISTORY.md"

#: Записи о релизе: `## v1.4.0 · 5 июля 2026 · тема`.
_RELEASE_HEADING = re.compile(r"^## (v\d+\.\d+\.\d+) · ", re.MULTILINE)

#: Любой заголовок второго уровня — граница раздела.
_SECTION_HEADING = re.compile(r"^## ", re.MULTILINE)

#: Строка таблицы метрик: `| v1.4.0 | 622 | 95% | … |`.
_METRICS_ROW = re.compile(r"^\|\s*(v\d+\.\d+\.\d+)\s*\|", re.MULTILINE)

#: Одна запись — не длиннее экрана. 60 строк с запасом на подзаголовки: предел
#: нужен, чтобы документ не повторил судьбу handoff, раздувшегося до 336 строк
#: мёртвого журнала.
_MAX_RELEASE_LINES = 60


@pytest.fixture(scope="module")
def text() -> str:
    return _HISTORY.read_text(encoding="utf-8")


def test_every_tagged_release_has_an_entry(text: str) -> None:
    """Все версии на месте, ни одна не растворена в «спринтах» и «партиях».

    Набор задан списком, а не вычисляется из git-тегов намеренно: в клоне без
    тегов (так клонирует облачная сессия) вычисляемый набор оказался бы пустым,
    и гейт зеленел бы на пустоте. Поэтому при релизе список расширяется тем же
    PR, что и запись, — это часть релизной процедуры, а не помеха ей.
    """
    documented = set(_RELEASE_HEADING.findall(text))
    expected = {f"v1.{minor}.0" for minor in range(12)}

    assert documented == expected, f"нет записей: {sorted(expected - documented)}"


def test_metrics_table_covers_the_same_releases(text: str) -> None:
    """Таблица и записи не должны расходиться — это и был исходный дефект."""
    assert set(_METRICS_ROW.findall(text)) == set(_RELEASE_HEADING.findall(text))


def test_exactly_one_table(text: str) -> None:
    """Вторую сводку не заводим: две таблицы в одном документе разъезжаются."""
    header_rows = [line for line in text.splitlines() if re.match(r"^\|\s*-+", line.strip())]

    assert len(header_rows) == 1, f"таблиц в документе: {len(header_rows)}"


def test_prologue_credits_the_upstream_project(text: str) -> None:
    """Атрибуция первоисточнику — обязательство, а не вежливость."""
    assert "python_generation_grader" in text
    assert "PavloOps" in text


def test_no_release_entry_grows_into_a_work_log(text: str) -> None:
    """Каждая запись — не длиннее экрана."""
    positions = [m.start() for m in _RELEASE_HEADING.finditer(text)]
    # Запись кончается на следующем заголовке — любом, а не только релизном.
    # Иначе последняя запись меряется вместе со всем хвостом документа (таблица
    # метрик, послесловие), то есть предел для неё зависит от того, сколько
    # релизов накопилось ниже, и с каждым релизом ужимается ещё на строку.
    section_starts = [m.start() for m in _SECTION_HEADING.finditer(text)]
    too_long = []
    for start in positions:
        following = [s for s in section_starts if s > start]
        entry = text[start : following[0] if following else len(text)]
        lines = entry.count("\n")
        if lines > _MAX_RELEASE_LINES:
            too_long.append(f"{_RELEASE_HEADING.match(entry).group(1)}: {lines} строк")

    assert not too_long, "записи разрослись: " + ", ".join(too_long)


def test_current_numbers_are_not_hardcoded_outside_the_table(text: str) -> None:
    """Живые числа — только в бейджах README (CLAUDE.md § Метрики).

    В таблице числа законны: это снимок на момент релиза, он не устаревает.
    А вот в прозе «сейчас N тестов» протухнет к следующему PR и начнёт спорить
    с README — так уже разошлись «1700+» и «2100+».
    """
    prose = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("|"))
    claims = re.findall(r"\b\d{3,}\+?\s*(?:тест|карточ)", prose)

    assert not claims, f"числа в прозе устареют: {claims}"
