"""Тесты для core/step_content.py — разбор Stepik API-контента (issue #302).

Выделено из test_downloader_extra.py: parse_stepik_step_url + извлечение
кода/имени функции из объектов шага/сабмишна. Чистые функции, без моков.
"""

from __future__ import annotations

import pytest

from stepik_grader.core.step_content import (
    extract_function_name,
    extract_python_code,
    extract_submission_code,
    parse_stepik_step_url,
)


class TestParseStepikStepUrl:
    """parse_stepik_step_url извлекает (lesson_id, step_position)."""

    def test_valid_url(self):
        assert parse_stepik_step_url("https://stepik.org/lesson/569749/step/4?unit=1") == (
            569749,
            4,
        )

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="URL шага"):
            parse_stepik_step_url("https://stepik.org/course/1")


class TestExtractCode:
    """extract_python_code / extract_submission_code / extract_function_name."""

    def test_code_template_from_options(self):
        step = {"block": {"options": [{"code_template": "def f(): pass"}]}}
        assert extract_python_code(step) == "def f(): pass"

    def test_code_from_markdown_block(self):
        step = {"block": {"text": "blah ```python\nx = 1\n``` end"}}
        assert extract_python_code(step) == "x = 1"

    def test_code_none_when_absent(self):
        assert extract_python_code({"block": {"text": "no code"}}) is None

    def test_submission_code(self):
        assert extract_submission_code({"reply": {"code": "print(1)"}}) == "print(1)"

    def test_submission_code_none(self):
        assert extract_submission_code(None) is None
        assert extract_submission_code({"reply": {}}) is None

    def test_function_name_extracted(self):
        assert extract_function_name("def my_func(x):\n    return x") == "my_func"

    def test_function_name_async(self):
        assert extract_function_name("async def af():\n    pass") == "af"

    def test_function_name_none_no_func(self):
        assert extract_function_name("x = 1") is None

    def test_function_name_none_syntax_error(self):
        assert extract_function_name("def (((") is None
