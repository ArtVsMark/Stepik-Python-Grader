"""Кнопка «Отправить в Stepik» отражает исход текущего прогона (issue #967).

Находка FE-2-01. Режим 1 в вебе **всегда** идёт через async-джобу, а
``updateStepikSubmitButton()`` звали только синхронный путь и ``setMode()`` —
ветка успеха джобы её не вызывала вовсе. Кнопка отставала на один прогон:
после полного AC оставалась выключённой (фича мёртвая), а после переключения
режимов включалась по вердикту, которого на экране уже нет.

Вторая половина того же дефекта — упавший прогон: ``state.lastResult`` хранит
ПРОШЛЫЙ результат, поэтому «просто позвать функцию в конце» мало. Обновлять
кнопку нужно исходом именно этого прогона, иначе она предложит отправить на
платформу код, который только что не прошёл, — а сабмит необратим.

Тесты статические: браузерного движка в облачной сессии нет. Они сторожат
форму (кто кого зовёт и с чем), а не поведение — поведение закрывается
прогоном в браузере, он описан в PR.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stepik_grader" / "web" / "static"


@pytest.fixture
def grade_js() -> str:
    """Исходник раздела проверки решений."""
    return (_STATIC / "grade.js").read_text(encoding="utf-8")


def _block(source: str, header: str) -> str:
    """Тело функции от заголовка до строки ``\\n}`` — как в соседних тестах."""
    start = source.index(header)
    return source[start : source.index("\n}", start)]


class TestOnePlaceForAllPaths:
    """Кнопку обновляет завершение прогона, а не каждый путь по отдельности."""

    def test_finish_updates_the_button(self, grade_js: str) -> None:
        assert "updateStepikSubmitButton(result)" in _block(grade_js, "function _finishGradeUI(")

    def test_async_path_no_longer_skips_it(self, grade_js: str) -> None:
        """Ровно тот путь, который не звал функцию вовсе: режим 1 идёт через него."""
        assert "_finishGradeUI(outcome)" in grade_js

    def test_grade_paths_do_not_call_it_directly(self, grade_js: str) -> None:
        """Прямые вызовы из путей прогона вернули бы прежнее расхождение.

        Легитимных мест два и оба вне прогона: ``setMode`` (смена режима) и
        возврат после сабмита. Оба зовут с ``state.lastResult`` — там это и есть
        актуальный результат, потому что нового прогона не было.
        """
        direct = re.findall(r"updateStepikSubmitButton\((.*?)\)", grade_js)

        assert direct.count("state.lastResult") == 2
        assert direct.count("result") == 1  # внутри _finishGradeUI
        assert len(direct) == 4  # плюс само определение функции


class TestFailedRunDisablesTheButton:
    """Прогон без результата обязан гасить кнопку — сабмит необратим."""

    def test_default_is_no_result(self, grade_js: str) -> None:
        """Сбой старта зовёт ``_finishGradeUI()`` без аргумента — дефолт решает."""
        assert "function _finishGradeUI(result = null)" in grade_js

    def test_only_done_fills_the_outcome(self, grade_js: str) -> None:
        """`outcome` заполняется в ветке `done` и только там.

        Отмена и ошибка оставляют его ``null``: у них результата нет, а
        ``state.lastResult`` в этих ветках хранит прошлый прогон.
        """
        assert grade_js.count("outcome = data.result") == 1
        assert (
            'if (data.status === "done") {\n          state.lastResult = data.result;' in grade_js
        )

    def test_sync_path_tracks_its_own_outcome(self, grade_js: str) -> None:
        """У синхронного пути та же болезнь: `catch` не обновлял бы кнопку."""
        assert "let outcome = null;" in grade_js
        assert "_finishGradeUI(outcome); // issue #683" in grade_js


class TestButtonRule:
    """Само правило включения не менялось — фикс про то, КОГДА его применяют."""

    def test_full_ac_in_mode_one_only(self, grade_js: str) -> None:
        body = _block(grade_js, "function updateStepikSubmitButton(")

        assert 'state.mode === "file"' in body
        assert "r.passed === r.total" in body
        assert "btn.disabled = !allPassed" in body
