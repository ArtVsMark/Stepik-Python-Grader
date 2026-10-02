"""Tests for core/error_glossary.py — единый RE-резолвер (issue #356).

Резолвер связывает два источника карточек: комплектную JSON-базу
(``glossary/data/``) и компактную карту (``core/glossary.py``). Проверяем
приоритет bundled, добор пустот из компактной, graceful degradation и то, что
CLI (reporter) и web (viewmodels) показывают одну и ту же карточку.
"""

from __future__ import annotations

from dataclasses import fields

from stepik_grader.core import error_glossary
from stepik_grader.core.error_glossary import ErrorHint, resolve_error_hint


def test_hint_carries_no_outbound_url() -> None:
    # issue #684: подсказка адресует карточку только якорем своего глоссария;
    # поля со ссылкой во внешний Glossary-Python в контракте больше нет.
    assert {f.name for f in fields(ErrorHint)} == {"exception", "hint", "anchor"}


def test_resolve_covered_exception_uses_bundled_rich_card() -> None:
    # IndexError покрыт bundled-базой: богатый summary и якорь своей карточки.
    hint = resolve_error_hint("Traceback (most recent call last):\nIndexError: list index")
    assert hint is not None
    assert hint.exception == "IndexError"
    assert hint.anchor == "indexerror"
    assert hint.hint  # непустое пояснение


def test_resolve_fills_empty_bundled_fields_from_compact() -> None:
    # У bundled-карточки ZeroDivisionError summary пустой — добираем из
    # компактной карты, чтобы подсказка не деградировала до пустой.
    hint = resolve_error_hint("ZeroDivisionError: division by zero")
    assert hint is not None
    assert hint.exception == "ZeroDivisionError"
    assert hint.hint  # непустой (из компактной карты)
    assert hint.anchor == "zerodivisionerror"


def test_resolve_unknown_and_empty_return_none() -> None:
    assert resolve_error_hint("UnlistedError: not in any base") is None
    assert resolve_error_hint("") is None
    assert resolve_error_hint("no traceback line here") is None


def test_cli_and_web_show_same_card_for_covered_exception() -> None:
    # Acceptance #356: RE-подсказка CLI (reporter) и web error card (viewmodels)
    # строятся одним резолвером — значит совпадают для покрытых исключений.
    from stepik_grader.web import viewmodels as web_vm

    error = "KeyError: 'x'"
    hint = resolve_error_hint(error)
    assert hint is not None
    view = web_vm._case_view(1, {"passed": False, "error": error, "verdict": "RE"})
    assert view["glossary"]["exception"] == hint.exception
    assert view["glossary"]["hint"] == hint.hint
    assert view["glossary"]["anchor"] == hint.anchor


def test_resolve_graceful_when_bundled_unavailable(monkeypatch) -> None:
    # Битая/отсутствующая bundled-база → тихий откат на компактную карту
    # (её якорь — имя класса в нижнем регистре).
    monkeypatch.setattr(error_glossary, "_bundled_index", dict)
    hint = resolve_error_hint("IndexError: x")
    assert hint is not None
    assert hint.exception == "IndexError"
    assert hint.anchor == "indexerror"


# ---------------------------------------------------------------------------
# issue #965 (MET-1-07): вид карточки, а не только совпадение имени
#
# Индекс строится из ВСЕХ карточек каталога, а имя берётся из последней строки
# stderr до двоеточия — то есть из строки, которую печатает проверяемый код.
# ---------------------------------------------------------------------------


def test_function_card_is_not_offered_as_an_explanation() -> None:
    """``sys.exit("input: …")`` не должен приносить карточку функции ``input()``.

    CPython печатает аргумент ``sys.exit`` в stderr без трейсбека, вердикт —
    RE, имя «исключения» получается ``input``. Карточка с таким id в базе
    есть, поэтому проверяем и её наличие: иначе тест был бы зелёным по
    случайной причине.
    """
    index = error_glossary._bundled_index()
    assert "input" in index, "карточка input исчезла из базы — тест потерял смысл"
    assert index["input"].kind == "function"

    assert resolve_error_hint("input: неверный формат") is None


def test_term_card_is_not_offered_as_an_explanation() -> None:
    """Тот же случай для термина: ``sys.exit("list: пусто")`` подсказки не даёт."""
    assert resolve_error_hint("list: пусто") is None


def test_exception_cards_still_resolve() -> None:
    """Фильтр не должен задеть то, ради чего резолвер существует."""
    for error in ("IndexError: x", "KeyError: 'k'", "RecursionError: deep"):
        assert resolve_error_hint(error) is not None, error


def test_unmarked_card_is_judged_by_its_name(monkeypatch) -> None:
    """Карточке без вида (``kind`` по умолчанию ``term``) верим по суффиксу id.

    Данные могут появиться раньше поля: новое исключение попадает в базу с
    непроставленным видом. Терять из-за этого подсказку не хочется, поэтому
    имя, кончающееся на error/exception, — запасное правило.
    """
    from stepik_grader.glossary.models import GlossaryCard

    unmarked = GlossaryCard(id="customprojecterror", title="CustomProjectError", kind="term")
    marked_function = GlossaryCard(id="logging-error", title="logging.error", kind="function")
    monkeypatch.setattr(
        error_glossary,
        "_bundled_index",
        lambda: {"customprojecterror": unmarked, "logging-error": marked_function},
    )

    assert error_glossary._is_exception_card(unmarked) is True
    assert error_glossary._is_exception_card(marked_function) is False
    hint = resolve_error_hint("CustomProjectError: boom")
    assert hint is not None
    assert hint.anchor == "customprojecterror"
