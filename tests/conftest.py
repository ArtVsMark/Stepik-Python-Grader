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


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    """Ни один тест не пишет в РЕАЛЬНУЮ базу истории пользователя (issue #818).

    Прецедент, стоивший данных: с единой пользовательской базой
    (``~/.stepik-grader/history.db``) прогон набора начал складывать туда свои
    записи — а тесты ``--purge-history`` удалили ``~/.grader_history.db``
    разработчика. Пока база лежала в cwd, тесты попадали в неё «естественно»,
    через ``monkeypatch.chdir(tmp_path)``; после смены дефолта такой изоляции
    стало недостаточно.

    Переменная окружения перекрывает и конфиг, и авторезолв, поэтому даже тест,
    запускающий грейдер ПОДПРОЦЕССОМ, не дотянется до домашней папки. Тесты,
    которым нужен конкретный путь, передают ``db_path`` явно или снимают
    переменную сами.
    """
    monkeypatch.setenv(
        "STEPIK_GRADER_HISTORY_DB",
        str(tmp_path_factory.mktemp("history-isolated") / "history.db"),
    )


@pytest.fixture(autouse=True)
def _never_open_a_real_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни один тест не открывает окно браузера на машине разработчика.

    Прецедент: тест `/api/auth/start` проверял только код ответа 202, полагая,
    что job «просто встанет в очередь». Воркер подхватывает его сразу, поэтому
    прогон открывал настоящую страницу авторизации Stepik и вставал на 120 с в
    ожидании OAuth-кода — заодно занимая единственный слот пула, отчего сыпались
    таймаутами все последующие job-тесты.

    Тесты, которым нужен сам факт вызова, патчат `webbrowser.open` сами — их
    патч ставится позже и перекрывает эту заглушку.
    """
    monkeypatch.setattr("webbrowser.open", lambda *_a, **_k: False)
