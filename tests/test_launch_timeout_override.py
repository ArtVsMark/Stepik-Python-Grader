"""Порог ожидания запуска подпроцесса поднимается для медленного раннера (#1232).

Класс падений «macOS + Python 3.14: спавн подпроцесса»: три разных теста
падали по одной причине — порождение процесса на этом раннере занимает десятки
секунд. Показателен последний случай: `test_local_runner_times_out` проверяет
**таймаут исполнения**, а до проверки дело не доходило вовсе — не стартовал сам
интерпретатор, и вместо `timed_out=True` возвращался `launch_error`.

Решение — порог, а не исключение комбинации из матрицы: экспериментальная 3.14
обещана `requires-python` и полезна, пока её падения о чём-то говорят. Когда
они каждый раз про медленный раннер, это шум, в котором утонет настоящая
регрессия.

Решение — переменная окружения, а не новый дефолт: двадцать секунд отличают
«подвисло навсегда» от «система под нагрузкой» на машине пользователя, и
поднятый дефолт заставил бы каждого ждать полторы минуты на настоящем зависании.

Чего здесь намеренно нет — «научить тест пропускаться по сигнатуре ошибки». Так
замаскировалась бы реальная поломка спавна, которую тест и должен ловить.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import threading
import time
from collections.abc import Callable
from types import ModuleType

import pytest

from stepik_grader.core import spawn

_PREFLIGHT = pathlib.Path(__file__).parent.parent / "scripts" / "preflight.py"
_CI_YML = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _blocking(release: threading.Event) -> Callable[..., object]:
    """Подмена запуска, которая висит, пока её не отпустят.

    Ждём с собственным потолком: если тест упадёт до `release.set()`, поток
    всё равно завершится и не переживёт прогон.
    """

    class _Started:
        """Возвращаемое значение с `kill()`: `guarded_popen` добивает опоздавшего."""

        def kill(self) -> None:
            return None

    def _launch(*_args: object, **_kwargs: object) -> object:
        release.wait(30)
        return _Started()

    return _launch


@pytest.fixture
def preflight() -> ModuleType:
    """Скрипт гейта как модуль — он намеренно не импортируется из пакета."""
    spec = importlib.util.spec_from_file_location("_preflight_timeout", _PREFLIGHT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestCoreThreshold:
    def test_default_without_the_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(spawn.ENV_LAUNCH_TIMEOUT, raising=False)

        assert spawn.launch_timeout_s() == spawn.DEFAULT_LAUNCH_TIMEOUT_S

    def test_variable_raises_the_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "90")

        assert spawn.launch_timeout_s() == 90.0

    def test_read_at_call_time_not_at_import(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CI ставит переменную job'у, а модуль к тому моменту уже загружен."""
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "45")
        first = spawn.launch_timeout_s()
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "60")

        assert (first, spawn.launch_timeout_s()) == (45.0, 60.0)

    @pytest.mark.parametrize("junk", ["", "   ", "быстро", "0", "-5", "nan-ish"])
    def test_junk_means_not_set(self, monkeypatch: pytest.MonkeyPatch, junk: str) -> None:
        """Гейт, падающий из-за опечатки в конфиге раннера, хуже отсутствующего.

        Пустая строка сюда входит не случайно: матрица подставляет именно её
        неэкспериментальным job'ам, то есть «переменная задана, значения нет».
        """
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, junk)

        assert spawn.launch_timeout_s() == spawn.DEFAULT_LAUNCH_TIMEOUT_S

    @pytest.mark.parametrize("entry_point", ["guarded_run", "guarded_popen"])
    def test_variable_actually_reaches_the_launch(
        self, monkeypatch: pytest.MonkeyPatch, entry_point: str
    ) -> None:
        """Значение обязано доезжать до запуска, а не только считываться.

        Запуск подменяется заведомо медленным, дедлайн из переменной — заведомо
        коротким. Сообщение об ошибке называет сработавший дедлайн, поэтому
        видно не только «сорвалось», но и **чьё** значение сорвало: 0.05 из
        переменной, а не дефолтные двадцать секунд.

        Настоящий подпроцесс с микроскопическим дедлайном тут не годится —
        такой тест гоночный: рабочий поток успевает закончить раньше, чем
        основной входит в ожидание, и результат зависит от планировщика.
        Поймано прямо здесь: `guarded_popen` проходил, `guarded_run` падал.
        """
        release = threading.Event()
        monkeypatch.setattr(spawn.subprocess, "Popen", _blocking(release))
        monkeypatch.setattr(spawn.subprocess, "run", _blocking(release))
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "0.05")

        try:
            with pytest.raises(spawn.SpawnTimeout, match="0.05"):
                getattr(spawn, entry_point)(["медленный-запуск"])
        finally:
            release.set()

    def test_healthy_launch_survives_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Контроль: без переменной настоящий запуск проходит целиком."""
        monkeypatch.delenv(spawn.ENV_LAUNCH_TIMEOUT, raising=False)

        assert spawn.guarded_run([sys.executable, "-c", "pass"]).returncode == 0

    def test_explicit_argument_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Переменная — аварийный подъём, а не подмена явного намерения кода."""
        release = threading.Event()
        monkeypatch.setattr(spawn.subprocess, "Popen", _blocking(release))
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "90")

        try:
            with pytest.raises(spawn.SpawnTimeout, match="0.05"):
                spawn.guarded_popen(["медленный-запуск"], launch_timeout=0.05)
        finally:
            release.set()


