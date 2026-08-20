"""conftest.py -- fixtures for the Playwright e2e smoke suite (issue #263).

Not part of the default ``pytest``/``pytest tests/`` sweep (see
``norecursedirs`` in ``pyproject.toml``) -- these tests drive a *real*
``--serve`` HTTP server with a headless Chromium browser, which needs the
opt-in ``e2e`` extra (``pip install -e ".[e2e]"``) plus
``playwright install chromium``. Run explicitly: ``pytest tests/e2e/``.

Deliberately uses the plain ``playwright.sync_api`` (no ``pytest-playwright``
plugin dependency -- the issue only authorizes ``playwright`` itself as a new
dev-extra) -- one session-scoped browser, one fresh context/page per test for
isolation (separate ``localStorage``/theme/etc. per test).

``pytest.importorskip("playwright.sync_api")`` deliberately lives *inside*
the ``playwright_instance`` fixture, not at module import time: importing an
absent module at conftest-collection time turns into a hard collection
*error* (breaks even `pytest --collect-only`), whereas importing lazily
inside a fixture body turns into a clean per-test *skip* when the ``e2e``
extra isn't installed.

The server fixture mirrors ``tests/test_web.py``'s ``server``/
``server_factory`` fixtures (``_GraderServer`` in a daemon thread on an
ephemeral port), just with a real browser navigating to it instead of
``urllib``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from stepik_grader.core import user_settings
from stepik_grader.web import server as web_server

from ._helpers import REQUIRE_E2E_ENV, executed_beyond_guards

__all__: list[str] = []

_EXECUTED: set[str] = set()
"""nodeid'ы тестов, у которых реально выполнилось тело (не setup, не пропуск)."""


def _guard_enabled() -> bool:
    """Жёсткий режим: пропуск набора обязан стать отказом (job ``e2e`` в CI)."""
    return bool(os.environ.get(REQUIRE_E2E_ENV))


def load_sync_api() -> Any:
    """``playwright.sync_api``: жёстко под флагом, мягким пропуском без него.

    Без ``STEPIK_REQUIRE_E2E_TESTS`` отсутствие пакета — чистый пропуск: extra
    опциональный (``pip install -e ".[e2e]"``), и разработчик без него не должен
    получать красный прогон.

    С переменной импорт **жёсткий** (issue #921, находка `QA-2-03`). Прежде
    ``importorskip`` стоял безусловно, и сломанное окружение пропускало заодно
    сами guard'ы — те, что заведены ровно для этого случая: фикстура
    отрабатывает раньше, чем тело теста успевает проверить флаг, поэтому
    «playwright сломан» выглядело как «guard пропущен», то есть как норма.

    Отдельной функцией, а не строкой в фикстуре: фикстуру нельзя вызвать
    напрямую, а решение о жёсткости обязано проверяться в обычном прогоне —
    в том, где браузера нет.
    """
    if _guard_enabled():
        import playwright.sync_api as sync_api

        return sync_api
    return pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Any]:
    """One ``sync_playwright()`` context for the whole test session."""
    with load_sync_api().sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance: Any) -> Iterator[Any]:
    """One headless Chromium instance, shared across tests (issue #263).

    Honors ``PLAYWRIGHT_EXECUTABLE_PATH`` if set — lets a runner point at an
    already-installed Chromium (e.g. a pre-provisioned image) instead of
    ``playwright install``'s bundled build. Unset → default bundled browser
    (backward-compatible, ``executable_path=None``).
    """
    browser = playwright_instance.chromium.launch(
        executable_path=os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH") or None,
    )
    yield browser
    browser.close()


@pytest.fixture
def page(browser: Any) -> Iterator[Any]:
    """A fresh browser context + page per test -- isolated ``localStorage``.

    Плюс (issue #804): непойманное исключение в странице роняет тест. Раньше
    оно было невидимым — падал только косвенный симптом (раздел не открылся), а
    первопричину (``TypeError`` в обработчике) приходилось искать вручную.
    Тест, которому ошибка нужна по замыслу, глушит её ``page_errors.clear()``.
    """
    context = browser.new_context()
    pg = context.new_page()
    errors: list[str] = []
    # `stack` есть у Error из страницы — без него в отчёте остаётся один текст
    # сообщения, а искать место приходится вручную.
    pg.on("pageerror", lambda exc: errors.append(getattr(exc, "stack", None) or str(exc)))
    pg.page_errors = errors  # доступ из теста: разрешить ожидаемую ошибку
    yield pg
    context.close()
    assert not errors, "непойманные ошибки JS на странице:\n" + "\n".join(errors)


@pytest.fixture
def e2e_server(tmp_path: Path) -> Iterator[str]:
    """Real ``_GraderServer`` on 127.0.0.1:<ephemeral>, workspace=``tmp_path``.

    Same pattern as ``tests/test_web.py``'s ``server_factory`` -- a daemon
    thread running ``serve_forever()``, torn down at test end.
    """
    # issue #660: гасим стартовый экран-онбординг для journey-тестов — его
    # модалка-оверлей иначе перехватывает все клики Playwright (модалка модальна).
    # Сам онбординг покрыт unit/web-тестами и ручной браузер-проверкой; journey
    # проверяют основные потоки, где онбординг только мешал бы.
    user_settings.save_settings(
        user_settings.UserSettings(onboarding_seen=True),
        user_settings.default_settings_path(tmp_path),
    )
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0), web_server._Handler, workspace=tmp_path, confine=True
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Запомнить тесты, у которых выполнилось ТЕЛО (issue #921, `QA-2-02`).

    Фаза ``call`` бывает только у теста, который действительно запустился:
    пропуск и падение в setup сюда не попадают. Именно эта разница и была
    дефектом — прежний guard считал СОБРАННЫЕ элементы, а собираются они и
    когда каждый следом пропускается.
    """
    if report.when == "call" and report.outcome in {"passed", "failed"}:
        _EXECUTED.add(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Под ``STEPIK_REQUIRE_E2E_TESTS`` пустой прогон обязан быть красным.

    Проверка живёт в конце сессии, а не в тесте: только здесь известны исходы
    ВСЕХ тестов. Тест, стоящий в середине прогона, о своих соседях знает лишь
    то, что они собраны.

    Единственный сценарий, который сюда не достаёт, — не загруженный
    ``conftest.py`` (переименовали каталог, ошиблись путём). Он закрыт самим
    pytest: «ничего не собрано» — код возврата 5, «путь не найден» — 4, и job
    краснеет без нашего участия.
    """
    if not _guard_enabled() or exitstatus != 0:
        return
    if executed_beyond_guards(_EXECUTED):
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    print(
        f"\nFAIL ({REQUIRE_E2E_ENV}=1): ни один e2e-тест не выполнился — "
        "собрать набор мало, он весь пропущен. Проверьте установку extra "
        '"e2e" и браузер (playwright install chromium).'
    )
