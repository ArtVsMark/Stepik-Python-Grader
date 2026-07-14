"""Tests for core/error_glossary.py — единый RE-резолвер (issue #356).

Резолвер связывает два источника карточек: комплектную JSON-базу
(``glossary/data/``) и компактную карту (``core/glossary.py``). Проверяем
приоритет bundled, добор пустот из компактной, graceful degradation и то, что
CLI (reporter) и web (viewmodels) показывают одну и ту же карточку.
"""

from __future__ import annotations

from stepik_grader.core import error_glossary
from stepik_grader.core.error_glossary import card_url, resolve_error_hint
from stepik_grader.core.glossary import GLOSSARY_BASE_URL


def test_card_url_prefers_static_url() -> None:
    assert card_url("https://x/#e-IndexError", "indexerror") == "https://x/#e-IndexError"


def test_card_url_computes_anchor_when_empty() -> None:
    assert card_url("", "indexerror") == f"{GLOSSARY_BASE_URL}#indexerror"


def test_resolve_covered_exception_uses_bundled_rich_card() -> None:
    # IndexError покрыт bundled-базой: богатый summary и DOM-анкор витрины.
    hint = resolve_error_hint("Traceback (most recent call last):\nIndexError: list index")
    assert hint is not None
    assert hint.exception == "IndexError"
    assert hint.anchor == "indexerror"
    assert hint.url.endswith("#e-IndexError")
    assert hint.hint  # непустое пояснение


def test_resolve_fills_empty_bundled_fields_from_compact() -> None:
    # У bundled-карточки ZeroDivisionError summary/url пустые — добираем из
    # компактной карты, чтобы подсказка не деградировала до пустой.
    hint = resolve_error_hint("ZeroDivisionError: division by zero")
    assert hint is not None
    assert hint.exception == "ZeroDivisionError"
    assert hint.hint  # непустой (из компактной карты)
    assert hint.url  # непустой


def test_resolve_unknown_and_empty_return_none() -> None:
    assert resolve_error_hint("UnlistedError: not in any base") is None
    assert resolve_error_hint("") is None
    assert resolve_error_hint("no traceback line here") is None


def test_cli_and_web_show_same_card_for_covered_exception() -> None:
    # Acceptance #356: RE-подсказка CLI (reporter) и web error card (viewmodels)
    # строятся одним резолвером — значит совпадают для покрытых исключений.
    from stepik_grader import web

    error = "KeyError: 'x'"
    hint = resolve_error_hint(error)
    assert hint is not None
    view = web._case_view(1, {"passed": False, "error": error, "verdict": "RE"})
    assert view["glossary"]["exception"] == hint.exception
    assert view["glossary"]["hint"] == hint.hint
    assert view["glossary"]["url"] == hint.url


def test_resolve_graceful_when_bundled_unavailable(monkeypatch) -> None:
    # Битая/отсутствующая bundled-база → тихий откат на компактную карту
    # (её вычисляемый анкор #indexerror вместо DOM-анкора витрины).
    monkeypatch.setattr(error_glossary, "_bundled_index", dict)
    hint = resolve_error_hint("IndexError: x")
    assert hint is not None
    assert hint.exception == "IndexError"
    assert hint.url.endswith("#indexerror")
