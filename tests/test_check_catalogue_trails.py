"""Тесты scripts/check_catalogue_trails.py — след каталога в наше дерево разрешается.

Правило 185 каталога: след записи — адрес в чужом репозитории, и правит его та
сторона, чей документ. Наша половина стояла без механизма: переименование
раздела в `CLAUDE.md` проходит здесь зелёным, а ссылка каталога обрывается
молча — узнать об этом можно было только из чужого прогона.

Проверяется здесь не «находит обрыв», а **что именно считается обрывом**:
сокращённое имя раздела законно, отсутствующее — нет; след-задача не предмет
вовсе; недоступная выгрузка — третий исход, а не «всё в порядке».
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
_SCRIPT = _SCRIPTS / "check_catalogue_trails.py"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_check_catalogue_trails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def trails() -> ModuleType:
    """Свежий модуль на каждый тест."""
    return _load_module()


def _export(*rules: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "1.5", "rules": list(rules)}


def _rule(rule_id: str, *trail: dict[str, Any]) -> dict[str, Any]:
    return {"id": rule_id, "trails": list(trail)}


def _tree(tmp_path: Path, docs: dict[str, str]) -> Path:
    """Дерево с документами: ключ — путь как он записан в следе каталога."""
    for name, text in docs.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


# --- что считается обрывом ----------------------------------------------------


def test_a_renamed_section_is_a_broken_trail(trails: ModuleType, tmp_path: Path) -> None:
    """Ровно тот случай, ради которого механизм и заведён."""
    root = _tree(tmp_path, {"CLAUDE.md": "# Свод\n\n## Очередь работ\n\nтекст\n"})
    export = _export(
        _rule("052", {"repo": trails.PROJECT, "doc": "CLAUDE.md", "section": "Очередь мержа"})
    )

    broken = trails.broken_trails(export, root=root)

    assert len(broken) == 1
    assert broken[0].rule == "052"
    assert "раздела" in broken[0].why


def test_a_moved_file_is_a_broken_trail(trails: ModuleType, tmp_path: Path) -> None:
    """Переезд в src-layout оставил в каталоге адреса прежнего дерева."""
    root = _tree(tmp_path, {"README.md": "# Пусто\n"})
    export = _export(_rule("110", {"repo": trails.PROJECT, "doc": "core/tracer.py"}))

    broken = trails.broken_trails(export, root=root)

    assert len(broken) == 1
    assert "адресу" in broken[0].why


def test_a_shortened_section_still_resolves(trails: ModuleType, tmp_path: Path) -> None:
    """Каталог законно сокращает длинный заголовок — это не поломка.

    Требуй механизм дословного совпадения, и он объявлял бы обрывом корректный
    след: правило про адрес, а не про буквальную копию строки.
    """
    root = _tree(
        tmp_path,
        {"CLAUDE.md": "# Свод\n\n## Комплексный issue ведёт чек-лист находок\n\nтекст\n"},
    )
    export = _export(
        _rule(
            "026",
            {
                "repo": trails.PROJECT,
                "doc": "CLAUDE.md",
                "section": "Комплексный issue ведёт чек-лист",
            },
        )
    )

    assert trails.broken_trails(export, root=root) == []


def test_a_trail_without_a_section_needs_only_the_file(trails: ModuleType, tmp_path: Path) -> None:
    """Форма «документ без раздела» проверяется существованием документа."""
    root = _tree(tmp_path, {"docs/agent/multiagent.md": "# Волны\n"})
    export = _export(_rule("015", {"repo": trails.PROJECT, "doc": "docs/agent/multiagent.md"}))

    assert trails.broken_trails(export, root=root) == []


# --- что предметом не является ------------------------------------------------


def test_an_issue_trail_is_not_our_subject(trails: ModuleType, tmp_path: Path) -> None:
    """Задача — состояние трекера, её переименованием раздела не сломать."""
    export = _export(_rule("001", {"repo": trails.PROJECT, "issue": "1265"}))

    assert trails.our_trails(export) == []
    assert trails.broken_trails(export, root=tmp_path) == []


def test_a_neighbours_tree_is_not_ours(trails: ModuleType, tmp_path: Path) -> None:
    """Чужой документ мы не правим и судить о нём не вправе."""
    export = _export(_rule("015", {"repo": "ArtVsMark/Glossary-Python", "doc": "docs/nowhere.md"}))

    assert trails.broken_trails(export, root=tmp_path) == []


# --- три исхода ---------------------------------------------------------------


def test_a_missing_catalogue_is_not_a_clean_result(
    trails: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Сверить не удалось» — отдельный исход (правило 039), а не «следы в порядке»."""
    assert trails.main(["--catalogue", str(tmp_path / "нет-такого")]) == trails.EXIT_UNKNOWN
    assert "Сверить не удалось" in capsys.readouterr().err


def test_broken_trails_warn_but_do_not_fail(
    trails: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Красный отказ здесь останавливал бы нашу работу из-за чужого файла.

    Предмет живёт в репозитории каталога: чинится он предложением туда, а не
    правкой у нас. Находка при этом обязана быть названа вслух — иначе у неё не
    будет адресата (правило 142).
    """
    catalogue = tmp_path / "catalogue"
    (catalogue / "export").mkdir(parents=True)
    (catalogue / "export" / "rules.json").write_text(
        json.dumps(
            _export(_rule("052", {"repo": trails.PROJECT, "doc": "нет.md"})),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(trails, "_ROOT", tmp_path / "tree")
    (tmp_path / "tree").mkdir()

    assert trails.main(["--catalogue", str(catalogue)]) == trails.EXIT_OK

    printed = capsys.readouterr().out
    assert "::warning::" in printed
    assert "нет.md" in printed
