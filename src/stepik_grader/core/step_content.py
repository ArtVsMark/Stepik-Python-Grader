"""step_content.py — извлечение данных из ответов Stepik API (issue #302).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Выделено из ``downloader.py`` (SRP): чистые функции ``dict/str -> данные`` над
объектами шага/сабмишна Stepik и URL шага. Ни сети, ни ФС, ни оркестрации —
поэтому тестируются напрямую, без моков.
"""

from __future__ import annotations

import ast
import re
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "extract_function_name",
    "extract_python_code",
    "extract_submission_code",
    "parse_stepik_step_url",
    "pick_solutions_thread",
]


def parse_stepik_step_url(step_url: str) -> tuple[int, int]:
    """Извлекает (lesson_id, step_position) из URL шага Stepik."""
    parsed = urlparse(step_url.strip())
    match = re.search(r"lesson/(\d+)/step/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            "Не удалось распознать URL шага. Ожидается формат:\n"
            "https://stepik.org/lesson/569749/step/4?unit=564263"
        )
    return int(match.group(1)), int(match.group(2))


def extract_python_code(step: dict[str, Any]) -> str | None:
    """Извлекает Python code_template из объекта шага или из блока Markdown."""
    block: dict[str, Any] = step.get("block") or {}
    for option in block.get("options") or []:
        if isinstance(option, dict) and option.get("code_template"):
            return str(option["code_template"])
    text = str(block.get("text", ""))
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def extract_submission_code(submission: dict[str, Any] | None) -> str | None:
    """Извлекает Python-код из объекта последнего сабмишна или возвращает None."""
    if not submission:
        return None
    reply: dict[str, Any] = submission.get("reply") or {}
    code = reply.get("code")
    return str(code) if code else None


def pick_solutions_thread(threads: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Возвращает thread ветки решений (``thread == "solutions"``) или None.

    У шага два thread'а: ``"default"`` (обычные обсуждения) и ``"solutions"``
    (закреплённые/пользовательские решения, открывается после сдачи, issue #55).
    """
    for thread in threads:
        if thread.get("thread") == "solutions":
            return thread
    return None


def extract_function_name(template_code: str) -> str | None:
    """Парсит template_code через ast и возвращает имя первой функции.

    Возвращает None если в шаблоне нет определений функций или код
    не является валидным Python.
    """
    try:
        tree = ast.parse(template_code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return None
