"""Тесты scripts/check_wheel_contents.py — что реально уехало в колесо (issue #953).

Гейт нужен потому, что все остальные проверки смотрят не туда: тесты пакета
читают дерево ИСХОДНИКОВ через `stepik_grader.__file__`, а `twine check` —
метаданные. Сломанный glob в `[tool.setuptools.package-data]` для них
невидим, и на PyPI уезжает wheel без вендоренного CodeMirror и шрифтов —
`--serve` у пользователя открывается без редактора. Версия в PyPI
неперезаписываема, поэтому ловить это надо до публикации.

Проверяется на настоящих zip-архивах: смысл скрипта — заглянуть внутрь
артефакта, и подмена этого слоя проверяла бы что угодно, кроме него.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import zipfile
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_wheel_contents.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_wheel_contents", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Модуль скрипта."""
    return _load_module()


def _wheel(path: pathlib.Path, names: list[str]) -> pathlib.Path:
    """Собрать поддельное колесо с заданным набором путей внутри."""
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return path


def _full_contents(module: ModuleType) -> list[str]:
    """Набор путей, покрывающий все обязательные шаблоны."""
    return [pattern.replace("*", "sample") for pattern in module.REQUIRED_PATTERNS]


class TestCompleteWheel:
    """Колесо со всем содержимым проходит."""

    def test_full_wheel_has_no_problems(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Все обязательные шаблоны совпали — претензий нет."""
        wheel = _wheel(tmp_path / "ok.whl", _full_contents(module))

        assert module.check_wheel(wheel) == []

    def test_main_reports_success(self, module: ModuleType, tmp_path: pathlib.Path, capsys) -> None:
        """Успешный прогон говорит, сколько колёс проверено."""
        wheel = _wheel(tmp_path / "ok.whl", _full_contents(module))

        assert module.main([str(wheel)]) == 0
        assert "OK" in capsys.readouterr().out


class TestBrokenPackageData:
    """Сломанный glob в package-data обязан валить сборку."""

    def test_missing_vendor_bundle_is_caught(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Без вендор-бандла у пользователя `--serve` открывается без редактора."""
        contents = [n for n in _full_contents(module) if "vendor" not in n]
        wheel = _wheel(tmp_path / "novendor.whl", contents)

        problems = module.check_wheel(wheel)

        assert len(problems) == 1
        assert "vendor" in problems[0]

    def test_missing_fonts_is_caught(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Шрифты — тоже package-data, и пропадают тем же способом."""
        contents = [n for n in _full_contents(module) if "fonts" not in n]

        problems = module.check_wheel(_wheel(tmp_path / "nofonts.whl", contents))

        assert len(problems) == 1
        assert "fonts" in problems[0]

    def test_missing_py_typed_is_caught(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Без py.typed downstream-потребитель теряет типы (PEP 561)."""
        contents = [n for n in _full_contents(module) if not n.endswith("py.typed")]

        problems = module.check_wheel(_wheel(tmp_path / "notyped.whl", contents))

        assert len(problems) == 1
        assert "py.typed" in problems[0]

    def test_main_exits_nonzero_on_broken_wheel(
        self, module: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Код возврата — то, чем гейт останавливает публикацию."""
        contents = [n for n in _full_contents(module) if "locales" not in n]

        assert module.main([str(_wheel(tmp_path / "bad.whl", contents))]) == 1


class TestUnusableInput:
    """Отсутствие колеса — не повод промолчать."""

    def test_empty_archive_is_caught(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Пустой архив — это не «всё на месте»."""
        problems = module.check_wheel(_wheel(tmp_path / "empty.whl", []))

        assert problems and "пуст" in problems[0]

    def test_missing_file_is_caught(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Путь, которого нет: `dist/*.whl` не раскрылся — публиковать нечего."""
        problems = module.check_wheel(tmp_path / "nothing.whl")

        assert problems and "нет" in problems[0]

    def test_no_wheels_given_is_failure(self, module: ModuleType) -> None:
        """Пустой список аргументов не должен читаться как успех."""
        assert module.main(["dist/README.txt"]) == 1

    def test_not_a_zip_is_caught(self, module: ModuleType, tmp_path: pathlib.Path) -> None:
        """Битый файл называется битым, а не пропускается."""
        broken = tmp_path / "broken.whl"
        broken.write_text("это не zip", encoding="utf-8")

        problems = module.check_wheel(broken)

        assert problems and "не читается" in problems[0]
