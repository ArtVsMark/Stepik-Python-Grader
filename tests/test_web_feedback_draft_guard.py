"""Гейт на защиту от гонки ответов черновика обратной связи (issue #803, FES-03).

`refreshFeedbackDraft()` асинхронна, а debounce гасит только таймер, но не сам
запрос: ответ на набранное раньше мог прийти позже нового и перезаписать
предпросмотр вместе с `href` кнопки — пользователь видел бы одно, а отправлял
другое. Лечится токеном актуальности (`_draftSeq`), как `_routeSeq` в роутере.

Почему статическая проверка, а не e2e: доставить поздний ответ на ранний
запрос из Playwright детерминированно не выходит — придержанный
`route.fulfill()` до страницы уже не доезжает, и такой тест зеленеет и без
фикса (проверено). Гейт ловит то, что проверить можно: сравнение токена после
`await` из `refreshFeedbackDraft` никуда не делось. Разбор JS — по исходнику,
как в `tests/test_web_rules_insights.py`.
"""

from __future__ import annotations

import pathlib
import re

_FEEDBACK_JS = (
    pathlib.Path(__file__).parent.parent
    / "src"
    / "stepik_grader"
    / "web"
    / "static"
    / "feedback.js"
)


def _refresh_draft_body() -> str:
    """Тело `refreshFeedbackDraft()` — от объявления до закрывающей скобки."""
    source = _FEEDBACK_JS.read_text(encoding="utf-8")
    start = source.index("async function refreshFeedbackDraft(")
    return source[start:].split("\n}", 1)[0]


def test_draft_refresh_keeps_a_staleness_token() -> None:
    """Токен берётся до запроса и сверяется после — иначе гонка возвращается."""
    body = _refresh_draft_body()
    assert re.search(r"\bconst\s+seq\s*=\s*\+\+_draftSeq\b", body), (
        "в refreshFeedbackDraft нет захвата токена актуальности"
    )
    assert re.search(r"if\s*\(\s*seq\s*!==\s*_draftSeq\s*\)\s*return", body), (
        "результат применяется без проверки, что запрос не устарел"
    )


def test_staleness_check_comes_after_the_await() -> None:
    """Проверка до `await` бессмысленна: обогнать могут именно во время ожидания."""
    body = _refresh_draft_body()
    await_at = body.index("await _fetchDraft()")
    check_at = body.index("seq !== _draftSeq")
    assert check_at > await_at, "сверка токена стоит до await — гонку она не ловит"


def test_links_are_disabled_before_the_new_draft_arrives() -> None:
    """FES-02: прежняя ссылка гасится до запроса, а не только при его провале.

    Иначе между сменой типа обращения и ответом сервера кнопка ведёт в форму
    прежнего вида с прежним текстом.
    """
    body = _refresh_draft_body()
    disable_at = body.index("_disableFeedbackLinks()")
    await_at = body.index("await _fetchDraft()")
    assert disable_at < await_at, "ссылки гасятся уже после ответа — окно рассинхрона осталось"


def test_disable_helper_clears_both_links() -> None:
    """Гасим обе ссылки: «Открыть форму» и «Обсуждения» — они из одного черновика."""
    source = _FEEDBACK_JS.read_text(encoding="utf-8")
    helper = source[source.index("function _disableFeedbackLinks(") :].split("\n}", 1)[0]
    assert "feedback-open-github" in helper
    assert "feedback-discussions" in helper
    assert "removeAttribute" in helper and 'setAttribute("aria-disabled", "true")' in helper
