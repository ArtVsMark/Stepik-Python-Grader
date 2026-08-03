"""Tests for scripts/extract_release_notes.py — тело релиза из CHANGELOG (issue #834).

Релиз собирался только автогенерацией `generate_release_notes`: список заголовков
PR вида «chore(ci): update badges [skip ci] by @ArtVsMark». Подробный русский
CHANGELOG, который ведётся в каждом PR, в релиз не попадал — страница, которую
цитируют и репостят, читалась как внутренний git log.

Скрипт загружается по пути (как `tests/test_check_docs_guardrails.py`): в
`scripts/` нет `__init__.py`, и обычный импорт по имени пакета там не работает.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "extract_release_notes.py"

_SAMPLE = """# Changelog

## [Unreleased]

### Added
- ещё не вышло

## [1.10.0] - 2026-07-30

### Added
- первое изменение (#1)

### Fixed
- второе изменение (#2)

## [1.9.0] - 2026-07-01

### Added
- старое изменение (#3)
"""


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_extract_release_notes", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extracts_requested_version_body() -> None:
    """Тело секции — без своего заголовка и без соседних версий."""
    notes = _load().extract_notes(_SAMPLE, "1.10.0")
    assert "первое изменение (#1)" in notes
    assert "второе изменение (#2)" in notes
    assert "## [1.10.0]" not in notes
    assert "старое изменение" not in notes


def test_unreleased_section_never_leaks_into_a_release() -> None:
    """Граница — любой `## `, а не следующая ВЕРСИЯ.

    Иначе `[Unreleased]`, который в этом файле всегда стоит выше версий, утащил
    бы в тело релиза незавершённый раздел следующей версии.
    """
    notes = _load().extract_notes(_SAMPLE, "1.9.0")
    assert "ещё не вышло" not in notes
    assert "старое изменение (#3)" in notes


@pytest.mark.parametrize(
    "version", ["1.10.0", "v1.10.0", " v1.10.0 "], ids=["bare", "tag", "spaced"]
)
def test_tag_prefix_and_spaces_are_accepted(version: str) -> None:
    """В workflow приезжает `GITHUB_REF_NAME` — то есть `vX.Y.0`, а не `X.Y.0`."""
    assert "первое изменение (#1)" in _load().extract_notes(_SAMPLE, version)


def test_latest_picks_the_topmost_version_not_unreleased() -> None:
    assert _load().latest_version(_SAMPLE) == "1.10.0"


def test_missing_section_is_an_error_not_empty_notes() -> None:
    """Забытая запись под тег обязана ронять релиз, а не давать пустое тело.

    Пустой `body_path` выглядел бы как «в релизе ничего не поменялось» — хуже,
    чем автосписок PR, который мы и заменяем.
    """
    module = _load()
    with pytest.raises(module.NotesNotFoundError, match=r"2\.0\.0"):
        module.extract_notes(_SAMPLE, "2.0.0")


def test_changelog_without_versions_is_an_error() -> None:
    module = _load()
    with pytest.raises(module.NotesNotFoundError):
        module.latest_version("# Changelog\n\n## [Unreleased]\n\n- пока ничего\n")


def test_cli_writes_file_and_reports(tmp_path: Path, capsys) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_SAMPLE, encoding="utf-8")
    out = tmp_path / "release-notes.md"

    code = _load().main(["v1.10.0", "--out", str(out), "--changelog", str(changelog)])

    assert code == 0
    assert "первое изменение (#1)" in out.read_text(encoding="utf-8")
    assert str(out) in capsys.readouterr().out


def test_cli_exits_nonzero_on_missing_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_SAMPLE, encoding="utf-8")
    assert _load().main(["9.9.9", "--changelog", str(changelog)]) == 1


def test_cli_requires_a_version_or_latest(tmp_path: Path) -> None:
    """Без аргументов — ошибка argparse, а не молчаливый выбор чего-нибудь."""
    with pytest.raises(SystemExit):
        _load().main([])


def test_notes_print_on_a_single_byte_console() -> None:
    """Заметки печатаются под cp1252, а не роняют скрипт (регресс из #834).

    Ровно это уронило три Windows-job'а на main: `print(notes)` упирался в
    кодовую страницу консоли и падал `UnicodeEncodeError` — скрипт ломался на
    содержимом, ради которого написан.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--latest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert "UnicodeEncodeError" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


def test_real_changelog_yields_notes_for_its_latest_version() -> None:
    """Гейт на реальном файле: скрипт должен работать на том, что в репозитории."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--latest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "тело последнего релиза пустое"
    assert "## [" not in result.stdout, "в тело утёк заголовок соседней секции"
