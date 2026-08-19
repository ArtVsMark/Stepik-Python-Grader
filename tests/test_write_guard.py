"""Tests for the `_no_writes_outside_tmp` guard in tests/conftest.py (issue #997).

Guard-the-guard: тест, который пишет мимо `tmp_path`, обязан краснеть с именем
виновника. Проверяется вложенной pytest-сессией (`pytester`) — иначе честной
проверки не выйдет: настоящее загрязнение пришлось бы устраивать на настоящем
диске. У вложенной сессии свой rootdir во временной папке, а в тесте про
домашнюю папку она же подменяется через HOME/USERPROFILE, поэтому реальные
каталоги разработчика остаются нетронутыми.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    _guarded_places,
    _is_run_artefact,
    _tolerated_names,
    _top_level_names,
)

_REPO_ROOT = Path(__file__).parent.parent

# Вложенная сессия подключает ровно две фикстуры guard'а — импортом, а не
# копией: копия начала бы жить своей жизнью и перестала бы проверять то, что
# реально стоит в наборе.
_CHILD_CONFTEST = f"""
import sys

sys.path.insert(0, {str(_REPO_ROOT)!r})

from tests.conftest import _known_filesystem_entries, _no_writes_outside_tmp  # noqa: F401
"""

# То же, но с подменённой домашней папкой: прецедент #818 (тесты удалили
# ~/.grader_history.db разработчика) проверяется без единого касания настоящей.
_CHILD_CONFTEST_FAKE_HOME = f"""
import os
import sys
from pathlib import Path

sys.path.insert(0, {str(_REPO_ROOT)!r})

_fake_home = Path(__file__).parent / "fake-home"
_fake_home.mkdir(exist_ok=True)
os.environ["HOME"] = str(_fake_home)
os.environ["USERPROFILE"] = str(_fake_home)

from tests.conftest import _known_filesystem_entries, _no_writes_outside_tmp  # noqa: F401
"""


# Файл-жертва создаётся до снимка (на импорте conftest), чтобы тест ниже мог его
# удалить: так проверяется вторая половина guard'а — исчезновение записи.
_CHILD_CONFTEST_WITH_VICTIM = f"""
import sys
from pathlib import Path

sys.path.insert(0, {str(_REPO_ROOT)!r})

(Path(__file__).parent / "precious.db").write_bytes(b"")

from tests.conftest import _known_filesystem_entries, _no_writes_outside_tmp  # noqa: F401
"""


def test_stray_file_in_project_root_is_reported(pytester: pytest.Pytester) -> None:
    """Файл, созданный мимо tmp_path, роняет тест-виновника."""
    pytester.makeconftest(_CHILD_CONFTEST)
    pytester.makepyfile(
        test_stray="""
        from pathlib import Path

        def test_writes_outside_tmp() -> None:
            Path("stray_artifact.json").write_text("{}", encoding="utf-8")
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)
    assert "stray_artifact.json" in result.stdout.str()
    assert "test_writes_outside_tmp" in result.stdout.str()