class TestPreflightThreshold:
    """Гейт дублирует приём намеренно — значит и переменную обязан читать ту же."""

    def test_same_variable_name(self, preflight: ModuleType) -> None:
        assert preflight._ENV_LAUNCH_TIMEOUT == spawn.ENV_LAUNCH_TIMEOUT

    def test_default_without_the_variable(
        self, preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(spawn.ENV_LAUNCH_TIMEOUT, raising=False)

        assert preflight._git_launch_timeout_s() == preflight._GIT_LAUNCH_TIMEOUT_S

    def test_variable_raises_the_threshold(
        self, preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "90")

        assert preflight._git_launch_timeout_s() == 90.0

    @pytest.mark.parametrize("junk", ["", "быстро", "0"])
    def test_junk_means_not_set(
        self, preflight: ModuleType, monkeypatch: pytest.MonkeyPatch, junk: str
    ) -> None:
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, junk)

        assert preflight._git_launch_timeout_s() == preflight._GIT_LAUNCH_TIMEOUT_S

    def test_variable_actually_reaches_the_guard(
        self, preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Тот же провод, что у ядра: значение обязано доезжать до ожидания.

        `_run_guarded` отдаёт `None`, когда вызов не уложился в дедлайн. Ставим
        короткий дедлайн из переменной и заведомо медленный вызов: при дефолтных
        двадцати секундах тест ждал бы их и вернул результат.
        """
        release = threading.Event()
        monkeypatch.setenv(spawn.ENV_LAUNCH_TIMEOUT, "0.05")
        started = time.monotonic()

        try:
            assert preflight._run_guarded(_blocking(release)) is None
        finally:
            release.set()

        assert time.monotonic() - started < 5, "ждали дефолт, а не значение из переменной"

    def test_gate_does_not_import_the_package(self) -> None:
        """Гейт обязан работать, когда проверяемый пакет не установлен или сломан."""
        source = _PREFLIGHT.read_text(encoding="utf-8")

        assert "from stepik_grader" not in source
        assert "import stepik_grader" not in source


class TestCiRaisesItOnlyForExperimental:
    def test_variable_is_set_in_the_test_job(self) -> None:
        text = _CI_YML.read_text(encoding="utf-8")

        assert spawn.ENV_LAUNCH_TIMEOUT in text

    def test_stable_combinations_keep_the_default(self) -> None:
        """Иначе подъём порога тихо распространился бы на весь проект.

        Выражение отдаёт значение только при `matrix.experimental`; остальным
        достаётся пустая строка, а она читается как «не задано».
        """
        text = _CI_YML.read_text(encoding="utf-8")
        line = re.search(rf"^\s*{spawn.ENV_LAUNCH_TIMEOUT}:\s*(.+)$", text, re.MULTILINE)

        assert line, "переменная не задана в ci.yml"
        assert "matrix.experimental" in line.group(1)
