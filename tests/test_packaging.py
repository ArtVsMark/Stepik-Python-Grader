"""Tests for packaging hygiene (issue #98 / PR-1): py.typed (#101), license (#100).

Читают метаданные установленного пакета — требуют выполненного `pip install -e .`
(как и test_cli.test_version_matches_pyproject_toml; см. CONTRIBUTING.md).
"""

from __future__ import annotations

import importlib.metadata
import pathlib

import stepik_grader


def test_py_typed_marker_is_shipped() -> None:
    """issue #101: PEP 561 маркер py.typed лежит рядом с пакетом (типы видны downstream)."""
    marker = pathlib.Path(stepik_grader.__file__).parent / "py.typed"
    assert marker.exists()


def test_license_is_mit_in_metadata() -> None:
    """issue #100: лицензия MIT объявлена в метаданных (PEP 639 SPDX-выражение)."""
    md = importlib.metadata.metadata("stepik-python-grader")
    # setuptools>=77 пишет SPDX в поле License-Expression.
    assert md.get("License-Expression") == "MIT"


def test_license_file_present_in_repo() -> None:
    """LICENSE есть в корне репозитория (источник для license-files)."""
    root = pathlib.Path(__file__).parent.parent
    license_path = root / "LICENSE"
    assert license_path.is_file()
    assert "MIT License" in license_path.read_text(encoding="utf-8")
