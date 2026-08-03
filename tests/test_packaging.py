"""Tests for packaging hygiene (issue #98 / PR-1): py.typed (#101), license (#100).

Читают метаданные установленного пакета — требуют выполненного `pip install -e .`
(как и test_cli.test_version_matches_pyproject_toml; см. CONTRIBUTING.md).
Тесты провенанса (`[project.urls]`/`authors`/`classifiers`) читают сам
`pyproject.toml`, а не установленный дистрибутив: они должны ловить регрессию
сразу после правки, не дожидаясь переустановки пакета.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import pathlib
import re
import tomllib

import pytest

import stepik_grader

_REPO_URL = "https://github.com/ArtVsMark/Stepik-Python-Grader"
_REPO_ROOT = pathlib.Path(__file__).parent.parent


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


# --- измерение покрытия: инварианты конфига (issue #790) ----------------------


def _coverage_tables() -> dict[str, dict[str, object]]:
    """Секции ``[tool.coverage.*]`` и ``[tool.pytest.ini_options]``."""
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return {
        "run": data["tool"]["coverage"]["run"],
        "paths": data["tool"]["coverage"]["paths"],
        "pytest": data["tool"]["pytest"]["ini_options"],
    }


def test_scripts_are_measured_by_coverage() -> None:
    """issue #790: ``scripts/`` меряется, а не только проверяется mypy.

    Эти 15 файлов — часть релизного конвейера (сверка версии, генерация
    ``.coveragerc.ci``, combine покрытия, публикация бейджей). Тесты на них
    были и раньше, но какие ветки те трогают, никто не знал: измерение шло
    только по ``src``, и сломанный релизный скрипт обнаружился бы в момент
    релиза. Инвариант держится тестом, потому что откат — это одна строка в
    конфиге, которая ничего не ломает и потому проходит незамеченной.
    """
    tables = _coverage_tables()
    assert "scripts" in tables["run"]["source"]
    assert "--cov=scripts" in tables["pytest"]["addopts"]


def test_scripts_paths_alias_declared_for_cross_os_combine() -> None:
    """issue #790: у ``scripts/`` есть свой aliasing в ``[tool.coverage.paths]``.

    Без него ``coverage combine`` считает один и тот же файл, записанный тремя
    job'ами матрицы под разными корнями воркспейса, тремя разными файлами — и
    cross-OS число тихо перестаёт быть агрегатом (ровно то, ради чего в #283
    заводили aliasing для пакета).
    """
    groups = _coverage_tables()["paths"]
    assert "scripts" in groups, "нет группы путей для scripts/"
    assert any(str(entry).startswith("*/") for entry in groups["scripts"]), (
        "в группе нет wildcard-варианта — данные чужих ОС не сольются"
    )


# ---------------------------------------------------------------------------
# issue #810 — целостность вендоренного CodeMirror: 360 КБ минифицированного
# стороннего JS уходит в каждую --serve-сессию и в опубликованный wheel, а в
# ревью выглядит как «файл изменён»
# ---------------------------------------------------------------------------

_VENDOR_DIR = pathlib.Path(stepik_grader.__file__).parent / "web" / "static" / "vendor"
_BUNDLE = _VENDOR_DIR / "codemirror-bundle@6.mjs"
_VERSIONS_MD = _VENDOR_DIR / "VERSIONS.md"
# Строка вида "sha256  <64 hex>" в блоке Integrity файла VERSIONS.md.
_SHA_RE = re.compile(r"sha256\s+([0-9a-f]{64})")


def _declared_bundle_sha256() -> str:
    """Хеш бандла, объявленный в ``vendor/VERSIONS.md``."""
    match = _SHA_RE.search(_VERSIONS_MD.read_text(encoding="utf-8"))
    assert match is not None, "в vendor/VERSIONS.md нет строки 'sha256 <хеш>' (issue #810)"
    return match.group(1)


def test_vendored_bundle_matches_declared_checksum() -> None:
    """Байты бандла совпадают с хешем из VERSIONS.md.

    Расхождение значит одно из двух: бандл пересобрали и забыли обновить
    хеш, либо его изменили мимо процедуры бампа. Оба случая должны быть
    видимыми, а не проходить как «файл изменён» в диффе.
    """
    actual = hashlib.sha256(_BUNDLE.read_bytes()).hexdigest()
    assert actual == _declared_bundle_sha256(), (
        "хеш vendor/codemirror-bundle@6.mjs разошёлся с VERSIONS.md. "
        "После намеренной пересборки обновите строку sha256 тем же PR: "
        f"фактический {actual}"
    )


def test_vendored_bundle_has_no_node_only_imports() -> None:
    """В бандле нет Node-зависимых импортов — ручная проверка из VERSIONS.md.

    Пункт 1 «Verify after rebuilding» существовал только как инструкция для
    человека: следующая версия пакета могла вернуть debug-путь, который
    esbuild перестанет вытряхивать, и браузер упал бы на импорте.
    """
    text = _BUNDLE.read_text(encoding="utf-8", errors="replace")
    forbidden = re.findall(r'"(events|tty|process|async_hooks|node:[a-z_]+)"', text)
    assert not forbidden, f"в бандле появились Node-импорты: {sorted(set(forbidden))}"


# ---------------------------------------------------------------------------
# issue #832 — витрина пакета на PyPI. Страница ломалась (относительные пути в
# long_description PyPI резолвит к pypi.org: hero-гиф и скриншоты не видны,
# два десятка ссылок на docs/ дают 404), keywords не было вовсе, а входа в
# документацию со страницы пакета не существовало.
# ---------------------------------------------------------------------------


def test_readme_declared_for_pypi_has_no_relative_links() -> None:
    """Readme пакета ссылается абсолютно — иначе половина страницы битая.

    Дублирует гейт ``check_docs_guardrails``, но с другой стороны: гейт живёт в
    job'е docs-guardrails, а этот тест падает в обычном прогоне — чинить успеешь
    до пуша.
    """
    name = _project_table()["readme"]
    assert isinstance(name, str)
    text = (_REPO_ROOT / name).read_text(encoding="utf-8")
    relative = [
        target
        for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", text)
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert not relative, f"относительные ссылки в readme пакета: {sorted(set(relative))}"


def test_keywords_cover_the_features_people_search_for() -> None:
    """PyPI ранжирует по name/summary/keywords: без keywords ищут только по имени."""
    keywords = _project_table()["keywords"]
    assert isinstance(keywords, list)
    assert {"stepik", "autograder", "benchmark", "sandbox", "glossary"} <= set(keywords)
    assert all(k == k.lower() and " " not in k for k in keywords), keywords


def test_summary_names_the_distinguishing_features() -> None:
    """Summary — второе поле ранжирования; редкие фичи должны в нём звучать."""
    description = _project_table()["description"].lower()
    for word in ("offline", "benchmark", "sandbox"):
        assert word in description, f"{word!r} нет в summary — по нему пакет не найдут"


def test_documentation_url_is_declared_and_resolves_in_repo() -> None:
    """Со страницы пакета есть дверь в docs/, и она ведёт в существующий файл."""
    urls = _project_table()["urls"]
    assert "Documentation" in urls, "у поставившего через pipx нет входа в документацию"
    docs_url = urls["Documentation"]
    assert docs_url.startswith(f"{_REPO_URL}/blob/main/")
    target = _REPO_ROOT / docs_url.removeprefix(f"{_REPO_URL}/blob/main/")
    assert target.is_file(), f"Documentation ведёт в несуществующий {target}"


# ---------------------------------------------------------------------------
# issue #835 (GROW-04/GROW-05) — issue-формы: двуязычные подписи и область.
# EN-поверхность (README.en.md, `?lang=en`) обрывалась ровно на точке конверсии
# в контрибьютора: англоязычный читатель открывал форму и видел только русский.
# ---------------------------------------------------------------------------

_FORMS_DIR = _REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
_FORMS = ("bug_report.yml", "idea.yml", "task_problem.yml")


def _form_lines(name: str) -> list[str]:
    return (_FORMS_DIR / name).read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("form", _FORMS)
def test_form_title_and_description_are_bilingual(form: str) -> None:
    """`name`/`description` формы читаются и по-русски, и по-английски."""
    head = _form_lines(form)[:2]
    for line in head:
        assert line.startswith(("name:", "description:")), line
        assert " / " in line, f"{form}: подпись не двуязычная — {line!r}"


@pytest.mark.parametrize("form", _FORMS)
def test_every_visible_field_label_is_bilingual(form: str) -> None:
    """Подписи полей тоже: форма наполовину по-русски хуже, чем целиком."""
    labels = [ln.split("label:", 1)[1].strip() for ln in _form_lines(form) if "label:" in ln]
    assert labels, form
    monolingual = [lbl for lbl in labels if " / " not in lbl]
    assert not monolingual, f"{form}: подписи без английского — {monolingual}"


def test_bug_form_asks_for_the_area() -> None:
    """Область спрашивается у того, кто её знает, — форма не может её угадать."""
    text = (_FORMS_DIR / "bug_report.yml").read_text(encoding="utf-8")
    assert "id: area" in text
    assert "type: dropdown" in text


def test_task_problem_form_carries_navigation_labels() -> None:
    """У этой формы область известна заранее — метки навигации статические."""
    text = (_FORMS_DIR / "task_problem.yml").read_text(encoding="utf-8")
    labels = re.search(r"^labels:\s*\[(.+)\]", text, re.M)
    assert labels is not None
    declared = {part.strip().strip('"') for part in labels.group(1).split(",")}
    assert {"bug", "area/core", "downloader"} <= declared
