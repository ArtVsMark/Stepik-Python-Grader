"""Дедлайн теста растёт вместе с объявленным порогом спавна (issue #1278).

Класс падений тот же, что у #1232: на `macos-latest × 3.14` порождение
подпроцесса занимает десятки секунд. Порог для НАШЕГО сторожа поднимает
переменная `STEPIK_GRADER_LAUNCH_TIMEOUT_S`, но тест, зовущий `subprocess.run`
напрямую (`tests/test_preflight.py::_git`), до сторожа не доходит: первым
стреляет глобальный дедлайн pytest-timeout, который про медленный раннер ничего
не знает. Окружение уже объявило «спавн здесь медленный» — объявление обязано
доезжать до того лимита, который реально срабатывает.

Чего здесь намеренно нет: «глушить тест по сигнатуре ошибки». Так замаскируется
настоящая поломка спавна, которую он и должен ловить, — та же оговорка, что в
докстринге `tests/test_launch_timeout_override.py`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types
from types import ModuleType

import pytest

_CONFTEST = pathlib.Path(__file__).parent.parent / "conftest.py"


def _load_conftest() -> ModuleType:
    """Корневой conftest как модуль — хук проверяется вызовом, а не прогоном."""
    spec = importlib.util.spec_from_file_location("_root_conftest", _CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(timeout: float | None = 120) -> types.SimpleNamespace:
    """Минимальный объект конфига: хук трогает только `option.timeout`."""
    return types.SimpleNamespace(option=types.SimpleNamespace(timeout=timeout))


@pytest.fixture
def conftest() -> ModuleType:
    return _load_conftest()


def test_default_timeout_is_untouched(
    conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без переменной ничего не меняется — 120 с остаются 120 с."""
    monkeypatch.delenv("STEPIK_GRADER_LAUNCH_TIMEOUT_S", raising=False)
    config = _config()

    conftest.pytest_configure(config)

    assert config.option.timeout == 120


def test_slow_runner_scales_the_deadline(
    conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Объявленный втрое больший порог спавна даёт втрое больший дедлайн теста."""
    from stepik_grader.core.spawn import DEFAULT_LAUNCH_TIMEOUT_S

    monkeypatch.setenv("STEPIK_GRADER_LAUNCH_TIMEOUT_S", str(DEFAULT_LAUNCH_TIMEOUT_S * 3))
    config = _config()

    conftest.pytest_configure(config)

    assert config.option.timeout == pytest.approx(360)


def test_lower_threshold_does_not_shrink_the_deadline(
    conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порог МЕНЬШЕ дефолта дедлайн не режет: правка умеет только поднимать.

    Иначе переменная, выставленная ради быстрой машины, обрезала бы время
    тестам, которые к спавну отношения не имеют.
    """
    monkeypatch.setenv("STEPIK_GRADER_LAUNCH_TIMEOUT_S", "1")
    config = _config()

    conftest.pytest_configure(config)

    assert config.option.timeout == 120


def test_garbage_value_changes_nothing(
    conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мусор в переменной — это «не задано», а не повод падать на старте набора."""
    monkeypatch.setenv("STEPIK_GRADER_LAUNCH_TIMEOUT_S", "медленно")
    config = _config()

    conftest.pytest_configure(config)

    assert config.option.timeout == 120


def test_missing_timeout_option_is_survivable(
    conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без pytest-timeout поднимать нечего — хук молчит, а не бросает."""
    monkeypatch.setenv("STEPIK_GRADER_LAUNCH_TIMEOUT_S", "60")
    config = _config(timeout=None)

    conftest.pytest_configure(config)

    assert config.option.timeout is None
