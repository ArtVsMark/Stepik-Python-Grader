"""Tests for core/glossary.py — карта исключений → подсказка + ссылка (issue #72).

Плюс интеграция: CLI-подсказка (reporter.print_case_verbose) и веб-карточка
(web._case_view) при вердикте RE.
"""

from __future__ import annotations

from stepik_grader.core.glossary import (
    GLOSSARY_BASE_URL,
    GlossaryEntry,
    all_entries,
    lookup,
    lookup_from_error,
)

# ---------------------------------------------------------------------------
# GlossaryEntry
# ---------------------------------------------------------------------------


def test_entry_default_anchor_is_lowercased_class() -> None:
    e = GlossaryEntry(exception="RecursionError", hint="…")
    assert e.anchor == "recursionerror"
    assert e.url == f"{GLOSSARY_BASE_URL}#recursionerror"


def test_entry_custom_slug_overrides_anchor() -> None:
    e = GlossaryEntry(exception="ZeroDivisionError", hint="…", slug="division")
    assert e.anchor == "division"
    assert e.url.endswith("#division")


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def test_lookup_known_exception() -> None:
    e = lookup("KeyError")
    assert e is not None
    assert e.exception == "KeyError"
    assert e.hint


def test_lookup_unknown_returns_none() -> None:
    assert lookup("TotallyMadeUpError") is None


# ---------------------------------------------------------------------------
# lookup_from_error — парсинг трейсбека
# ---------------------------------------------------------------------------


def test_lookup_from_error_parses_last_traceback_line() -> None:
    tb = (
        "Traceback (most recent call last):\n"
        '  File "sol.py", line 3, in <module>\n'
        "    d[missing]\n"
        "KeyError: 'missing'\n"
    )
    e = lookup_from_error(tb)
    assert e is not None
    assert e.exception == "KeyError"


def test_lookup_from_error_recursion() -> None:
    e = lookup_from_error("RecursionError: maximum recursion depth exceeded")
    assert e is not None and e.exception == "RecursionError"


def test_lookup_from_error_strips_module_prefix() -> None:
    """foo.BarError → BarError; здесь берём реальное — decimal.DivisionByZero не в наборе,
    а os.OSError-подобный префикс отбрасывается до известного имени."""
    e = lookup_from_error("some.module.OSError: disk full")
    assert e is not None and e.exception == "OSError"


def test_lookup_from_error_no_match_returns_none() -> None:
    assert lookup_from_error("CustomProjectError: boom") is None


def test_lookup_from_error_empty_returns_none() -> None:
    assert lookup_from_error("") is None
    assert lookup_from_error("   \n  \n") is None


# ---------------------------------------------------------------------------
# all_entries — issue #125, fallback-контент раздела «Глоссарий»
# ---------------------------------------------------------------------------


def test_all_entries_returns_every_curated_record() -> None:
    from stepik_grader.core.glossary import _ENTRIES

    entries = all_entries()
    assert len(entries) == len(_ENTRIES)
    assert all(isinstance(e, GlossaryEntry) for e in entries)
    names = {e.exception for e in entries}
    assert "RecursionError" in names
    assert "KeyError" in names


def test_all_entries_returns_a_copy_not_the_live_dict_values() -> None:
    """Mutating the returned list must not affect subsequent calls."""
    entries = all_entries()
    entries.clear()
    assert len(all_entries()) > 0


# ---------------------------------------------------------------------------
# Интеграция: CLI (reporter) и web
# ---------------------------------------------------------------------------


def test_reporter_verbose_prints_glossary_hint(capsys) -> None:
    from stepik_grader.core import reporter
    from stepik_grader.core.test_loader import TestCase

    case = TestCase(index=1, input_lines=["1"], expected_lines=["1"])
    result = {
        "passed": False,
        "error": "RecursionError: maximum recursion depth exceeded",
        "verdict": "RE",
        "expected": ["1"],
        "output": [],
        "diff": "",
    }
    reporter.print_case_verbose(case, result)
    out = capsys.readouterr().out
    assert "RecursionError" in out
    assert "artvsmark.github.io/Glossary-Python" in out


def test_reporter_verbose_no_hint_for_unknown_error(capsys) -> None:
    from stepik_grader.core import reporter
    from stepik_grader.core.test_loader import TestCase

    case = TestCase(index=1, input_lines=["1"], expected_lines=["1"])
    result = {
        "passed": False,
        "error": "CustomProjectError: boom",
        "verdict": "RE",
        "expected": ["1"],
        "output": [],
        "diff": "",
    }
    reporter.print_case_verbose(case, result)
    out = capsys.readouterr().out
    assert "Glossary-Python" not in out


def test_web_case_view_includes_glossary_on_error() -> None:
    from stepik_grader import web

    view = web._case_view(1, {"passed": False, "error": "KeyError: 'x'", "verdict": "RE"})
    assert "glossary" in view
    assert view["glossary"]["exception"] == "KeyError"
    assert view["glossary"]["url"].endswith("#keyerror")


def test_web_case_view_no_glossary_when_passed() -> None:
    from stepik_grader import web

    view = web._case_view(1, {"passed": True, "error": "", "verdict": "AC"})
    assert "glossary" not in view
