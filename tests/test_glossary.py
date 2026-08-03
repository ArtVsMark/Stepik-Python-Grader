"""Tests for core/glossary.py — карта исключений → подсказка + якорь (issue #72).

Плюс интеграция: CLI-подсказка (reporter.print_case_verbose) и веб-карточка
(web_vm._case_view) при вердикте RE.
"""

from __future__ import annotations

from stepik_grader.core.glossary import (
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


def test_entry_custom_slug_overrides_anchor() -> None:
    e = GlossaryEntry(exception="ZeroDivisionError", hint="…", slug="division")
    assert e.anchor == "division"


def test_entry_has_no_outbound_url() -> None:
    # issue #684: запись адресует только свою карточку (anchor); ссылки во
    # внешний Glossary-Python у неё нет — он копия этой базы, не эталон.
    assert not hasattr(GlossaryEntry(exception="KeyError", hint="…"), "url")


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
    assert "💡" in out  # сама подсказка напечатана
    # issue #684: ссылки во внешний глоссарий-копию в выводе больше нет.
    assert "artvsmark.github.io" not in out


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
    assert "💡" not in out  # неизвестное исключение — подсказки нет


def test_web_case_view_includes_glossary_on_error() -> None:
    from stepik_grader.web import viewmodels as web_vm

    view = web_vm._case_view(1, {"passed": False, "error": "KeyError: 'x'", "verdict": "RE"})
    assert "glossary" in view
    assert view["glossary"]["exception"] == "KeyError"
    # issue #684: карточка адресуется якорем СВОЕЙ базы (deep-link
    # #/glossary/keyerror), поля url со ссылкой на внешнюю витрину больше нет.
    assert view["glossary"]["anchor"] == "keyerror"
    assert "url" not in view["glossary"]


def test_web_case_view_no_glossary_when_passed() -> None:
    from stepik_grader.web import viewmodels as web_vm

    view = web_vm._case_view(1, {"passed": True, "error": "", "verdict": "AC"})
    assert "glossary" not in view


# ---------------------------------------------------------------------------
# Ratchet: никаких ссылок на витрину-копию (issue #684)
# ---------------------------------------------------------------------------


def test_package_never_links_to_external_glossary_showcase() -> None:
    """Ни один рантайм-артефакт пакета не ссылается на витрину Glossary-Python.

    Инвариант «Истина глоссария» (CLAUDE.md): база этого проекта — источник,
    внешний глоссарий — односторонняя копия. Ссылка из оригинала в копию уводит
    студента на устаревшую карточку, поэтому её нет ни в данных (`glossary/data`),
    ни в коде, ни в статике/локалях web-оболочки.
    """
    import pathlib

    import stepik_grader

    root = pathlib.Path(stepik_grader.__file__).parent
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".js", ".json", ".html", ".css"}
        and "artvsmark.github.io" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []
