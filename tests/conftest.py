"""tests/conftest.py — общие guard'ы набора тестов (issue #646).

T6: ``[tool.pytest.ini_options] timeout = 120`` действует только при
установленном плагине ``pytest-timeout`` (dev-зависимость, issue #444). Без него
настройка не применяется — зависший тест висит до внешнего kill вместо падения
по дедлайну. Раньше отключение было тихим; теперь отсутствие плагина — громкое
предупреждение на этапе конфигурации pytest.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from stepik_grader.core import user_settings

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


# Артефакты самого прогона, а не тестов: pytest/coverage/hypothesis/линтеры
# пишут их в корень репозитория и делают это законно. Префиксом — потому что
# `coverage` в параллельном режиме кладёт `.coverage.<host>.<pid>.<rand>`.
_RUN_ARTEFACT_NAMES = frozenset(
    {".pytest_cache", ".hypothesis", ".mypy_cache", ".ruff_cache", "htmlcov", "coverage.xml"}
)
_RUN_ARTEFACT_PREFIXES = (".coverage",)

# Файлы соседних инструментов, которые работают на машине разработчика
# ОДНОВРЕМЕННО с прогоном и трогают собственное состояние в домашнем каталоге.
# К тестам они отношения не имеют, но снимок guard'а их видит и обвиняет того,
# в чьём teardown заметил. Прецедент: полный прогон дал 14 ложных обвинений
# подряд — все из-за файлов `~/.claude.json*` (Claude Code), при том что сам
# прогон был зелёным.
#
# Именно ПРЕФИКС, а не точные имена: инструмент пишет настройки атомарно, и
# рядом с `.claude.json` живут `.claude.json.lock` и `.claude.json.tmp.<pid>.
# <hash>` — список точных имён отсеял бы первые два и продолжил обвинять на
# третьем. При этом «игнорировать всё в доме» вернуло бы ровно тот дефект,
# ради которого guard написан (#818 — удалённая `~/.grader_history.db`).
_FOREIGN_TOOL_PREFIXES = (".claude.json",)


def _is_run_artefact(name: str) -> bool:
    """Запись создана инструментом (прогона или соседним), а не тестом."""
    return (
        name in _RUN_ARTEFACT_NAMES
        or name.startswith(_RUN_ARTEFACT_PREFIXES)
        or name.startswith(_FOREIGN_TOOL_PREFIXES)
    )


def _top_level_names(place: Path) -> set[str]:
    """Имена записей ВЕРХНЕГО уровня каталога; недоступный каталог → пустое множество.

    Только верхний уровень и только имена: обход в глубину пришлось бы делать
    после каждого теста, а прецеденты загрязнения — ``C:\\some\\dir``,
    ``~/.stepik-grader/``, ``~/.grader_history.db``, ``.grader_cache/`` в корне
    репозитория — все видны сразу, новой записью верхнего уровня.
    """
    try:
        with os.scandir(place) as entries:
            return {entry.name for entry in entries}
    except OSError:
        return set()


def _tolerated_names(place: Path, protected: Iterable[Path]) -> set[str]:
    """Имена внутри ``place``, ведущие к легальным для записи корням.

    ``--basetemp`` и ``TMPDIR`` разработчик вправе направить куда угодно, в том
    числе внутрь репозитория или домашней папки (на этой машине системный
    ``%TEMP%`` недоступен из песочницы инструментов, и ``--basetemp`` —
    единственный способ прогнать набор). Запись, ведущая в такой корень, — не
    загрязнение.
    """
    names: set[str] = set()
    for root in protected:
        try:
            relative = root.resolve().relative_to(place)
        except ValueError:
            continue
        if relative.parts:
            names.add(relative.parts[0])
    return names


def _guarded_places(config: pytest.Config) -> list[Path]:
    """Каталоги, за появлением новых записей в которых следит guard.

    Корень репозитория и рабочая директория ловят ``.grader_cache/``,
    ``.grader_settings.json`` и прочие следы прогона грейдера без ``chdir``;
    домашняя папка — пользовательские базы (``~/.stepik-grader/``); корень
    диска — выдуманный абсолютный путь, который на Windows превращается из
    ``/some/dir`` в ``C:\\some\\dir``.
    """
    places = [Path(config.rootpath), Path.cwd()]
    with contextlib.suppress(RuntimeError):  # домашняя папка может быть не определена
        places.append(Path.home())
    places.append(Path(Path(config.rootpath).anchor))
    unique: dict[Path, None] = {}
    for place in places:
        resolved = place.resolve()
        if resolved.is_dir():
            unique[resolved] = None
    return list(unique)


@pytest.fixture(scope="session")
def _known_filesystem_entries(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> dict[Path, set[str]]:
    """Слепок опасных мест на старте прогона: ``{каталог: имена верхнего уровня}``."""
    protected = (tmp_path_factory.getbasetemp(), Path(tempfile.gettempdir()))
    return {
        place: _top_level_names(place) | _tolerated_names(place, protected)
        for place in _guarded_places(request.config)
    }


@pytest.fixture(autouse=True)
def _no_writes_outside_tmp(
    request: pytest.FixtureRequest, _known_filesystem_entries: dict[Path, set[str]]
) -> Iterator[None]:
    """Ни один тест не трогает РЕАЛЬНУЮ файловую систему за пределами tmp_path.

    Прецедент: тест ``--serve`` передавал в ``cli.main`` выдуманный путь
    ``--root /some/dir``. Пока ``--root`` задавал только корень раздачи, это
    было безобидно; когда он стал ещё и корнем настроек, резолвер начал читать
    и писать по этому пути — на диске появился ``C:\\some\\dir`` с
    ``.grader_settings.json``. Через несколько недель этот файл сломал сам тест:
    ``record_history`` резолвился в ``False`` вместо ожидаемого ``True``, причём
    ТОЛЬКО на машине, где каталог успел появиться, — в CI тест был зелёным.

    Исчезнувшие записи проверяются наравне с появившимися, и по более дорогому
    прецеденту (issue #818): тесты ``--purge-history`` удалили настоящую
    ``~/.grader_history.db`` разработчика. Появление файла стоит недоумения,
    удаление — данных.

    Guard объявляет находку сразу и с именем виновника, вместо того чтобы ждать,
    пока она вернётся падением в другом месте через месяц. Сам он ничего не
    удаляет и не восстанавливает: файл может оказаться чужим, а решение о судьбе
    постороннего файла на диске разработчика — не за набором тестов.

    Определена ПЕРВОЙ из autouse-фикстур намеренно: финализаторы отрабатывают в
    обратном порядке, поэтому проверка идёт последней — после того как соседние
    фикстуры откатили свои подмены.
    """
    yield
    created: list[str] = []
    removed: list[str] = []
    for place, known in _known_filesystem_entries.items():
        now = _top_level_names(place)
        appeared = {name for name in now - known if not _is_run_artefact(name)}
        vanished = {name for name in known - now if not _is_run_artefact(name)}
        # Приводим слепок к факту: иначе один тест-виновник уронит и все следующие.
        known |= appeared
        known -= vanished
        created.extend(str(place / name) for name in sorted(appeared))
        removed.extend(str(place / name) for name in sorted(vanished))
    if created or removed:
        found = "; ".join(
            part
            for part in (
                f"создано: {', '.join(created)}" if created else "",
                f"удалено: {', '.join(removed)}" if removed else "",
            )
            if part
        )
        pytest.fail(
            f"{request.node.nodeid} тронул файловую систему вне tmp_path — {found}. "
            "Тесты работают только в tmp_path/tmp_path_factory: выдуманный абсолютный "
            "путь в аргументах адресует настоящий диск разработчика, и следующий "
            "прогон читает оттуда чужие настройки (а удаление уносит его данные). "
            "Если запись тронул посторонний процесс, а не тест, добавьте её в "
            "_RUN_ARTEFACT_NAMES."
        )


@pytest.fixture(scope="session", autouse=True)
def _history_db_safety_net(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Изоляция истории держится и МЕЖДУ тестами, а не только внутри них (#1169).

    Изоляция ниже — per-test, и её ``monkeypatch`` откатывается на границе
    теста. В окне между откатом одного теста и установкой следующего
    ``STEPIK_GRADER_HISTORY_DB`` пуста, и ``default_history_db_path()``
    резолвится в НАСТОЯЩУЮ ``~/.stepik-grader/history.db``. Тест этого окна не
    видит — а поток видит: web-слой гоняет проверки в пуле
    (``web/runs.py``), и поток, переживший свой тест, попадает ровно туда.

    Так падал guard ``_no_filesystem_pollution``: «создано
    ``/Users/runner/.stepik-grader``» — причём каждый раз в другом тесте
    ``test_runs.py`` и только на медленных раннерах (macOS/Windows), где поток
    не успевает закончить до конца теста. На Linux окно закрывалось раньше, чем
    поток до него добирался.

    Страховка ставится на всю сессию, поэтому откат per-test изоляции
    возвращает не пустоту, а тоже путь в ``tmp``. Свою базу на тест это не
    отменяет: значение ниже ложится поверх и действует, пока тест идёт.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(
            "STEPIK_GRADER_HISTORY_DB",
            str(tmp_path_factory.mktemp("history-safety-net") / "history.db"),
        )
        yield


@pytest.fixture(scope="session", autouse=True)
def _user_settings_safety_net(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """То же для файла настроек: изоляция держится и МЕЖДУ тестами (issue #948).

    Настройки стали общими (``~/.stepik-grader/settings.json``), то есть
    попадают в ту же категорию, что и база истории: без подмены прогон набора
    писал бы в домашнюю папку разработчика. Причина отдельной сессионной
    страховки — ровно та же, что у истории (#1169): per-test изоляция
    откатывается на границе теста, а поток веб-пула этот момент застаёт.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(
            user_settings.SETTINGS_ENV_VAR,
            str(tmp_path_factory.mktemp("settings-safety-net") / "settings.json"),
        )
        yield


@pytest.fixture(autouse=True)
def _isolate_user_settings(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    """Свой файл настроек на каждый тест (issue #948).

    Переменная окружения, а не подмена функции: её видит и грейдер, запущенный
    подпроцессом, — тот же приём, что у ``STEPIK_GRADER_HISTORY_DB``. Тесты,
    проверяющие сам резолв пути, снимают переменную сами
    (``monkeypatch.delenv``).
    """
    monkeypatch.setenv(
        user_settings.SETTINGS_ENV_VAR,
        str(tmp_path_factory.mktemp("settings-isolated") / "settings.json"),
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


def _loaded_config_modules() -> list[ModuleType]:
    """Все живые экземпляры ``stepik_grader.config`` (обычно один).

    Второй появляется после ``test_bare_import_does_not_read_pyproject_toml``:
    он переимпортирует модуль, и в ``sys.modules`` встаёт НОВЫЙ объект, тогда
    как ``cli``/``web`` держат ссылку на прежний. Сбрасывать нужно оба, иначе
    переопределение останется жить в том, который видит код под тестом.
    """
    candidates = [
        sys.modules.get("stepik_grader.config"),
        getattr(sys.modules.get("stepik_grader.cli"), "config", None),
        getattr(sys.modules.get("stepik_grader.web.server"), "config", None),
    ]
    unique: dict[int, ModuleType] = {}
    for module in candidates:
        if module is not None and hasattr(module, "set_config_path"):
            unique[id(module)] = module
    return list(unique.values())


@pytest.fixture(autouse=True)
def _reset_config_overrides() -> Iterator[None]:
    """Ни один тест не оставляет процессный источник конфига следующему (issue #993).

    ``--config`` и ``--root`` фиксируют источник конфигурации и корень настроек
    на весь процесс — это их назначение в реальном запуске, но в одном процессе
    pytest такое переопределение утекает в соседние тесты: прогон ``--serve
    --root /some/dir`` уводил чтение ``.grader_settings.json`` в чужую папку у
    всех тестов после него.
    """
    yield
    for module in _loaded_config_modules():
        module.set_config_path(None)
        module.set_workspace_root(None)


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
