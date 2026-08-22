"""Узкий экран прокручивается, «уменьшить движение» — не строб (issue #970).

Находки DES-1-01 и DES-1-02, обе в ``web/static/app.css``.

**Прокрутка.** Десктопная раскладка держит единственную scroll-область внутри
``.panel-body``, а ``body`` закрыт ``overflow: hidden``. На ``max-width: 768px``
оболочка получала ``height: auto``, из-за чего ряд ``1fr`` резолвился по
max-content: у ``.panel-body`` пропадала опора для ``flex: 1``, и он переставал
прокручиваться — при том, что прокрутка документа осталась заблокированной.
Итог на телефоне: контент под сгибом недоступен вовсе, и расширить окно человеку
нечем.

**Движение.** Блок ``prefers-reduced-motion`` гасил длительность, но не число
повторов. ``.skeleton`` и ``.progress-indeterminate`` объявлены с ``infinite``,
а ``0.01ms × infinite`` — это не остановка, а перезапуск каждый кадр. Настройка,
которую включают по медицинским причинам, выдавала ровно то, от чего человек ею
и защищается.

Тесты читают каскад текстом: браузерного движка в облачной сессии нет. Прогон в
браузере (метрики до/после, обе раскладки) описан в PR.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_CSS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "stepik_grader"
    / "web"
    / "static"
    / "app.css"
)


@pytest.fixture
def css() -> str:
    return _CSS.read_text(encoding="utf-8")


def _media_block(css: str, condition: str) -> str:
    """Тело ``@media`` от условия до закрывающей скобки блока."""
    start = css.index(f"@media {condition}")
    depth = 0
    for i in range(css.index("{", start), len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    raise AssertionError(f"незакрытый блок @media {condition}")


class TestNarrowScreenScrolls:
    """На ≤768px прокручивается документ — значит его никто не должен запирать."""

    @pytest.fixture
    def narrow(self, css: str) -> str:
        return _media_block(css, "(max-width: 768px)")

    def test_document_scroll_is_unlocked(self, narrow: str) -> None:
        """``body { overflow: hidden }`` из десктопа здесь обязан сниматься."""
        assert re.search(r"body\s*\{[^}]*overflow:\s*auto", narrow)

    def test_chain_does_not_clip(self, narrow: str) -> None:
        """Одной оболочки мало: `hidden` в середине цепочки режет так же."""
        assert re.search(r"\.main,\s*\.main-view\s*\{[^}]*overflow:\s*visible", narrow)

    def test_panel_body_stops_being_the_scroller(self, narrow: str) -> None:
        """Внутренний скролл без опоры по высоте не работает — отдаём документу."""
        assert re.search(r"\.panel-body\s*\{[^}]*overflow-y:\s*visible", narrow)

    def test_shell_row_is_content_sized(self, narrow: str) -> None:
        """Ряд `1fr` при `height: auto` и был причиной потери опоры."""
        assert "grid-template-rows: var(--topbar-h) auto auto" in narrow


class TestDesktopLayoutUntouched:
    """Правка мобильной раскладки не должна менять десктопную."""

    def test_body_still_clips_outside_the_media_query(self, css: str) -> None:
        head = css[: css.index("@media (max-width: 768px)")]

        assert re.search(r"body\s*\{[^}]*overflow:\s*hidden", head)

    def test_main_still_clips_outside_the_media_query(self, css: str) -> None:
        head = css[: css.index("@media (max-width: 768px)")]

        assert re.search(r"\.main\s*\{[^}]*overflow:\s*hidden", head)


class TestReducedMotionActuallyStops:
    """Гасится не только длительность, иначе получается строб."""

    @pytest.fixture
    def reduced(self, css: str) -> str:
        return _media_block(css, "(prefers-reduced-motion: reduce)")

    def test_iteration_count_is_capped(self, reduced: str) -> None:
        """Ровно то, чего не хватало: без этого 0.01ms × infinite = мерцание."""
        assert "animation-iteration-count: 1 !important" in reduced

    def test_duration_is_still_suppressed(self, reduced: str) -> None:
        assert "animation-duration: 0.01ms !important" in reduced
        assert "transition-duration: 0.01ms !important" in reduced

    def test_smooth_scrolling_is_off_too(self, reduced: str) -> None:
        """Плавная прокрутка — тоже движение, а `html` объявляет её глобально."""
        assert "scroll-behavior: auto" in reduced

    def test_infinite_animations_still_exist(self, css: str) -> None:
        """Сторож смысла: не станет `infinite` — правило выше потеряет повод.

        Тест не требует их сохранять, а фиксирует связь: если анимации однажды
        перестанут быть бесконечными, эту проверку и правило стоит пересмотреть
        вместе, а не оставлять как непонятный артефакт.
        """
        assert css.count("infinite") >= 2
