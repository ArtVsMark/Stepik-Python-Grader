"""Tests for packaging hygiene (issue #98 / PR-1): py.typed (#101), license (#100).

Читают метаданные установленного пакета — требуют выполненного `pip install -e .`
(как и test_cli.test_version_matches_pyproject_toml; см. CONTRIBUTING.md).
Тесты провенанса (`[project.urls]`/`authors`/`classifiers`) читают сам
`pyproject.toml`, а не установленный дистрибутив: они должны ловить регрессию
сразу после правки, не дожидаясь переустановки пакета.
"""

from __future__ import annotations

import importlib.metadata
import pathlib
import tomllib

import stepik_grader

_REPO_URL = "https://github.com/ArtVsMark/Stepik-Python-Grader"


def _project_table() -> dict[str, object]:
    """Секция ``[project]`` из ``pyproject.toml`` репозитория."""
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    project: dict[str, object] = data["project"]
    return project


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


def test_project_urls_lead_back_to_the_repository() -> None:
    """Страница пакета на PyPI ведёт в проект: репозиторий, changelog, трекер.

    Провенанс «дистрибутив ↔ репозиторий» выражен в метаданных, а не только в
    README: установивший через `pipx install stepik-python-grader` попадает со
    страницы пакета в исходники, историю изменений и issue-трекер.
    """
    urls = _project_table()["urls"]
    assert isinstance(urls, dict)
    assert set(urls) >= {"Homepage", "Repository", "Changelog", "Issues"}
    assert urls["Homepage"] == _REPO_URL
    assert urls["Repository"] == _REPO_URL
    assert urls["Changelog"] == f"{_REPO_URL}/blob/main/CHANGELOG.md"
    assert urls["Issues"] == f"{_REPO_URL}/issues"


def test_authors_are_declared() -> None:
    """`authors` непуст и несёт имя — иначе METADATA молчит об авторстве."""
    authors = _project_table()["authors"]
    assert isinstance(authors, list)
    assert authors, "authors не должен быть пустым"
    assert all(isinstance(a, dict) and a.get("name") for a in authors)


def test_no_license_classifier_alongside_spdx_expression() -> None:
    """PEP 639: `License ::` рядом с SPDX-выражением ломает сборку целиком.

    `license = "MIT"` — SPDX-выражение, и setuptools>=77 на пару «выражение +
    классификатор» падает с InvalidConfigError ещё на get_requires_for_build.
    Регрессия здесь стоит не косметики, а всего дистрибутива, поэтому проверяется
    отдельно от остальных классификаторов.
    """
    project = _project_table()
    classifiers = project["classifiers"]
    assert isinstance(classifiers, list)
    assert project.get("license") == "MIT"
    offenders = [c for c in classifiers if str(c).startswith("License ::")]
    assert not offenders, f"классификаторы лицензии несовместимы с SPDX: {offenders}"


def test_python_classifiers_agree_with_requires_python() -> None:
    """Версии в `Programming Language :: Python :: X.Y` не ниже пола requires-python.

    Классификаторы — самая заметная часть страницы пакета и первыми протухают:
    подняли `requires-python`, а «3.10» в списке осталась обещать поддержку.
    """
    project = _project_table()
    requires_python = project["requires-python"]
    assert isinstance(requires_python, str)
    floor = tuple(int(p) for p in requires_python.removeprefix(">=").split("."))

    classifiers = project["classifiers"]
    assert isinstance(classifiers, list)
    prefix = "Programming Language :: Python :: "
    declared = [
        str(c).removeprefix(prefix)
        for c in classifiers
        if str(c).startswith(prefix) and str(c).removeprefix(prefix)[:1].isdigit()
    ]
    versions = [tuple(int(p) for p in v.split(".")) for v in declared if "." in v]
    assert versions, "не объявлено ни одной минорной версии Python"
    assert all(v >= floor for v in versions), (
        f"классификаторы обещают версии ниже requires-python={requires_python}: {declared}"
    )
