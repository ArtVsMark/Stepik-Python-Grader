"""Модалки веб-интерфейса держат один focus-trap на всех (issue #1225).

Копий ловушки было три — онбординг (``app.js``), согласие на AI (``core.js``) и
обратная связь (``feedback.js``), — и они разошлись: фикс #804 (слушать
``keydown`` на ``document``, а НЕ на оверлее) попал только в онбординг. Пока
слушатель висит на оверлее, клик по затемнённой подложке уводит фокус на
``body``; ``body`` не потомок оверлея, событие до него не доходит вовсе — и
клавиатура перестаёт управлять окном: ``Escape`` не закрывает, ``Tab`` уходит
гулять по странице под оверлеем. Мышью при этом всё работает, поэтому дефект
незаметен при обычной проверке.

Здесь проверяется то, что проверяемо статически: обе модалки зовут общий
помощник ``openModal`` из ``core.js`` и собственного перехвата не держат.

**Чего эти тесты не доказывают:** что в браузере окно ведёт себя правильно.
Прогон с клавиатурой — открыть, кликнуть по подложке, нажать ``Escape``,
прогнать ``Tab`` по кругу — делался отдельно на локальной машине (см. PR), и
статикой такое не воспроизводится в принципе.
"""

from __future__ import annotations

import pathlib

import pytest

_STATIC = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "static"

_JS_FILES = ("app.js", "core.js", "feedback.js", "navigation.js", "statement.js")


def _block(source: str, marker: str, size: int = 2000) -> str:
    """Тело функции от её объявления — срезом, как в tests/test_statement_modal.py."""
    return source[source.index(marker) :][:size]


@pytest.fixture(scope="module")
def core_js() -> str:
    return (_STATIC / "core.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def feedback_js() -> str:
    return (_STATIC / "feedback.js").read_text(encoding="utf-8")


class TestAiConsent:
    """«Согласие на AI» — модалка core.js, вторая из трёх копий."""

    def test_uses_shared_helper(self, core_js: str) -> None:
        assert "openModal(" in _block(core_js, "function _requestAiConsent("), (
            "окно согласия не переведено на общий помощник"
        )

    def test_has_no_own_focus_trap(self, core_js: str) -> None:
        """Свой перехват Tab означал бы, что копия осталась жить."""
        block = _block(core_js, "function _requestAiConsent(")
        assert "shiftKey" not in block
        assert 'key === "Escape"' not in block

    def test_backdrop_does_not_close(self, core_js: str) -> None:
        """Вопрос с двумя вариантами не закрывается промахом мыши — как и было."""
        assert "closeOnBackdrop: false" in _block(core_js, "function _requestAiConsent(")


class TestFeedback:
    """«Обратная связь» — модалка feedback.js, третья из трёх копий."""

    def test_imports_shared_helper(self, feedback_js: str) -> None:
        head = feedback_js[: feedback_js.index('from "./core.js"')]
        assert "openModal" in head, "openModal не импортирован из core.js"

    def test_uses_shared_helper(self, feedback_js: str) -> None:
        assert "openModal(" in _block(feedback_js, "function openFeedback("), (
            "окно обратной связи не переведено на общий помощник"
        )

    def test_has_no_own_focus_trap(self, feedback_js: str) -> None:
        assert "_feedbackKeydown" not in feedback_js, "своя ловушка осталась в feedback.js"
        assert "shiftKey" not in feedback_js
        assert 'key === "Escape"' not in feedback_js

    def test_backdrop_does_not_close(self, feedback_js: str) -> None:
        """Написанный отзыв не теряется от промаха мимо диалога — как и было."""
        assert "closeOnBackdrop: false" in _block(feedback_js, "function openFeedback(")


class TestNoOverlayListeners:
    """Слушатель на оверлее — это и есть дефект #1225, где бы он ни появился."""

    @pytest.mark.parametrize("name", _JS_FILES)
    def test_keydown_never_listens_on_overlay(self, name: str) -> None:
        source = (_STATIC / name).read_text(encoding="utf-8")
        assert 'overlay.addEventListener("keydown"' not in source, (
            f"{name}: keydown на оверлее — после клика по подложке событие до него не дойдёт"
        )
