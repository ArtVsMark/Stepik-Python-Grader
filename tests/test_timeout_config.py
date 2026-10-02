"""issue #646 (T6): глобальный per-test timeout реально обеспечен плагином.

Позитивная пара к guard'у в ``conftest.py``: подтверждает, что ``pytest-timeout``
активен и ini-опция ``timeout`` задана. Если кто-то уберёт плагин из
dev-зависимостей или ``timeout`` из ``pyproject.toml`` — тест упадёт, а не молча
снимет дедлайн (именно эту тихую деградацию вскрыл аудит).
"""

from __future__ import annotations

import pytest

_TIMEOUT_PLUGIN_NAMES = ("timeout", "pytest_timeout")


def test_pytest_timeout_plugin_is_active(pytestconfig: pytest.Config) -> None:
    """Плагин, обеспечивающий глобальный дедлайн, зарегистрирован."""
    active = any(pytestconfig.pluginmanager.hasplugin(n) for n in _TIMEOUT_PLUGIN_NAMES)
    assert active, "pytest-timeout должен быть установлен (dev-зависимость, issue #444/#646)"


def test_global_timeout_is_configured(pytestconfig: pytest.Config) -> None:
    """ini-опция timeout задана и непуста — дедлайн действительно применяется."""
    assert pytestconfig.getini("timeout"), "timeout из pyproject.toml должен быть задан и непуст"
