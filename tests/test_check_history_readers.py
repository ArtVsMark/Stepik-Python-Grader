"""Тесты scripts/check_history_readers.py — у таблицы истории есть читатель (issue #1587).

Гейт проверяется с обеих сторон: на живом дереве он чист (задел объявлен), а
на подделанном пакете отвергает таблицу без читателя, необъявленную таблицу и
устаревший задел.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "check_history_readers.py"

_SCHEMA = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER);
CREATE TABLE IF NOT EXISTS hits (id INTEGER);
"""


def read_runs(db): ...


def read_hits(db): ...
'''


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_history_readers", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gate() -> ModuleType:
    """Свежий модуль гейта."""
    return _load()


def _package(tmp_path: Path, *, consumer: str) -> tuple[Path, Path]:
    """Пакет с историей из двух таблиц и одним модулем-потребителем."""
    package = tmp_path / "pkg"
    (package / "core").mkdir(parents=True)
    history = package / "core" / "history.py"
    history.write_text(_SCHEMA, encoding="utf-8")
    (package / "ui.py").write_text(consumer, encoding="utf-8")
    return package, history


_READERS = {"runs": ("read_runs",), "hits": ("read_hits",)}


def test_live_tree_is_clean(gate: ModuleType) -> None:
    """На настоящем дереве находок нет: таблицы без читателя объявлены заделом."""
    assert gate.findings() == []


def test_every_reserve_points_to_an_issue(gate: ModuleType) -> None:
    """Задел без номера задачи — это не объявление, а молчание."""
    assert all(number > 0 for number in gate.RESERVED.values())


def test_table_read_outside_history_passes(gate: ModuleType, tmp_path: Path) -> None:
    """Обе таблицы читаются продуктом — чисто."""
    package, history = _package(tmp_path, consumer="read_runs()\nread_hits()\n")
    assert gate.findings(package, history, readers=_READERS, reserved={}) == []


def test_table_without_reader_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """Таблица пишется, а читателя вне history.py нет — находка."""
    package, history = _package(tmp_path, consumer="read_runs()\n")
    [found] = gate.findings(package, history, readers=_READERS, reserved={})
    assert "hits" in found and "не читается" in found


def test_reserve_covers_missing_reader(gate: ModuleType, tmp_path: Path) -> None:
    """Объявленный задел снимает находку."""
    package, history = _package(tmp_path, consumer="read_runs()\n")
    assert gate.findings(package, history, readers=_READERS, reserved={"hits": 1}) == []


def test_stale_reserve_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """Читатель появился, а задел висит — находка: объявление врёт."""
    package, history = _package(tmp_path, consumer="read_runs()\nread_hits()\n")
    [found] = gate.findings(package, history, readers=_READERS, reserved={"hits": 1})
    assert "снимите задел" in found


def test_undeclared_table_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """Новая таблица без строки в TABLE_READERS — находка."""
    package, history = _package(tmp_path, consumer="read_runs()\n")
    [found] = gate.findings(package, history, readers={"runs": ("read_runs",)}, reserved={})
    assert "hits" in found and "не объявлен" in found


def test_missing_reader_function_is_rejected(gate: ModuleType, tmp_path: Path) -> None:
    """Объявлен читатель, которого в history.py нет, — находка."""
    package, history = _package(tmp_path, consumer="read_runs()\nread_hits()\n")
    readers = {"runs": ("read_runs",), "hits": ("read_hits", "read_gone")}
    found = gate.findings(package, history, readers=readers, reserved={})
    assert any("read_gone" in line for line in found)


def test_strict_turns_findings_into_exit_one(
    gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--strict``: находка — код 1; без него — предупреждение и 0."""
    package, history = _package(tmp_path, consumer="read_runs()\n")
    monkeypatch.setattr(gate, "TABLE_READERS", _READERS)
    monkeypatch.setattr(gate, "RESERVED", {})
    argv = ["--package", str(package), "--history", str(history)]
    assert gate.main(argv) == 0
    assert gate.main([*argv, "--strict"]) == 1


def test_missing_history_is_a_broken_run(gate: ModuleType, tmp_path: Path) -> None:
    """Нет history.py — проверка не отработала, код 2."""
    assert gate.main(["--history", str(tmp_path / "нет.py")]) == 2
