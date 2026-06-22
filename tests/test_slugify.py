"""Unit tests for at_first.slugify."""
from __future__ import annotations

from at_first import slugify


def test_slugify_basic() -> None:
    assert slugify("Hello World") == "hello-world"


def test_slugify_cyrillic_yo() -> None:
    assert slugify("Ёж") == slugify("Еж")


def test_slugify_special_chars() -> None:
    assert slugify('test<>:"/\\|?*') == "test"


def test_slugify_empty() -> None:
    assert slugify("") == "task"
    assert slugify("   ") == "task"


def test_slugify_truncation() -> None:
    long_text = "a" * 100
    result = slugify(long_text)
    assert len(result) <= 80


def test_slugify_spaces_become_dashes() -> None:
    assert slugify("hello   world") == "hello-world"


def test_slugify_strips_leading_trailing() -> None:
    result = slugify("  - hello world -  ")
    assert not result.startswith("-")
    assert not result.endswith("-")
