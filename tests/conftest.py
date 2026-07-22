"""tests/conftest.py — общие guard'ы набора тестов (issue #646).

T6: ``[tool.pytest.ini_options] timeout = 120`` действует только при
установленном плагине ``pytest-timeout`` (dev-зависимость, issue #444). Без него
настройка не применяется — зависший тест висит до внешнего kill вместо падения
по дедлайну. Раньше отключение было тихим; теперь отсутствие плагина — громкое
предупреждение на этапе конфигурации pytest.
"""

from __future__ import annotations

import pytest

# pytest-timeout регистрирует pytest11 entry point с именем "timeout"; под ним же
# плагин виден в pluginmanager. Проверяем и каноничное имя модуля — на случай
# иной схемы регистрации в будущих версиях.
_TIMEOUT_PLUGIN_NAMES = ("timeout", "pytest_timeout")


def pytest_configure(config: pytest.Config) -> None:
    """Не дать глобальному timeout молча онеметь без pytest-timeout (issue #646, T6)."""
    if any(config.pluginmanager.hasplugin(name) for name in _TIMEOUT_PLUGIN_NAMES):
        return
    config.issue_config_time_warning(
        pytest.PytestConfigWarning(
            "pytest-timeout не установлен — глобальный timeout из pyproject.toml НЕ "
            "действует (issue #646/#444): зависший тест повесит прогон вместо падения "
            'по дедлайну. Установите dev-зависимости: pip install -e ".[dev]".'
        ),
        stacklevel=2,
    )
