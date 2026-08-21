"""Огромный вывод не вешает раздел проверки (issue #968).

Находка FE-2-02. `renderInlineDiff` строит на каждую строку `div` и три `span`,
а на различающуюся — две таких строки, и вставляет всё одним `innerHTML`.
Границы не было ни на клиенте, ни на сервере.

Забытый выход из цикла — типичная студенческая ошибка, и решение печатает
десятки тысяч строк. До 10 МБ вывода складывались в сотни тысяч DOM-узлов:
вкладка замирала, отменить было нельзя, спасала только перезагрузка. То есть
результат прогона, который и должен объяснить ошибку, посмотреть было
невозможно — именно тогда, когда он нужнее всего.

Границы две и они про разное: сервер режет патологию до отправки (по строкам и
по длине строки), браузер ограничивает единовременную отрисовку и отдаёт
решение человеку кнопкой «показать всё».
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.web import viewmodels

_GRADE_JS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "stepik_grader"
    / "web"
    / "static"
    / "grade.js"
)


def _case(output: list[str], expected: list[str] | None = None) -> dict:
    """Упавший кейс с заданным выводом."""
    return {
        "verdict": "WA",
        "passed": False,
        "time": 0.01,
        "output": output,
        "expected": expected if expected is not None else ["ok"],
        "error": "",
    }


class TestServerClipsThePathology:
    """Сервер не отправляет в браузер то, что его гарантированно повесит."""

    def test_huge_output_is_cut_by_lines(self) -> None:
        view = viewmodels._case_view(1, _case([f"line {i}" for i in range(50_000)]))

        assert len(view["actual"].split("\n")) == viewmodels._MAX_CASE_LINES
        assert view["output_truncated"] is True

    def test_single_enormous_line_is_cut_too(self) -> None:
        """Одна строка на мегабайт вешает не хуже тысячи коротких."""
        view = viewmodels._case_view(1, _case(["x" * 1_000_000]))

        assert len(view["actual"]) == viewmodels._MAX_CASE_LINE_CHARS
        assert view["output_truncated"] is True

    def test_expected_is_clipped_as_well(self) -> None:
        """Эталон приходит из файла задачи и тоже бывает огромным."""
        view = viewmodels._case_view(1, _case(["ok"], expected=[f"e{i}" for i in range(50_000)]))

        assert len(view["expected"].split("\n")) == viewmodels._MAX_CASE_LINES
        assert view["output_truncated"] is True

    def test_normal_output_is_untouched(self) -> None:
        """Рабочий случай не должен даже узнать о существовании границы."""
        view = viewmodels._case_view(1, _case(["4", "5", "6"]))

        assert view["actual"] == "4\n5\n6"
        assert "output_truncated" not in view

    def test_flag_appears_only_on_truncation(self) -> None:
        """Поле необязательное: его отсутствие означает «вывод целиком».

        Ровно на границе усечения нет — иначе флаг врал бы на каждом кейсе
        ровно в лимит длиной.
        """
        exactly = viewmodels._case_view(1, _case(["y"] * viewmodels._MAX_CASE_LINES))

        assert "output_truncated" not in exactly

    @pytest.mark.parametrize("lines", [1, 10, 999])
    def test_short_outputs_never_flagged(self, lines: int) -> None:
        view = viewmodels._case_view(1, _case([f"l{i}" for i in range(lines)]))

        assert "output_truncated" not in view


class TestBrowserBoundsTheRender:
    """Клиентская граница — про отрисовку, и она снимается человеком."""

    @pytest.fixture
    def grade_js(self) -> str:
        return _GRADE_JS.read_text(encoding="utf-8")

    def test_row_limit_exists(self, grade_js: str) -> None:
        assert "const DIFF_MAX_ROWS = 500" in grade_js

    def test_limit_applies_unless_full(self, grade_js: str) -> None:
        """`full` — согласие человека, а не значение по умолчанию."""
        assert "const limit = full ? total : Math.min(total, DIFF_MAX_ROWS)" in grade_js
        assert "function renderInlineDiff(expected, actual, { full = false } = {})" in grade_js

    def test_hidden_rows_are_announced_with_a_button(self, grade_js: str) -> None:
        """Молча скрытые строки читались бы как «расхождений дальше нет»."""
        assert 'id="diff-show-all"' in grade_js
        assert 'grade.diff_truncated"' in grade_js.replace("'", '"')

    def test_consent_resets_on_case_switch(self, grade_js: str) -> None:
        """Иначе согласие на один огромный вывод молча накрыло бы все следующие."""
        assert "state.diffShowAll = false;" in grade_js

    def test_server_truncation_is_surfaced(self, grade_js: str) -> None:
        """Сервер отдал начало — человек должен знать, что конца тут нет."""
        assert "c.output_truncated" in grade_js
        assert "grade.output_truncated" in grade_js


class TestLocalesCoverBothLanguages:
    """Новые строки — сразу в обе локали: en это витрина, а не перевод потом."""

    @pytest.fixture
    def ui(self) -> dict:
        import json

        path = _GRADE_JS.parent / "locales" / "ui.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "key", ["grade.diff_truncated", "grade.diff_show_all", "grade.output_truncated"]
    )
    def test_key_present_in_both(self, ui: dict, key: str) -> None:
        assert key in ui["ru"]
        assert key in ui["en"]

    def test_counts_are_placeholders_not_words(self, ui: dict) -> None:
        """Числа подставляются, а не зашиты — иначе плашка врёт про объём."""
        for lang in ("ru", "en"):
            assert "{shown}" in ui[lang]["grade.diff_truncated"]
            assert "{total}" in ui[lang]["grade.diff_truncated"]
