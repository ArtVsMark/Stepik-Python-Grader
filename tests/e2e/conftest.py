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

from stepik_grader import web

__all__: list[str] = []


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Any]:
    """One ``sync_playwright()`` context for the whole test session.

    Skips the whole e2e suite (instead of erroring) if ``playwright`` isn't
    installed -- it's an opt-in dev-extra (``pip install -e ".[e2e]"``).
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
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
    """A fresh browser context + page per test -- isolated ``localStorage``."""
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def e2e_server(tmp_path: Path) -> Iterator[str]:
    """Real ``_GraderServer`` on 127.0.0.1:<ephemeral>, workspace=``tmp_path``.

    Same pattern as ``tests/test_web.py``'s ``server_factory`` -- a daemon
    thread running ``serve_forever()``, torn down at test end.
    """
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
