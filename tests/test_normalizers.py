"""Tests for normalizers.py and grader._normalize_output_line.

normalizers.py is currently DEAD CODE — grader.py uses its own inline
_normalize_output_line. These tests pin down BOTH implementations and document
exactly where they agree and where they diverge, which is critical for a safe
refactoring: the merge must keep grader's _normalize_output_line semantics (the
live path used by run_single_test), not silently swap in normalize_floats.
"""

import grader
from normalizers import normalize_floats, normalize_whitespace, sort_lines


def test_normalize_floats_precision():
    assert normalize_floats("5.000000000000001") == "5.0"


def test_normalize_floats_keeps_short():
    assert normalize_floats("3.14") == "3.14"


def test_normalize_floats_no_floats():
    assert normalize_floats("hello 42") == "hello 42"


def test_sort_lines():
    assert sort_lines("b\na\nc") == "a\nb\nc"


def test_normalize_whitespace():
    assert normalize_whitespace("hello   world") == "hello world"


# ===========================================================================
# grader._normalize_output_line — the LIVE implementation used by run_single_test
# ===========================================================================


def test_normalize_output_line_float_rounding():
    """Floats are rounded to 9 decimal places via str(round(x, 9)).

    NOTE: this is 9 places, NOT 4. '1.23456789' has 8 decimals, so it is kept
    verbatim. '5.000000000000001' collapses to '5.0'.
    REFACTORING INVARIANT: the merged grader must keep 9-place str(round())
    semantics, not normalizers.normalize_floats' fixed-format-then-strip output.
    """
    assert grader._normalize_output_line("1.23456789") == "1.23456789"
    assert grader._normalize_output_line("5.000000000000001") == "5.0"
    assert grader._normalize_output_line("3.14159265358979") == "3.141592654"


def test_normalize_output_line_non_float_passthrough():
    """Non-float strings (and bare integers) pass through unchanged."""
    assert grader._normalize_output_line("hello world") == "hello world"
    assert grader._normalize_output_line("42") == "42"
    assert grader._normalize_output_line("abc 1.5 def") == "abc 1.5 def"


def test_normalize_output_line_scientific_notation():
    """A float WITH a decimal point and exponent is matched and rounded.

    The regex requires `\\d+\\.\\d+`, so a mantissa with a dot is needed:
    '1.0e-10' rounds to 0.0 at 9 places. A bare '1e-10' (no dot) is NOT matched
    and passes through unchanged.
    """
    assert grader._normalize_output_line("1.0e-10") == "0.0"
    assert grader._normalize_output_line("1e-10") == "1e-10"


def test_normalize_output_line_empty_string():
    """Empty string normalizes to empty string (no crash)."""
    assert grader._normalize_output_line("") == ""


def test_normalize_output_line_nan_inf():
    """'nan'/'inf' are not matched by the float regex (no digits+dot) → unchanged.

    Documents behavior: these tokens pass through verbatim rather than being
    parsed as special floats.
    """
    assert grader._normalize_output_line("nan") == "nan"
    assert grader._normalize_output_line("inf") == "inf"
    assert grader._normalize_output_line("-inf") == "-inf"


def test_normalize_output_line_negative_float():
    """Negative floats are matched and rounded too."""
    assert grader._normalize_output_line("-5.000000000000001") == "-5.0"


# ===========================================================================
# normalizers.py module functions
# ===========================================================================


def test_normalizers_normalize_floats_multi_line():
    """Each float on each line is normalized independently."""
    text = "5.000000000000001\n3.14\nhello"
    assert normalize_floats(text) == "5.0\n3.14\nhello"


def test_normalizers_normalize_floats_multiple_per_line():
    """Multiple floats on one line are each normalized."""
    assert normalize_floats("1.5000000001 2.25") == "1.5 2.25"


def test_normalizers_sort_lines():
    """sort_lines sorts lines lexicographically and strips surrounding blanks."""
    assert sort_lines("c\na\nb") == "a\nb\nc"
    assert sort_lines("  banana\napple\n") == "apple\nbanana"


def test_normalizers_normalize_whitespace():
    """Multiple spaces collapse to one; leading/trailing trimmed per line."""
    assert normalize_whitespace("  hello   world  ") == "hello world"
    assert normalize_whitespace("a\tb   c") == "a b c"


# ===========================================================================
# Cross-implementation invariant: where grader and normalizers AGREE / DIVERGE
# ===========================================================================


def test_normalizers_vs_grader_same_float_output():
    """On typical single-line float strings the two implementations agree.

    This is the key invariant a safe refactoring relies on for the COMMON case:
    grader._normalize_output_line and normalizers.normalize_floats produce the
    same string for ordinary values (collapsing FP noise, keeping short decimals).
    """
    for value in ("5.000000000000001", "3.14", "3.14159265358979", "1.5", "100.0", "-5.0"):
        assert grader._normalize_output_line(value) == normalize_floats(value), value


def test_normalizers_vs_grader_diverge_on_tiny_floats():
    """DIVERGENCE (important for refactoring): the two are NOT interchangeable.

    For very small magnitudes, grader uses str(round(x, 9)) which yields
    scientific notation ('1e-07'), while normalizers.normalize_floats formats with
    a fixed decimal count and strips zeros ('0.0000001'). The merge must therefore
    preserve grader._normalize_output_line — it cannot blindly substitute
    normalize_floats without changing comparison results.
    """
    assert grader._normalize_output_line("0.0000001") == "1e-07"
    assert normalize_floats("0.0000001") == "0.0000001"
    assert grader._normalize_output_line("0.0000001") != normalize_floats("0.0000001")
