"""Тесты core/reporter.py — то, что читает человек, а не машина.

Отчёт грейдера — единственное, по чему студент понимает, что не так с его
решением. Здесь проверяется случай, где отчёт молчал: различие, которого не
видно на экране.
"""

from __future__ import annotations

from stepik_grader.core import reporter

# ---------------------------------------------------------------------------
# STR-5-03 (issue #996) — WA, где Expected и Actual выглядят одинаково
# ---------------------------------------------------------------------------


class TestInvisibleDifference:
    """Различие, которого не видно на экране, называется словами.

    Самый тупиковый WA: две одинаково напечатанные строки и нечего править.
    Подсказка появляется только в этом случае — иначе стала бы шумом на каждом
    обычном расхождении.
    """

    def test_trailing_space_is_named(self) -> None:
        hint = reporter.invisible_difference("ответ ", "ответ")

        assert "пробелы в конце строки" in hint

    def test_non_breaking_space_is_named(self) -> None:
        hint = reporter.invisible_difference("a b", "a b")

        assert "U+00A0" in hint

    def test_zero_width_space_is_named(self) -> None:
        hint = reporter.invisible_difference("ab", "a​b")

        assert "U+200B" in hint

    def test_visible_difference_gets_no_hint(self) -> None:
        """Обычное расхождение объясняет себя само — подсказка была бы шумом."""
        assert reporter.invisible_difference("42", "43") == ""

    def test_identical_values_get_no_hint(self) -> None:
        assert reporter.invisible_difference("одно и то же", "одно и то же") == ""

    def test_control_chars_are_visible_after_escaping(self) -> None:
        """Управляющие символы уже показываются экранированием — это не «невидимо»."""
        assert reporter.invisible_difference("a\rb", "ab") == ""

    def test_hint_starts_with_the_reason(self) -> None:
        """Первое слово подсказки — сама причина, а не украшение."""
        assert reporter.invisible_difference("итог ", "итог").startswith(
            "различие не видно на экране"
        )
