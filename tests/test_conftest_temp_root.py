"""Системный temp на время прогона живёт внутри basetemp (issue #1171).

Guard `_no_writes_outside_tmp` называет виновником того, в чьём teardown заметил
изменение, — и потому короткоживущий каталог, созданный продуктом в ОБЩЕМ temp,
обвиняет посторонний тест. Здесь проверяется лечение класса: общего temp на
время прогона нет, любой код без явного ``dir=`` пишет внутрь pytest-basetemp.

Главный тест — последний: он гоняет настоящую «Песочницу» и спрашивает у самого
запущенного кода, где он лежит. Чтение исходника такого не докажет.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

from stepik_grader.web import playground


@pytest.fixture
def basetemp(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Корень временных каталогов прогона, приведённый к канону.

    ``resolve()`` обязателен: на macOS ``gettempdir()`` отдаёт ``/var/...``,
    который на деле ``/private/var/...``, и сравнение путей без него ложно.
    """
    return tmp_path_factory.getbasetemp().resolve()


def test_gettempdir_points_inside_basetemp(basetemp: pathlib.Path) -> None:
    assert pathlib.Path(tempfile.gettempdir()).resolve().is_relative_to(basetemp)


def test_mkdtemp_without_dir_stays_inside_basetemp(basetemp: pathlib.Path) -> None:
    created = pathlib.Path(tempfile.mkdtemp(prefix="проверка-"))

    assert created.resolve().is_relative_to(basetemp)


def test_named_temporary_file_stays_inside_basetemp(basetemp: pathlib.Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".py") as handle:
        assert pathlib.Path(handle.name).resolve().is_relative_to(basetemp)


def test_subprocess_inherits_the_same_temp(basetemp: pathlib.Path) -> None:
    """Подпроцесс видит только окружение — грейдер в тестах запускается им."""
    completed = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert pathlib.Path(completed.stdout.strip()).resolve().is_relative_to(basetemp)


def test_environment_names_the_same_place_on_every_os() -> None:
    """POSIX читает TMPDIR, Windows — TEMP/TMP: разойтись они не должны."""
    values = {os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")}

    assert len(values) == 1


def test_playground_workdir_is_not_in_the_shared_temp(basetemp: pathlib.Path) -> None:
    """Тот самый источник шума: приватный каталог «Песочницы» (#799).

    Спрашиваем у запущенного кода, где лежит его файл, — то есть проверяем
    прогоном той поверхности, на которой дефект и наблюдался.
    """
    result = playground.run_playground("import pathlib\nprint(pathlib.Path(__file__).parent)")

    assert result["status"] == "OK"
    workdir = pathlib.Path(result["stdout"].strip())
    assert workdir.name.startswith("stepik-playground-")
    assert workdir.resolve().is_relative_to(basetemp)
