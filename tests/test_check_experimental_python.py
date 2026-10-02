"""Тесты scripts/check_experimental_python.py — «экспериментальная» устаревает сама.

Пометка `experimental: true` — утверждение о ЧУЖОМ календаре: версия
предрелизная не потому, что мы так решили, а потому что её ещё не выпустили.
Она стареет без единой правки в нашем дереве, и ни один прогон не краснеет —
`continue-on-error` делает ровно то, что написано.

Замер, из которого выросла проверка (09.09.2026): флаг стоял на 3.14 с её
предрелизных времён и пережил выход — манифест раннера отдавал `3.14.7
stable=True` при `3.15.0-rc.2 stable=False`. Целый цикл релизов падения на
полноценной версии не блокировали мерж.

Проверяется здесь не «ходит в сеть», а **что считается расхождением**: вышедшая
версия под флагом — да, предрелизная под флагом — нет, вышедшая без флага —
тоже нет (так и должно быть).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"
_SCRIPT = _SCRIPTS / "check_experimental_python.py"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_check_experimental_python", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard() -> ModuleType:
    """Свежий модуль на каждый тест."""
    return _load_module()


def _cell(version: str, *, experimental: bool) -> str:
    flag = "true" if experimental else "false"
    return (
        f'          - {{os: "ubuntu-latest", python-version: "{version}", experimental: {flag}}}\n'
    )


def _manifest(*entries: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"version": version, "stable": stable} for version, stable in entries]


# --- что считается расхождением -----------------------------------------------


def test_a_released_version_under_the_flag_is_the_finding(guard: ModuleType) -> None:
    """Ровно тот случай: 3.14 вышла, а флаг с неё не сняли."""
    found = guard.mismatches(
        _cell("3.14", experimental=True),
        _manifest(("3.15.0-rc.2", False), ("3.14.7", True), ("3.13.15", True)),
    )

    assert [item.version for item in found] == ["3.14"]
    assert "3.14.7" in found[0].line(), "номер патча показывает, ДАВНО ЛИ версия вышла"


def test_a_prerelease_under_the_flag_is_correct(guard: ModuleType) -> None:
    """Предрелизная под флагом — то, ради чего флаг и существует."""
    found = guard.mismatches(
        _cell("3.15", experimental=True),
        _manifest(("3.15.0-rc.2", False), ("3.14.7", True)),
    )

    assert found == []


def test_a_released_version_without_the_flag_is_not_a_finding(guard: ModuleType) -> None:
    """Вышедшая версия обычной ячейкой — норма, а не расхождение.

    Проверка отвечает на один вопрос: не устарел ли флаг. Требовать флага у
    каждой версии значило бы запретить матрице содержать стабильные — то есть
    ровно то, ради чего матрица и заведена.
    """
    found = guard.mismatches(
        _cell("3.14", experimental=False),
        _manifest(("3.14.7", True)),
    )

    assert found == []


def test_an_unknown_version_is_not_accused(guard: ModuleType) -> None:
    """Версии нет в манифесте вовсе — судить не по чему, и это не находка."""
    found = guard.mismatches(
        _cell("3.16", experimental=True),
        _manifest(("3.14.7", True)),
    )

    assert found == []


def test_the_freshest_patch_wins(guard: ModuleType) -> None:
    """Манифест идёт от новых к старым — берётся первая запись на минорную версию."""
    stable = guard.stable_minors(_manifest(("3.14.7", True), ("3.14.6", True), ("3.14.0", True)))

    assert stable["3.14"] == "3.14.7"


def test_prereleases_never_count_as_released(guard: ModuleType) -> None:
    """`stable=False` не даёт минорной версии считаться вышедшей."""
    assert guard.stable_minors(_manifest(("3.15.0-rc.2", False))) == {}


# --- живое дерево -------------------------------------------------------------


def test_the_live_matrix_flags_exactly_one_version(guard: ModuleType) -> None:
    """В матрице ровно одна экспериментальная версия — предрелизная следующего цикла.

    Ни одной означало бы, что следующий цикл не проверяется вовсе; две и больше
    — что предыдущую забыли снять с флага, а это и есть чинимый дефект.
    """
    flagged = guard.flagged_versions(_CI.read_text(encoding="utf-8"))

    assert len(flagged) == 1, f"помечено экспериментальными: {sorted(flagged)}"


# --- три исхода ---------------------------------------------------------------


def test_an_unreachable_manifest_is_not_a_clean_result(
    guard: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Спросить не удалось» — отдельный исход (правило 039), а не «флаги совпали»."""
    assert guard.main(["--manifest", str(tmp_path / "нет.json")]) == guard.EXIT_UNKNOWN
    assert "Спросить не удалось" in capsys.readouterr().err


def test_a_stale_flag_warns_but_does_not_fail(
    guard: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Красный отказ останавливал бы работу из-за выхода чужой версии.

    Событие произошло без нас и правится сменой флага плюс правкой ruleset —
    то есть не одним изменением. Находка при этом обязана быть названа вслух:
    без адресата она не доедет ни до кого (правило 142).
    """
    ci = tmp_path / "ci.yml"
    ci.write_text(_cell("3.14", experimental=True), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest(("3.14.7", True))), encoding="utf-8")
    monkeypatch.setattr(guard, "_CI", ci)

    assert guard.main(["--manifest", str(manifest)]) == guard.EXIT_OK

    printed = capsys.readouterr().out
    assert "::warning::" in printed
    assert "3.14.7" in printed