def test_stray_file_in_home_is_reported(pytester: pytest.Pytester) -> None:
    """Домашняя папка под охраной наравне с корнем проекта."""
    pytester.makeconftest(_CHILD_CONFTEST_FAKE_HOME)
    pytester.makepyfile(
        test_home="""
        from pathlib import Path

        def test_writes_into_home() -> None:
            (Path.home() / ".grader_history.db").write_bytes(b"")
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)
    assert ".grader_history.db" in result.stdout.str()


def test_deleted_file_is_reported(pytester: pytest.Pytester) -> None:
    """Исчезнувший файл — тоже след: прецедент #818 стоил данных, а не недоумения."""
    pytester.makeconftest(_CHILD_CONFTEST_WITH_VICTIM)
    pytester.makepyfile(
        test_purge="""
        from pathlib import Path

        def test_deletes_a_real_file() -> None:
            (Path(__file__).parent / "precious.db").unlink()
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)
    assert "precious.db" in result.stdout.str()
    assert "удалено" in result.stdout.str()


def test_write_into_tmp_path_is_allowed(pytester: pytest.Pytester) -> None:
    """Штатная запись в tmp_path guard не трогает."""
    pytester.makeconftest(_CHILD_CONFTEST)
    pytester.makepyfile(
        test_clean="""
        from pathlib import Path

        def test_writes_into_tmp(tmp_path: Path) -> None:
            (tmp_path / "artifact.json").write_text("{}", encoding="utf-8")
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_only_the_offender_fails(pytester: pytest.Pytester) -> None:
    """Виновник один, а не «все тесты после него».

    Найденное имя заносится в слепок: без этого один каталог-подкидыш красил бы
    красным весь остаток прогона, и виновника пришлось бы искать глазами по
    первому падению.
    """
    pytester.makeconftest(_CHILD_CONFTEST)
    pytester.makepyfile(
        test_pair="""
        from pathlib import Path

        def test_a_writes_outside_tmp() -> None:
            Path("stray_artifact.json").write_text("{}", encoding="utf-8")

        def test_b_is_innocent() -> None:
            assert True
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2, errors=1)


# ---------------------------------------------------------------------------
# Составные части guard'а
# ---------------------------------------------------------------------------


def test_run_artefacts_are_not_pollution() -> None:
    """Артефакты pytest/coverage/hypothesis пишет прогон, а не тест."""
    assert _is_run_artefact(".pytest_cache")
    assert _is_run_artefact(".hypothesis")
    assert _is_run_artefact(".coverage")
    assert _is_run_artefact(".coverage.HOST.1234.567")  # coverage parallel=true
    assert not _is_run_artefact(".grader_settings.json")
    assert not _is_run_artefact("some")


def test_foreign_tool_files_are_not_pollution() -> None:
    """Файлы соседнего инструмента разработчика — не след теста.

    Прецедент: полный прогон на машине владельца дал 14 ложных обвинений подряд,
    все — из-за `~/.claude.json.lock`, который Claude Code создаёт и удаляет
    независимо от тестов. Обвинялся каждый раз случайный тест, ничего не
    писавший; сам прогон при этом был зелёным (4620 passed). Guard ценен именно
    точностью обвинения — ложное срабатывание обесценивает его быстрее, чем
    пропуск.
    """
    assert _is_run_artefact(".claude.json")
    assert _is_run_artefact(".claude.json.lock")
    # Атомарная запись кладёт рядом временный файл со случайным хвостом —
    # именно из-за него точных имён было мало.
    assert _is_run_artefact(".claude.json.tmp.15864.92a1")
    # Наши собственные файлы остаются под наблюдением — их след теста значим.
    assert not _is_run_artefact(".grader_history.db")
    assert not _is_run_artefact(".grader_settings.json.lock")


def test_top_level_names_ignores_nested_entries(tmp_path: Path) -> None:
    """Слепок — только верхний уровень: имена, а не обход в глубину."""
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    (tmp_path / "dir" / "nested").mkdir(parents=True)
    assert _top_level_names(tmp_path) == {"visible.txt", "dir"}


def test_top_level_names_survives_unreadable_place(tmp_path: Path) -> None:
    """Недоступный каталог не роняет прогон — guard молчит, а не падает."""
    assert _top_level_names(tmp_path / "does-not-exist") == set()


def test_basetemp_inside_a_guarded_place_is_tolerated(tmp_path: Path) -> None:
    """`--basetemp` внутри охраняемого места — легальный корень для записи.

    На машине владельца системный %TEMP% недоступен из песочницы инструментов,
    и `--basetemp` в рабочем каталоге — единственный способ прогнать набор.
    """
    basetemp = tmp_path / "pytest-tmp" / "run-0"
    basetemp.mkdir(parents=True)
    assert _tolerated_names(tmp_path, [basetemp]) == {"pytest-tmp"}


def test_place_outside_basetemp_tolerates_nothing(tmp_path: Path) -> None:
    """Корень, не содержащий basetemp, не получает поблажек."""
    assert _tolerated_names(tmp_path, [tmp_path.parent / "elsewhere"]) == set()


def test_guarded_places_cover_repo_home_and_drive_root(pytestconfig: pytest.Config) -> None:
    """Под охраной корень репозитория, домашняя папка и корень диска."""
    places = _guarded_places(pytestconfig)
    assert Path(pytestconfig.rootpath).resolve() in places
    assert Path.home().resolve() in places
    assert Path(Path(pytestconfig.rootpath).anchor).resolve() in places
