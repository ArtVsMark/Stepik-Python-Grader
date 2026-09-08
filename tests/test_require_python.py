"""Тесты scripts/_require_python.py — точка входа и своя версия Python (issue #1507).

Смысл не в том, что модуль пропускает верный интерпретатор, а в том, что он
**останавливает** неверный и объясняет, что происходит. Замер, из которого он
вырос: системный ``python3`` облачного контейнера — 3.11, ``requires-python``
проекта — ``>=3.12``. ``gh_rest.py`` и ``check_pr_ready.py`` под 3.11
разбирались и работали молча, а ``preflight.py`` падал ``SyntaxError`` на
единственной конструкции PEP 695 — то есть сообщал номер строки вместо причины.

Отдельно закреплено главное свойство самого модуля: он обязан **разбираться
старым интерпретатором**. Сказать «интерпретатор не тот» может только тот, кто
сумел прочитать файл; 3.12-синтаксис здесь превратил бы объяснение в
``SyntaxError`` — ровно в то, от чего модуль и заведён.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "_require_python.py"

#: Точки входа, которые обязаны отказываться под неподходящей версией.
_ENTRY_POINTS = ("preflight.py", "gh_rest.py", "check_pr_ready.py")


@pytest.fixture
def require_python() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_require_python_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(root: pathlib.Path, requires: str | None = '">=3.12"') -> pathlib.Path:
    """Корень проекта с ``pyproject.toml``; ``None`` — файл без требования."""
    line = f"requires-python = {requires}\n" if requires is not None else ""
    (root / "pyproject.toml").write_text(f"[project]\nname = \"x\"\n{line}", encoding="utf-8")
    return root


class TestTheModuleItselfRunsAnywhere:
    """Условие работоспособности, а не стиль."""

    def test_it_parses_on_an_old_interpreter(self) -> None:
        """Файл обязан разбираться грамматикой, которая старше требования.

        Проверяется разбором, а не запуском: тесты идут под своей версией, и
        «у меня импортируется» ничего не говорит о том интерпретаторе, ради
        которого модуль написан. ``feature_version`` просит ast разобрать
        текст правилами 3.9 — если сюда попадёт ``def f[T]`` или ``type X =``,
        тест покраснеет здесь, а не у человека с чужой версией.
        """
        source = _SCRIPT.read_text(encoding="utf-8")

        ast.parse(source, feature_version=(3, 9))

    def test_the_entry_points_parse_too(self) -> None:
        """Ровно то же — у зовущих: отказ печатается ПОСЛЕ разбора файла.

        ``preflight.py`` держал одну конструкцию PEP 695, и её хватало: под
        3.11 он не доходил до собственной проверки и сообщал номер строки.
        """
        for name in _ENTRY_POINTS:
            source = (_SCRIPT.parent / name).read_text(encoding="utf-8")

            ast.parse(source, feature_version=(3, 9))


class TestReadingTheRequirement:
    """Требование читается из проекта, а не хранится копией."""

    def test_the_minimum_comes_from_pyproject(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        assert require_python.minimum_version(_project(tmp_path)) == (3, 12)

    def test_a_raised_minimum_is_picked_up(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Поднимут минимум — отказ поднимется вместе с ним, без правки кода."""
        assert require_python.minimum_version(_project(tmp_path, '">=3.14"')) == (3, 14)

    def test_no_requirement_is_not_an_error(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Нечего сверять — молчим: гейт, краснеющий на пустоте, обходят."""
        assert require_python.minimum_version(_project(tmp_path, None)) is None

    def test_a_missing_file_is_not_an_error(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        assert require_python.minimum_version(tmp_path) is None

    def test_the_real_project_states_its_minimum(self, require_python: ModuleType) -> None:
        """Guard-the-guard: у самого проекта требование на месте и читается."""
        minimum = require_python.minimum_version(pathlib.Path(__file__).parent.parent)

        assert minimum is not None
        assert minimum >= (3, 12)


class TestTheRefusal:
    """Отказ обязан быть отказом, а не предупреждением в потоке вывода."""

    def test_an_older_interpreter_is_stopped(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Версия выше текущей — работа не начинается."""
        higher = f'">={sys.version_info[0]}.{sys.version_info[1] + 1}"'

        with pytest.raises(SystemExit) as stopped:
            require_python.require("гейт", _project(tmp_path, higher))

        assert stopped.value.code == require_python.EXIT_WRONG_PYTHON

    def test_the_message_names_both_versions_and_the_cure(
        self,
        require_python: ModuleType,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Иначе это сообщение про поломку скрипта, а не про среду."""
        higher = f'">={sys.version_info[0]}.{sys.version_info[1] + 1}"'

        with pytest.raises(SystemExit):
            require_python.require("гейт", _project(tmp_path, higher))

        said = capsys.readouterr().err
        assert "гейт" in said
        assert f"{sys.version_info[0]}.{sys.version_info[1]}" in said
        assert "venv" in said

    def test_a_matching_interpreter_passes(
        self, require_python: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Своя версия проходит молча — и ничего не печатает."""
        current = f'">={sys.version_info[0]}.{sys.version_info[1]}"'

        require_python.require("гейт", _project(tmp_path, current))

    def test_the_exit_code_is_its_own(self, require_python: ModuleType) -> None:
        """Не 1 и не 2: вердикта нет вовсе — работа не начиналась.

        Единица означает «проверка не прошла», двойка у ``gh_rest.py`` —
        «ждать, квота исчерпана». Смешать их значило бы ответить на другой
        вопрос: конвейер отличает «занято» от «сломано» по этому числу.
        """
        assert require_python.EXIT_WRONG_PYTHON not in (0, 1, 2)
