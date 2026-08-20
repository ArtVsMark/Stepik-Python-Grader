"""Guard'ы e2e-набора проверяются обычным прогоном (issue #921).

Сам e2e-набор исключён из `pytest tests/` (`norecursedirs`) и живёт в отдельном
job'е с браузером. Значит его сторожа — код, который решает, красный прогон или
зелёный, — иначе проверялись бы только там, где и так всё хорошо. Здесь они
разбираются на части и проверяются без браузера и без `playwright`.

Две находки аудита 2026-08-10, ради которых файл появился:

* `QA-2-02` — guard «набор не скипнулся целиком» считал **собранные** тесты.
  Собираются они одинаково, выполнится тест или уйдёт в skip следом, поэтому
  полный пропуск набора оставался зелёным.
* `QA-2-03` — `importorskip` в фикстуре срабатывал раньше, чем guard успевал
  посмотреть на флаг: сломанное окружение пропускало и сами guard'ы, то есть
  ровно те тесты, которые заведены на этот случай.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from tests.e2e import conftest as e2e_conftest
from tests.e2e._helpers import GUARD_FILE, REQUIRE_E2E_ENV, executed_beyond_guards


class _Report:
    """Минимальный `TestReport`: хуку важны только три поля."""

    def __init__(self, nodeid: str, when: str, outcome: str) -> None:
        self.nodeid = nodeid
        self.when = when
        self.outcome = outcome


class _Session:
    """Минимальная `Session`: хук трогает только `exitstatus`."""

    def __init__(self) -> None:
        self.exitstatus = 0


@pytest.fixture
def clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой набор выполненных: состояние модуля глобально на процесс."""
    monkeypatch.setattr(e2e_conftest, "_EXECUTED", set())


class TestExecutedBeyondGuards:
    def test_guard_file_does_not_count(self) -> None:
        """Сторожа выполняются всегда — они не трогают ни браузер, ни сервер."""
        assert executed_beyond_guards({f"tests/e2e/{GUARD_FILE}::test_a"}) == set()

    def test_real_test_counts(self) -> None:
        assert executed_beyond_guards({"tests/e2e/test_journeys.py::test_x"}) == {
            "tests/e2e/test_journeys.py::test_x"
        }

    def test_mixed_input_keeps_only_the_real_ones(self) -> None:
        nodeids = {
            f"tests/e2e/{GUARD_FILE}::test_a",
            f"tests/e2e/{GUARD_FILE}::test_b",
            "tests/e2e/test_router.py::test_c",
        }

        assert executed_beyond_guards(nodeids) == {"tests/e2e/test_router.py::test_c"}


class TestOnlyExecutedTestsAreCounted:
    """Фаза `call` бывает только у теста, который действительно запустился."""

    @pytest.mark.parametrize("outcome", ["passed", "failed"])
    def test_call_phase_counts(self, clean_state: None, outcome: str) -> None:
        e2e_conftest.pytest_runtest_logreport(_Report("tests/e2e/test_x.py::t", "call", outcome))

        assert e2e_conftest._EXECUTED == {"tests/e2e/test_x.py::t"}

    @pytest.mark.parametrize(
        ("when", "outcome"),
        [("call", "skipped"), ("setup", "passed"), ("setup", "failed"), ("teardown", "passed")],
    )
    def test_everything_else_does_not(self, clean_state: None, when: str, outcome: str) -> None:
        """Пропуск и падение в setup — это «не выполнялся», а не «выполнился»."""
        e2e_conftest.pytest_runtest_logreport(_Report("tests/e2e/test_x.py::t", when, outcome))

        assert e2e_conftest._EXECUTED == set()


class TestSessionFinishIsTheRealGuard:
    """Ради этого всё и делалось: пустой прогон под флагом обязан быть красным."""

    def test_all_skipped_becomes_a_failure(
        self, clean_state: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(REQUIRE_E2E_ENV, "1")
        session = _Session()

        e2e_conftest.pytest_sessionfinish(session, 0)

        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        assert "ни один e2e-тест не выполнился" in capsys.readouterr().out

    def test_guards_alone_are_not_enough(
        self, clean_state: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сторожа проходят и при мёртвом наборе — иначе guard сторожил бы себя."""
        monkeypatch.setenv(REQUIRE_E2E_ENV, "1")
        e2e_conftest._EXECUTED.add(f"tests/e2e/{GUARD_FILE}::test_a")
        session = _Session()

        e2e_conftest.pytest_sessionfinish(session, 0)

        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED

    def test_a_live_suite_passes(self, clean_state: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(REQUIRE_E2E_ENV, "1")
        e2e_conftest._EXECUTED.add("tests/e2e/test_journeys.py::test_x")
        session = _Session()

        e2e_conftest.pytest_sessionfinish(session, 0)

        assert session.exitstatus == 0

    def test_without_the_flag_nothing_is_enforced(
        self, clean_state: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Локально без extra прогон обязан оставаться зелёным."""
        monkeypatch.delenv(REQUIRE_E2E_ENV, raising=False)
        session = _Session()

        e2e_conftest.pytest_sessionfinish(session, 0)

        assert session.exitstatus == 0

    def test_existing_failure_is_not_overwritten(
        self, clean_state: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Упавший прогон уже красный — второе объяснение только путало бы."""
        monkeypatch.setenv(REQUIRE_E2E_ENV, "1")
        session = _Session()
        session.exitstatus = pytest.ExitCode.INTERNAL_ERROR

        e2e_conftest.pytest_sessionfinish(session, pytest.ExitCode.INTERNAL_ERROR)

        assert session.exitstatus == pytest.ExitCode.INTERNAL_ERROR
        assert "ни один e2e-тест" not in capsys.readouterr().out


class TestImportIsHardUnderTheFlag:
    """`QA-2-03`: под флагом сломанный playwright — ошибка, а не пропуск."""

    @pytest.fixture
    def fake_playwright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Подставной пакет: настоящий тут не нужен и в этом job'е не стоит."""

        class _Ctx:
            def __enter__(self) -> str:
                return "playwright"

            def __exit__(self, *exc: object) -> bool:
                return False

        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = lambda: _Ctx()  # type: ignore[attr-defined]
        package = types.ModuleType("playwright")
        package.sync_api = sync_api  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "playwright", package)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    def _refuse_importorskip(self, monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
        def _importorskip(name: str, *args: object, **kwargs: object) -> None:
            calls.append(name)

        monkeypatch.setattr(pytest, "importorskip", _importorskip)

    def test_flag_set_means_no_importorskip(
        self, fake_playwright: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Провод: мало решить «жёстко» — надо ещё не позвать `importorskip`."""
        calls: list[str] = []
        self._refuse_importorskip(monkeypatch, calls)
        monkeypatch.setenv(REQUIRE_E2E_ENV, "1")

        loaded = e2e_conftest.load_sync_api()

        assert calls == [], "под флагом пропуск всё ещё возможен"
        assert loaded is sys.modules["playwright.sync_api"]

    def test_without_the_flag_it_is_a_clean_skip(
        self, fake_playwright: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Без флага отсутствующий extra обязан давать пропуск, а не красное."""
        calls: list[str] = []
        module: Any = sys.modules["playwright.sync_api"]

        def _importorskip(name: str, *args: object, **kwargs: object) -> Any:
            calls.append(name)
            return module

        monkeypatch.setattr(pytest, "importorskip", _importorskip)
        monkeypatch.delenv(REQUIRE_E2E_ENV, raising=False)

        assert e2e_conftest.load_sync_api() is module
        assert calls == ["playwright.sync_api"]
