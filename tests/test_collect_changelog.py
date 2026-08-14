"""Тесты scripts/collect_changelog.py — записи CHANGELOG файлами (issue #997).

Смысл фрагментов — в том, что два PR не дерутся за один участок файла, поэтому
проверяется именно это: сборка не зависит от порядка появления файлов, а
негодный фрагмент называется по имени, а не молча пропадает из релиза.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "collect_changelog.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_collect_changelog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Модуль скрипта."""
    return _load_module()


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    """Каркас проекта: CHANGELOG с `[Unreleased]` и пустой changelog.d/."""
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [1.10.0] - 2026-08-01\n\n- старое\n",
        encoding="utf-8",
    )
    return tmp_path


def _fragment(project: pathlib.Path, name: str, text: str) -> pathlib.Path:
    path = project / "changelog.d" / name
    path.write_text(text, encoding="utf-8")
    return path


class TestParsing:
    """Разбор имени и содержимого фрагмента."""

    def test_name_encodes_section(self, module: ModuleType, project: pathlib.Path) -> None:
        """Секция читается из имени файла."""
        path = _fragment(project, "my-branch.fixed.md", "починили штуку (#1)\n")

        fragment = module.parse_fragment(path)

        assert fragment is not None
        assert fragment.section == "fixed"
        assert fragment.text == "починили штуку (#1)"

    def test_leading_dash_is_tolerated(self, module: ModuleType, project: pathlib.Path) -> None:
        """Привычка писать «- запись» не должна давать двойное тире."""
        path = _fragment(project, "b.added.md", "- добавили штуку (#2)\n")

        fragment = module.parse_fragment(path)

        assert fragment is not None
        assert fragment.text == "добавили штуку (#2)"

    def test_readme_is_not_a_fragment(self, module: ModuleType, project: pathlib.Path) -> None:
        """README каталога — инструкция, а не запись релиза."""
        (project / "changelog.d" / "README.md").write_text("как писать", encoding="utf-8")

        assert module.fragment_files(project / "changelog.d") == []


class TestValidation:
    """Негодный фрагмент называется по имени, а не пропадает молча."""

    def test_unknown_section_is_reported(self, module: ModuleType, project: pathlib.Path) -> None:
        """Секция вне списка — ошибка с перечислением допустимых."""
        _fragment(project, "b.improved.md", "что-то")

        problems = module.validate(project / "changelog.d")

        assert len(problems) == 1
        assert "improved" in problems[0]

    def test_bad_name_is_reported(self, module: ModuleType, project: pathlib.Path) -> None:
        """Имя без секции — ошибка про шаблон."""
        _fragment(project, "просто-заметка.md", "что-то")

        problems = module.validate(project / "changelog.d")

        assert len(problems) == 1
        assert "шаблон" in problems[0]

    def test_empty_fragment_is_reported(self, module: ModuleType, project: pathlib.Path) -> None:
        """Пустой файл — забытая запись, а не «изменений нет»."""
        _fragment(project, "b.fixed.md", "   \n")

        problems = module.validate(project / "changelog.d")

        assert len(problems) == 1
        assert "пустой" in problems[0]

    def test_healthy_set_has_no_problems(self, module: ModuleType, project: pathlib.Path) -> None:
        """Нормальные фрагменты проходят проверку."""
        _fragment(project, "a.fixed.md", "первое (#1)")
        _fragment(project, "b.added.md", "второе (#2)")

        assert module.validate(project / "changelog.d") == []


class TestCollect:
    """Сборка в `## [Unreleased]`."""

    def test_sections_render_in_fixed_order(
        self, module: ModuleType, project: pathlib.Path
    ) -> None:
        """Порядок секций фиксирован и не зависит от порядка файлов."""
        _fragment(project, "z.fixed.md", "починка (#3)")
        _fragment(project, "a.added.md", "новинка (#1)")

        moved, block = module.collect_into_changelog(project)

        assert moved == 2
        assert block.index("### Added") < block.index("### Fixed")

    def test_records_land_under_unreleased(self, module: ModuleType, project: pathlib.Path) -> None:
        """Записи попадают в [Unreleased], а не в релизную секцию."""
        _fragment(project, "a.fixed.md", "починка (#7)")

        module.collect_into_changelog(project)

        text = (project / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = text.index("## [Unreleased]")
        released = text.index("## [1.10.0]")
        assert unreleased < text.index("починка (#7)") < released

    def test_fragments_are_removed_after_collect(
        self, module: ModuleType, project: pathlib.Path
    ) -> None:
        """Собранные фрагменты удаляются — иначе попадут в релиз дважды."""
        path = _fragment(project, "a.fixed.md", "починка (#7)")

        module.collect_into_changelog(project)

        assert not path.exists()

    def test_collect_is_noop_without_fragments(
        self, module: ModuleType, project: pathlib.Path
    ) -> None:
        """Нет фрагментов — файл не трогается вовсе."""
        before = (project / "CHANGELOG.md").read_text(encoding="utf-8")

        moved, _ = module.collect_into_changelog(project)

        assert moved == 0
        assert (project / "CHANGELOG.md").read_text(encoding="utf-8") == before

    def test_missing_unreleased_section_is_loud(
        self, module: ModuleType, project: pathlib.Path
    ) -> None:
        """Без секции [Unreleased] сборка падает внятно, а не пишет мимо."""
        (project / "CHANGELOG.md").write_text("# Changelog\n\n## [1.10.0]\n", encoding="utf-8")
        _fragment(project, "a.fixed.md", "починка (#7)")

        with pytest.raises(RuntimeError, match="Unreleased"):
            module.collect_into_changelog(project)


class TestOrderIndependence:
    """Главное свойство: результат не зависит от порядка появления PR."""

    def test_two_prs_in_either_order_give_same_result(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Один и тот же набор записей собирается одинаково при любом порядке.

        Это и есть причина перехода на файлы: раньше порядок мержа решал, кто
        разводит конфликт, и записи двух PR дрались за один участок файла.
        """
        results = []
        for order in (("a.fixed.md", "b.added.md"), ("b.added.md", "a.fixed.md")):
            root = tmp_path / f"proj-{order[0]}"
            (root / "changelog.d").mkdir(parents=True)
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
            for name in order:
                text = "первое (#1)" if name.startswith("a") else "второе (#2)"
                (root / "changelog.d" / name).write_text(text, encoding="utf-8")
            module.collect_into_changelog(root)
            results.append((root / "CHANGELOG.md").read_text(encoding="utf-8"))

        assert results[0] == results[1]


class TestRealRepository:
    """Фрагменты самого проекта должны быть валидны — иначе релиз потеряет запись."""

    def test_project_fragments_are_valid(self, module: ModuleType) -> None:
        """Guard-the-guard: каталог проекта проходит собственную проверку."""
        root = pathlib.Path(__file__).parent.parent

        assert module.validate(root / "changelog.d") == []
