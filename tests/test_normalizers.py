"""Tests for normalizers.py."""
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
