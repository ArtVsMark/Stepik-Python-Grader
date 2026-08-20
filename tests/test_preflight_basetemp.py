"""Гейт даёт `pytest` свой корень временных каталогов (issue #1291).

Родня `mypy --platform linux` из #1286: обе истории про то, что гейт был
непроходим на машине владельца при исправном коде — и гейт, который невозможно
пройти, обходят целиком, вместе со всеми остальными проверками.

Здесь причина другая. `pytest` по умолчанию берёт `<tempdir>/pytest-of-<user>`
и **переиспользует** его между прогонами: каталог переживает смену контекста
запуска, и стоит ему один раз достаться другому владельцу, как весь набор
падает ещё на сборе — по `PermissionError` на каждый тест.

Второе требование — короткий путь — выглядит придиркой ровно до первого обхода
через глубокий каталог: там `git push` в тестовых репозиториях перестаёт
создавать объекты, потому что путь не влезает в MAX_PATH.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"


def _load_module() -> ModuleType:
    """Загрузить гейт как модуль: это скрипт, а не пакет."""
    spec = importlib.util.spec_from_file_location("_preflight_basetemp", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate() -> ModuleType:
    """Свежий модуль гейта."""
    return _load_module()


class TestChoice:
    """Какой каталог гейт выбирает и кому уступает."""

    def test_default_is_own_dir_in_tempdir(self, gate: ModuleType) -> None:
        """Свой каталог рядом с системным temp — а не `pytest-of-<user>`."""
        chosen = gate.basetemp_dir(env={})

        assert chosen.name == "grader-preflight"
        assert chosen.parent == pathlib.Path(gate.tempfile.gettempdir())

    def test_env_override_wins(self, gate: ModuleType, tmp_path: pathlib.Path) -> None:
        """У CI и чужих ОС свои порядки — навязывать им наш каталог незачем."""
        assert gate.basetemp_dir(env={"PREFLIGHT_BASETEMP": str(tmp_path)}) == tmp_path

    def test_empty_override_is_not_a_choice(self, gate: ModuleType) -> None:
        """Пустая переменная — не «каталог в корне», а «не задано»."""
        assert gate.basetemp_dir(env={"PREFLIGHT_BASETEMP": ""}).name == "grader-preflight"


class TestFitness:
    """Пригодность каталога проверяется ДО запуска, а не лавиной ошибок сбора."""

    def test_writable_dir_has_no_problem(self, gate: ModuleType, tmp_path: pathlib.Path) -> None:
        assert gate.basetemp_problem(tmp_path / "fresh") is None

    def test_missing_parents_are_created(self, gate: ModuleType, tmp_path: pathlib.Path) -> None:
        """Каталога ещё нет — это не помеха, а обычное первое использование.

        `name="posix"` здесь не для галочки: `tmp_path` сам живёт внутри
        выбранного гейтом каталога, и на Windows проверка длины отвергла бы
        путь раньше, чем тест дошёл бы до своего вопроса — создаются ли
        родители. Про длину есть отдельная пара тестов.
        """
        target = tmp_path / "a" / "b" / "grader-preflight"

        assert gate.basetemp_problem(target, name="posix") is None
        assert target.is_dir()

    def test_long_path_is_refused_on_windows(self, gate: ModuleType) -> None:
        """Глубокий путь ломает git в тестовых репозиториях — 45 ошибок из #1291."""
        deep = pathlib.Path("C:/") / ("x" * 120)

        problem = gate.basetemp_problem(deep, name="nt")

        assert problem is not None
        assert "MAX_PATH" in problem

    def test_long_path_is_fine_elsewhere(self, gate: ModuleType, tmp_path: pathlib.Path) -> None:
        """Ограничение — свойство Windows, а не наша выдумка про аккуратность."""
        deep = tmp_path / ("x" * 120)

        assert gate.basetemp_problem(deep, name="posix") is None

    def test_unusable_dir_names_the_reason(
        self, gate: ModuleType, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Причина называется словами: иначе её выкапывают из тысяч строк."""

        def deny(*args: object, **kwargs: object) -> None:
            raise PermissionError(13, "Отказано в доступе")

        monkeypatch.setattr(pathlib.Path, "mkdir", deny)

        problem = gate.basetemp_problem(tmp_path / "denied")

        assert problem is not None
        assert "PermissionError" in problem


class TestCommand:
    """Флаг обязан доехать до самого прогона, иначе всё выше — украшение."""

    def test_command_carries_basetemp(self, gate: ModuleType, tmp_path: pathlib.Path) -> None:
        command = gate._pytest_command(tmp_path)

        assert command[-2:] == ["--basetemp", str(tmp_path)]
        assert "tests/" in command

    def test_unfit_dir_fails_the_step_without_running_pytest(
        self, gate: ModuleType, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Непригодный каталог — красный шаг с объяснением, а не запуск вслепую."""
        monkeypatch.setattr(gate, "basetemp_problem", lambda *a, **k: "PermissionError: 13")
        monkeypatch.setattr(
            gate, "_run_stage", lambda *a, **k: pytest.fail("pytest не должен запускаться")
        )

        check = gate._pytest_check(tmp_path)

        assert not check.ok
        assert "непригоден" in check.detail
        assert "PREFLIGHT_BASETEMP" in (check.hint or "")
