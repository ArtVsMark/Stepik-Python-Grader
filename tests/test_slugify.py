from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from at_first import slugify


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars() -> None:
    result = slugify('test<>:"/\\|?*')
    assert result == "test"


def test_slugify_cyrillic() -> None:
    result = slugify("Привет мир")
    assert result  # не пустая строка после slugify


def test_slugify_empty() -> None:
    assert slugify("") == "task"


def test_slugify_truncation() -> None:
    long_str = "a" * 100
    assert len(slugify(long_str)) <= 80


def test_slugify_yo() -> None:
    """Буква ё заменяется на е."""
    assert "е" in slugify("ёлка")
    assert "ё" not in slugify("ёлка")


def test_slugify_spaces_to_dash() -> None:
    assert slugify("hello   world") == "hello-world"
