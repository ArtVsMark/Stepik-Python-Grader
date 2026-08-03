"""Гейты фронтенд-роутера и онбординга (issue #804, находки FER-02 и FER-04).

JS в этом проекте проверяется разбором исходника (как `check_ui_locale_guardrails.py`
и гейт `FAILURE_KIND` в `test_web_rules_insights.py`): рантайм-драйвера браузера в
зависимостях нет, а регрессии здесь тихие — URL расходится с экраном, Escape
перестаёт закрывать модалку, и ни один существующий тест этого не замечает.
"""

from __future__ import annotations

import pathlib

_STATIC = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "static"
_APP = (_STATIC / "app.js").read_text(encoding="utf-8")
_CORE = (_STATIC / "core.js").read_text(encoding="utf-8")


def _route_from_hash() -> str:
    body = _APP.split("async function routeFromHash() {", 1)[1]
    return body.split("\n}", 1)[0]


# --- FER-02: гонка навигаций ------------------------------------------------


def test_router_takes_a_cancellation_token() -> None:
    """Быстрый Back/Forward запускает две навигации — старая не должна дорисовывать."""
    body = _route_from_hash()
    assert "const seq = ++_routeSeq;" in body, "нет токена отмены навигации"


def test_every_await_in_router_is_followed_by_a_staleness_check() -> None:
    """Проверка стоит ПОСЛЕ каждого `await`, иначе гонка остаётся на своём месте.

    Именно после `await` инвокация теряет управление и может обнаружить, что её
    уже обогнали; проверка до `await` бесполезна.
    """
    body = _route_from_hash()
    lines = body.splitlines()
    awaits = [i for i, line in enumerate(lines) if "await " in line]
    assert awaits, "в роутере не осталось await — тест устарел"
    for i in awaits:
        following = "\n".join(lines[i + 1 : i + 3])
        assert "seq !== _routeSeq" in following, (
            f"после await на строке {i} нет проверки: {lines[i]!r}"
        )


# --- FER-04: онбординг закрывается с клавиатуры и кликом --------------------


def test_onboarding_keydown_is_bound_to_document() -> None:
    """После клика по подложке фокус на body, и до оверлея keydown не всплывает.

    Слушатель на самом оверлее делал Escape и перехват Tab мёртвыми ровно в тот
    момент, когда пользователь уже кликнул мимо диалога.
    """
    body = _APP.split("function openOnboarding() {", 1)[1].split("\n}", 1)[0]
    assert 'document.addEventListener("keydown", _onboardingKeydown)' in body
    assert 'overlay.addEventListener("keydown"' not in body


def test_onboarding_listener_is_removed_on_close() -> None:
    """Слушатель на document обязан сниматься — иначе он переживёт модалку."""
    body = _APP.split("function closeOnboarding() {", 1)[1].split("\n}", 1)[0]
    assert 'document.removeEventListener("keydown", _onboardingKeydown)' in body


def test_onboarding_closes_on_backdrop_click() -> None:
    body = _APP.split("function openOnboarding() {", 1)[1].split("\n}", 1)[0]
    assert "_onboardingBackdropClick" in body
    handler = _APP.split("function _onboardingBackdropClick(", 1)[1].split("\n}", 1)[0]
    # Клик ВНУТРИ диалога всплывает на оверлей — закрывать можно только сам оверлей.
    assert "e.target === e.currentTarget" in handler
    assert "closeOnboarding()" in handler
